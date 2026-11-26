"""SQLite Journal adapter (WP-03): the JournalPort and JournalVerifier implementation.

Design references: A 2, A 5.3 (A11), A.I1/I3/I18, E 2.1-2.3, E 8, E 15.1,
ADR-0002. The full specification is docs/design/phase-1/WP-03_JOURNAL.md.

Summary:

- One writer: an OS lock (``RuntimeLock``) plus an ``owner_epoch`` in the
  ``runtime_owner`` row, incremented at open with ``BEGIN IMMEDIATE`` and
  checked inside every write transaction.
- Every append is one ``BEGIN IMMEDIATE`` transaction: owner check, tenant and
  run positions, ``prev_hash``, event hash, event row, head rows, commit. Any
  failure rolls back the whole transaction, so no partial row and no gap.
- A duplicate ``event_id`` is detected by the UNIQUE constraint, rolled back
  and reported as ``DuplicateIdError``. Other database errors are never
  reported as duplicates. A failed COMMIT is reported as
  ``JournalWriteOutcomeUnknownError``.
- After a fatal error the instance stops (``JournalClosedError`` afterwards).
- Reads re-verify each returned row (body, canonical form, columns, hash,
  chain, continuity) and never return unverified data as an Event.
- The write transaction is a single unit so a later work package (WP-09) can
  update the cost ledger in the same SQLite transaction.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from agent_core.adapters.runtime_lock import RuntimeLock
from agent_core.ports.clock import Clock
from agent_core.ports.journal import JournalHead
from agent_core.ports.randomness import RandomSource
from agent_core.types.errors import (
    DuplicateIdError,
    InvalidIdError,
    JournalArgumentError,
    JournalClosedError,
    JournalCorruptionError,
    JournalError,
    JournalSchemaError,
    JournalStorageError,
    JournalWriteOutcomeUnknownError,
    OwnerEpochMismatchError,
    RandomSourceError,
    RecordValidationError,
    RunTenantMismatchError,
    RuntimeLockLostError,
    SequenceExhaustedError,
    StaleEventEpochError,
)
from agent_core.types.event_hash import EVENT_HASH_VERSION, event_hash
from agent_core.types.ids import RunId, TenantId
from agent_core.types.records import Event, EventDraft
from agent_core.types.serialization import dump_json, load_json
from agent_core.types.values import MAX_INT64

JOURNAL_SCHEMA_VERSION: Final = 1
# "ACJ1" in ASCII: marks the file as an Agent Core journal (PRAGMA application_id).
APPLICATION_ID: Final = 0x41434A31
# Only P1 writes (OS lock), so waits come only from short external readers.
BUSY_TIMEOUT_MS: Final = 5_000
MAX_READ_LIMIT: Final = 1_000
INSTANCE_ID_BYTES: Final = 16
MIN_SQLITE_VERSION: Final = (3, 37, 0)  # STRICT tables

SCHEMA_DDL: Final = (
    """CREATE TABLE runtime_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    instance_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0)
) STRICT""",
    """CREATE TABLE tenant_head (
    tenant_id TEXT PRIMARY KEY,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    head_hash TEXT NOT NULL
) STRICT""",
    """CREATE TABLE run_head (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER NOT NULL CHECK (run_seq >= 1)
) STRICT""",
    """CREATE TABLE events (
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    event_id TEXT NOT NULL,
    run_id TEXT REFERENCES run_head (run_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER CHECK (run_seq >= 1),
    hash_version INTEGER NOT NULL,
    event_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, tenant_seq),
    CONSTRAINT events_event_id_unique UNIQUE (event_id),
    CONSTRAINT events_run_position_unique UNIQUE (run_id, run_seq),
    CHECK ((run_id IS NULL) = (run_seq IS NULL))
) STRICT""",
)

_SCHEMA_OBJECTS_SQL: Final = (
    "SELECT type, name, tbl_name, sql FROM sqlite_master "
    "WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY type, name"
)
_DUPLICATE_EVENT_ID_MESSAGE: Final = "UNIQUE constraint failed: events.event_id"
_EVENT_COLUMNS: Final = (
    "tenant_id, tenant_seq, event_id, run_id, run_seq, hash_version, event_hash, body"
)


def _expected_schema_objects() -> list[tuple[Any, ...]]:
    memory = sqlite3.connect(":memory:")
    try:
        for ddl in SCHEMA_DDL:
            memory.execute(ddl)
        return memory.execute(_SCHEMA_OBJECTS_SQL).fetchall()
    finally:
        memory.close()


def _check_position_arguments(after: object, limit: object) -> tuple[int, int]:
    if type(after) is not int or not 0 <= after <= MAX_INT64:
        raise JournalArgumentError("start position must be an int in [0, 2**63-1]")
    if type(limit) is not int or not 1 <= limit <= MAX_READ_LIMIT:
        raise JournalArgumentError(f"limit must be an int in [1, {MAX_READ_LIMIT}]")
    return after, limit


def _typed[T: str](value: object, id_type: type[T]) -> T:
    if type(value) is not id_type:
        raise JournalArgumentError(f"expected a {id_type.__name__}")
    try:
        return id_type(value)
    except InvalidIdError as exc:
        raise JournalArgumentError(f"invalid {id_type.__name__}") from exc


class SqliteJournal:
    """Journal on one SQLite file. Create with ``SqliteJournal.open``."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        lock: RuntimeLock,
        owner_epoch: int,
        instance_id: str,
    ) -> None:
        self._conn: sqlite3.Connection | None = conn
        self._lock = lock
        self._owner_epoch = owner_epoch
        self._instance_id = instance_id

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def open(cls, path: Path, *, clock: Clock, random: RandomSource) -> SqliteJournal:
        if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
            raise JournalSchemaError("SQLite 3.37 or newer is required")
        path = Path(path)
        _check_database_path(path)
        lock = RuntimeLock.acquire(path.with_name(path.name + ".lock"))
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=True
            )
            _configure(conn)
            instance_id = _new_instance_id(random)
            acquired_at = clock.now_ms()
            if type(acquired_at) is not int or not 0 <= acquired_at <= MAX_INT64:
                raise JournalStorageError("clock returned an invalid time")
            epoch = _initialize_and_claim(conn, instance_id, acquired_at)
        except sqlite3.Error as exc:
            _close_quietly(conn)
            lock.release()
            raise JournalStorageError(f"cannot open journal: {type(exc).__name__}") from exc
        except BaseException:
            _close_quietly(conn)
            lock.release()
            raise
        return cls(conn=conn, lock=lock, owner_epoch=epoch, instance_id=instance_id)

    @property
    def owner_epoch(self) -> int:
        return self._owner_epoch

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def closed(self) -> bool:
        return self._conn is None

    def close(self) -> None:
        conn, self._conn = self._conn, None
        try:
            if conn is not None:
                conn.close()
        finally:
            self._lock.release()

    def __enter__(self) -> SqliteJournal:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connection_settings(self) -> dict[str, Any]:
        conn = self._require_open()
        return {
            name: conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in (
                "journal_mode",
                "synchronous",
                "foreign_keys",
                "busy_timeout",
                "trusted_schema",
            )
        }

    # ------------------------------------------------------------ append

    def append(self, draft: EventDraft) -> Event:
        conn = self._require_open()
        if type(draft) is not EventDraft:
            raise JournalArgumentError("append expects an EventDraft")
        if draft.owner_epoch != self._owner_epoch:
            raise StaleEventEpochError("draft owner_epoch is not the current owner epoch")
        try:
            self._lock.verify()
        except RuntimeLockLostError:
            self._stop()
            raise
        with self._write_transaction(conn) as cur:
            event, digest = self._position(cur, draft)
            self._insert_event(cur, event, digest)
            self._update_heads(cur, event, digest)
        return event

    @contextmanager
    def _write_transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
        """One ``BEGIN IMMEDIATE`` transaction with the owner check. All or nothing."""
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._stop()
            raise JournalStorageError(f"cannot begin write: {type(exc).__name__}") from exc
        cur = conn.cursor()
        try:
            self._check_owner(cur)
            yield cur
        except (DuplicateIdError, RunTenantMismatchError):
            self._rollback_or_unknown(conn)
            raise
        except JournalError:
            self._rollback_or_unknown(conn)
            self._stop()
            raise
        except sqlite3.Error as exc:
            self._rollback_or_unknown(conn)
            self._stop()
            raise JournalStorageError(f"write failed: {type(exc).__name__}") from exc
        except BaseException:
            # Not a journal error: roll back, then let the original error propagate.
            self._rollback_or_unknown(conn)
            raise
        try:
            self._commit()
        except sqlite3.Error as exc:
            self._rollback_quietly(conn)
            self._stop()
            raise JournalWriteOutcomeUnknownError("commit failed; outcome unknown") from exc

    def _check_owner(self, cur: sqlite3.Cursor) -> None:
        row = cur.execute(
            "SELECT epoch, instance_id FROM runtime_owner WHERE singleton = 1"
        ).fetchone()
        if row is None or row[0] != self._owner_epoch or row[1] != self._instance_id:
            raise OwnerEpochMismatchError("runtime_owner names another owner")

    def _position(self, cur: sqlite3.Cursor, draft: EventDraft) -> tuple[Event, str]:
        head = cur.execute(
            "SELECT tenant_seq, head_hash FROM tenant_head WHERE tenant_id = ?",
            (str(draft.tenant_id),),
        ).fetchone()
        if head is not None and head[0] >= MAX_INT64:
            raise SequenceExhaustedError("tenant_seq would exceed 2**63-1")
        tenant_seq = 1 if head is None else head[0] + 1
        prev_hash = None if head is None else head[1]
        run_seq = None
        if draft.run_id is not None:
            run = cur.execute(
                "SELECT tenant_id, run_seq FROM run_head WHERE run_id = ?", (str(draft.run_id),)
            ).fetchone()
            if run is not None and run[0] != str(draft.tenant_id):
                raise RunTenantMismatchError("run_id belongs to another tenant")
            if run is not None and run[1] >= MAX_INT64:
                raise SequenceExhaustedError("run_seq would exceed 2**63-1")
            run_seq = 1 if run is None else run[1] + 1
        try:
            event = Event.from_draft(
                draft, tenant_seq=tenant_seq, run_seq=run_seq, prev_hash=prev_hash
            )
        except ValueError as exc:
            raise JournalCorruptionError("stored head is not a valid position") from exc
        return event, event_hash(event)

    def _insert_event(self, cur: sqlite3.Cursor, event: Event, digest: str) -> None:
        try:
            cur.execute(
                f"INSERT INTO events ({_EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
                (
                    str(event.tenant_id),
                    event.tenant_seq,
                    str(event.event_id),
                    None if event.run_id is None else str(event.run_id),
                    event.run_seq,
                    EVENT_HASH_VERSION,
                    digest,
                    dump_json(event).decode("utf-8"),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if (
                getattr(exc, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
                and str(exc) == _DUPLICATE_EVENT_ID_MESSAGE
            ):
                raise DuplicateIdError(event.event_id) from exc
            raise

    def _update_heads(self, cur: sqlite3.Cursor, event: Event, digest: str) -> None:
        cur.execute(
            "INSERT INTO tenant_head (tenant_id, tenant_seq, head_hash) VALUES (?, ?, ?) "
            "ON CONFLICT (tenant_id) DO UPDATE SET tenant_seq = excluded.tenant_seq, "
            "head_hash = excluded.head_hash",
            (str(event.tenant_id), event.tenant_seq, digest),
        )
        if event.run_id is not None:
            cur.execute(
                "INSERT INTO run_head (run_id, tenant_id, run_seq) VALUES (?, ?, ?) "
                "ON CONFLICT (run_id) DO UPDATE SET run_seq = excluded.run_seq",
                (str(event.run_id), str(event.tenant_id), event.run_seq),
            )

    def _commit(self) -> None:
        conn = self._require_open()
        conn.execute("COMMIT")

    # ------------------------------------------------------------ reads

    def read_tenant(
        self, tenant_id: TenantId, *, after_tenant_seq: int, limit: int
    ) -> tuple[Event, ...]:
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        after, limit = _check_position_arguments(after_tenant_seq, limit)
        with self._read_transaction(conn) as cur:
            first = max(after, 1)
            rows = cur.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE tenant_id = ? AND tenant_seq >= ? "  # noqa: S608
                "ORDER BY tenant_seq ASC LIMIT ?",
                (str(tenant), first, limit + (1 if after >= 1 else 0)),
            ).fetchall()
            # When after >= 1 the row at ``after`` is read too, so the first
            # returned event's prev_hash is checked against a verified row.
            events = self._verify_tenant_rows(rows, expected_first=first)
            if after >= 1:
                if not events:
                    head = self._head_row(cur, tenant)
                    if head is not None and head[0] >= after:
                        raise JournalCorruptionError("missing event", after)
                    return ()
                events = events[1:]
            if len(events) < limit:
                self._check_tail(cur, tenant, events[-1][0].tenant_seq if events else after)
            return tuple(e for e, _ in events)

    def read_run(self, run_id: RunId, *, after_run_seq: int, limit: int) -> tuple[Event, ...]:
        conn = self._require_open()
        run = _typed(run_id, RunId)
        after, limit = _check_position_arguments(after_run_seq, limit)
        with self._read_transaction(conn) as cur:
            head = cur.execute(
                "SELECT tenant_id, run_seq FROM run_head WHERE run_id = ?", (str(run),)
            ).fetchone()
            rows = cur.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE run_id = ? AND run_seq > ? "  # noqa: S608
                "ORDER BY run_seq ASC LIMIT ?",
                (str(run), after, limit),
            ).fetchall()
            events: list[Event] = []
            expected = after + 1
            for row in rows:
                event, _ = _load_row(row)
                if event.run_seq != expected:
                    raise JournalCorruptionError("run_seq gap or reorder", event.tenant_seq)
                if head is None or str(event.tenant_id) != head[0]:
                    raise JournalCorruptionError(
                        "run is not bound to this tenant", event.tenant_seq
                    )
                events.append(event)
                expected += 1
            head_seq = 0 if head is None else int(head[1])
            if len(events) < limit:
                if events and events[-1].run_seq != head_seq:
                    raise JournalCorruptionError("run head does not match the last event")
                if not events and head_seq > after:
                    raise JournalCorruptionError("run events are missing")
            return tuple(events)

    def head(self, tenant_id: TenantId) -> JournalHead | None:
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        with self._read_transaction(conn) as cur:
            return self._verified_head(cur, tenant)

    def verify_tenant(self, tenant_id: TenantId) -> JournalHead | None:
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        with self._read_transaction(conn) as cur:
            check = cur.execute("PRAGMA quick_check").fetchall()
            if check != [("ok",)]:
                raise JournalCorruptionError("sqlite quick_check failed")
            rows = cur.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE tenant_id = ? "  # noqa: S608
                "ORDER BY tenant_seq ASC",
                (str(tenant),),
            ).fetchall()
            events = self._verify_tenant_rows(rows, expected_first=1)
            run_seqs: dict[str, int] = {}
            for event, _ in events:
                if event.run_id is not None:
                    key = str(event.run_id)
                    if event.run_seq != run_seqs.get(key, 0) + 1:
                        raise JournalCorruptionError("run_seq gap or reorder", event.tenant_seq)
                    run_seqs[key] = event.run_seq
            run_heads = dict(
                cur.execute(
                    "SELECT run_id, run_seq FROM run_head WHERE tenant_id = ?", (str(tenant),)
                ).fetchall()
            )
            if run_heads != run_seqs:
                raise JournalCorruptionError("run heads do not match the events")
            foreign = cur.execute(
                "SELECT count(*) FROM events e JOIN run_head r ON e.run_id = r.run_id "
                "WHERE r.tenant_id = ? AND e.tenant_id != ?",
                (str(tenant), str(tenant)),
            ).fetchone()[0]
            if foreign:
                raise JournalCorruptionError("a run of this tenant has events in another tenant")
            head = self._head_row(cur, tenant)
            if not events:
                if head is not None:
                    raise JournalCorruptionError("head exists without events")
                return None
            last_event, last_hash = events[-1]
            if head is None or head[0] != last_event.tenant_seq or head[1] != last_hash:
                raise JournalCorruptionError("tenant head does not match the last event")
            return JournalHead(tenant_id=tenant, tenant_seq=head[0], head_hash=head[1])

    def _verify_tenant_rows(
        self, rows: list[tuple[Any, ...]], *, expected_first: int
    ) -> list[tuple[Event, str]]:
        """Verify rows that must be consecutive from ``expected_first`` and chained."""
        verified: list[tuple[Event, str]] = []
        expected = expected_first
        prev_hash: str | None = None
        for row in rows:
            event, digest = _load_row(row)
            if event.tenant_seq != expected:
                raise JournalCorruptionError("tenant_seq gap or reorder", expected)
            if verified and event.prev_hash != prev_hash:
                raise JournalCorruptionError("hash chain broken", event.tenant_seq)
            verified.append((event, digest))
            prev_hash = digest
            expected += 1
        return verified

    def _head_row(self, cur: sqlite3.Cursor, tenant: TenantId) -> tuple[int, str] | None:
        row = cur.execute(
            "SELECT tenant_seq, head_hash FROM tenant_head WHERE tenant_id = ?", (str(tenant),)
        ).fetchone()
        return None if row is None else (int(row[0]), str(row[1]))

    def _check_tail(self, cur: sqlite3.Cursor, tenant: TenantId, last_seq: int) -> None:
        head = self._head_row(cur, tenant)
        head_seq = 0 if head is None else head[0]
        if head_seq != last_seq:
            raise JournalCorruptionError("tenant head does not match the stored events", last_seq)

    def _verified_head(self, cur: sqlite3.Cursor, tenant: TenantId) -> JournalHead | None:
        head = self._head_row(cur, tenant)
        last = cur.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE tenant_id = ? "  # noqa: S608
            "ORDER BY tenant_seq DESC LIMIT 2",
            (str(tenant),),
        ).fetchall()
        if head is None:
            if last:
                raise JournalCorruptionError("events exist without a head")
            return None
        if not last:
            raise JournalCorruptionError("head exists without events")
        event, digest = _load_row(last[0])
        if event.tenant_seq != head[0] or digest != head[1]:
            raise JournalCorruptionError("tenant head does not match the last event", head[0])
        if len(last) == 2:
            previous, previous_digest = _load_row(last[1])
            if previous.tenant_seq != event.tenant_seq - 1 or event.prev_hash != previous_digest:
                raise JournalCorruptionError("hash chain broken", event.tenant_seq)
        elif event.tenant_seq != 1:
            raise JournalCorruptionError("missing event", event.tenant_seq - 1)
        return JournalHead(tenant_id=tenant, tenant_seq=head[0], head_hash=head[1])

    @contextmanager
    def _read_transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
        """A read snapshot (``BEGIN DEFERRED``). Verification errors propagate."""
        try:
            conn.execute("BEGIN DEFERRED")
        except sqlite3.Error as exc:
            raise JournalStorageError(f"cannot begin read: {type(exc).__name__}") from exc
        try:
            yield conn.cursor()
        except sqlite3.Error as exc:
            self._rollback_quietly(conn)
            raise JournalStorageError(f"read failed: {type(exc).__name__}") from exc
        except BaseException:
            self._rollback_quietly(conn)
            raise
        try:
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise JournalStorageError(f"cannot end read: {type(exc).__name__}") from exc

    # ------------------------------------------------------------ helpers

    def _require_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise JournalClosedError("journal is closed")
        return self._conn

    def _stop(self) -> None:
        """Stop this instance after a fatal error: no further reads or writes."""
        self.close()

    def _rollback_or_unknown(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error as exc:
            self._stop()
            raise JournalWriteOutcomeUnknownError("rollback failed; outcome unknown") from exc

    @staticmethod
    def _rollback_quietly(conn: sqlite3.Connection) -> None:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                # The caller already reports a fatal error; the connection is closed next.
                return


def _check_database_path(path: Path) -> None:
    if not path.parent.is_dir():
        raise JournalStorageError("journal directory does not exist")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise JournalStorageError(f"cannot check journal path: {exc.strerror}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise JournalStorageError("journal path must be a regular file, not a link")


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA trusted_schema = OFF")
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if mode != "wal":
        raise JournalStorageError("WAL mode could not be enabled")
    conn.execute("PRAGMA synchronous = FULL")


def _new_instance_id(random: RandomSource) -> str:
    try:
        data = random.token_bytes(INSTANCE_ID_BYTES)
    except Exception as exc:
        raise RandomSourceError("random source failed") from exc
    if type(data) is not bytes or len(data) != INSTANCE_ID_BYTES:
        raise RandomSourceError("random source returned malformed output")
    return data.hex()


def _initialize_and_claim(conn: sqlite3.Connection, instance_id: str, acquired_at: int) -> int:
    """Create or check the schema, then take ownership with a new epoch (one transaction)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = conn.execute(_SCHEMA_OBJECTS_SQL).fetchall()
        if application_id == 0 and version == 0 and not objects:
            for ddl in SCHEMA_DDL:
                conn.execute(ddl)
            conn.execute(
                "INSERT INTO runtime_owner (singleton, epoch, instance_id, acquired_at_ms) "
                "VALUES (1, 0, '', 0)"
            )
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {JOURNAL_SCHEMA_VERSION}")
        elif application_id != APPLICATION_ID:
            raise JournalSchemaError("file is not an Agent Core journal")
        elif version != JOURNAL_SCHEMA_VERSION:
            raise JournalSchemaError(f"unsupported journal schema version {version}")
        elif objects != _expected_schema_objects():
            raise JournalSchemaError("journal schema objects do not match version 1")
        row = conn.execute("SELECT epoch FROM runtime_owner WHERE singleton = 1").fetchone()
        if row is None:
            raise JournalSchemaError("runtime_owner row is missing")
        if row[0] >= MAX_INT64:
            raise SequenceExhaustedError("owner epoch would exceed 2**63-1")
        epoch = int(row[0]) + 1
        conn.execute(
            "UPDATE runtime_owner SET epoch = ?, instance_id = ?, acquired_at_ms = ? "
            "WHERE singleton = 1",
            (epoch, instance_id, acquired_at),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return epoch


def _load_row(row: tuple[Any, ...]) -> tuple[Event, str]:
    """Turn a stored row into a verified Event, or raise ``JournalCorruptionError``."""
    tenant_id, tenant_seq, event_id, run_id, run_seq, hash_version, digest, body = row
    seq_hint = tenant_seq if type(tenant_seq) is int else None
    if hash_version != EVENT_HASH_VERSION:
        raise JournalCorruptionError("unsupported hash version", seq_hint)
    if type(body) is not str:
        raise JournalCorruptionError("body is not text", seq_hint)
    try:
        event = load_json(Event, body)
    except RecordValidationError as exc:
        raise JournalCorruptionError("stored body is not a valid event", seq_hint) from exc
    if dump_json(event) != body.encode("utf-8"):
        raise JournalCorruptionError("stored body is not canonical", seq_hint)
    columns = (tenant_id, tenant_seq, event_id, run_id, run_seq)
    fields = (
        str(event.tenant_id),
        event.tenant_seq,
        str(event.event_id),
        None if event.run_id is None else str(event.run_id),
        event.run_seq,
    )
    if columns != fields:
        raise JournalCorruptionError("columns do not match the body", seq_hint)
    if event_hash(event) != digest:
        raise JournalCorruptionError("event hash mismatch", seq_hint)
    return event, digest


def _close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        conn.close()
