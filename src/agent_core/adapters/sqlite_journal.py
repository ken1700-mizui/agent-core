"""SQLite Journal adapter (WP-03): the JournalPort and JournalVerifier implementation.

Design references: A 2, A 5.3 (A11), A.I1/I3/I18, E 2.1-2.3, E 8, E 15.1,
ADR-0002. The full specification is docs/design/phase-1/WP-03_JOURNAL.md.

Summary:

- One writer: an OS lock (``RuntimeLock``) plus an ``owner_epoch`` in the
  ``runtime_owner`` row. At open, inside one ``BEGIN IMMEDIATE`` transaction,
  the schema and the whole journal are verified and only then is the epoch
  incremented. A damaged journal is refused unchanged (no repair).
- Every append is one ``BEGIN IMMEDIATE`` transaction: owner, lock and file
  checks, head-versus-tail check, positions, ``prev_hash``, event hash, event
  row, head rows, a final owner/lock/file check, commit. Any failure rolls the
  whole transaction back: no partial row, no gap.
- A duplicate ``event_id`` is detected by the UNIQUE constraint, rolled back
  and reported as ``DuplicateIdError``. Other database errors are never
  reported as duplicates. A failed COMMIT is ``JournalWriteOutcomeUnknownError``.
- After any fatal error (read or write) the instance stops; afterwards every
  call raises ``JournalClosedError``. Non-fatal argument errors do not stop it.
- Reads verify every returned row plus the links on both sides of the page
  (the row before it and the row after it, or the head). Rows outside the page
  are covered by open-time verification and by ``verify_tenant``.
- The write transaction is a single unit: ledger operations (WP-09) run inside
  it, after the head check and before the event row, and must pair with the
  event's runtime transitions (``sqlite_ledger.check_pairing``). Every append
  checks the pairing, so a transition that needs a ledger operation cannot be
  journaled without it, and vice versa. Ledger refusals roll everything back.
- Schema version 4 holds the budget ledger (WP-09B), the authority tables
  (WP-09C) and, from the WP-09 limited fixes, the runtime mirror, price tables,
  counter usage and split cost columns. Older files (versions 1-3) are refused,
  never migrated or deleted (Phase 1 files are synthetic test data).
- The database directory must be a trusted local directory (A 5.3).
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from agent_core.adapters import sqlite_ledger
from agent_core.adapters.runtime_lock import RuntimeLock
from agent_core.ports.clock import Clock
from agent_core.ports.journal import HeadExpectation, JournalHead
from agent_core.ports.randomness import RandomSource
from agent_core.safety.approval import verify_ledger_mac
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    CounterView,
    LedgerEntryView,
    ReservationView,
    ScopeUsage,
)
from agent_core.types.errors import (
    DuplicateIdError,
    InvalidIdError,
    JournalArgumentError,
    JournalClosedError,
    JournalCorruptionError,
    JournalError,
    JournalFileChangedError,
    JournalHeadMismatchError,
    JournalSchemaError,
    JournalStorageError,
    JournalWriteOutcomeUnknownError,
    LedgerKeyMissingError,
    OwnerAuthorizationError,
    OwnerEpochMismatchError,
    RandomSourceError,
    RecordValidationError,
    RunTenantMismatchError,
    SequenceExhaustedError,
    StaleEventEpochError,
)
from agent_core.types.event_hash import EVENT_HASH_VERSION, event_hash
from agent_core.types.events import Event, EventDraft
from agent_core.types.ids import RunId, TenantId
from agent_core.types.ledger import LedgerBatch, LedgerOp
from agent_core.types.safety import (
    AuditView,
    DispatchAuthorization,
    LedgerKeys,
    Permit,
    PermitState,
    Receipt,
    ReceiptState,
    StopEntry,
)
from agent_core.types.serialization import dump_json, load_json
from agent_core.types.values import MAX_INT64

JOURNAL_SCHEMA_VERSION: Final = 4
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
    *sqlite_ledger.ALL_DDL,
)

_SCHEMA_OBJECTS_SQL: Final = (
    "SELECT type, name, tbl_name, sql FROM sqlite_master "
    "WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY type, name"
)
_DUPLICATE_EVENT_ID_MESSAGE: Final = "UNIQUE constraint failed: events.event_id"
_COLUMNS: Final = "tenant_id, tenant_seq, event_id, run_id, run_seq, hash_version, event_hash, body"
_SELECT: Final = f"SELECT {_COLUMNS} FROM events "  # noqa: S608 (constant column list)


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


Verified = tuple[Event, str]


class SqliteJournal:
    """Journal on one SQLite file. Create with ``SqliteJournal.open``."""

    def __init__(
        self,
        *,
        path: Path,
        conn: sqlite3.Connection,
        lock: RuntimeLock,
        identity: tuple[int, int],
        owner_epoch: int,
        instance_id: str,
        clock: Clock,
        keys: LedgerKeys | None,
    ) -> None:
        self._path = path
        self._clock = clock
        self._keys = keys
        self._conn: sqlite3.Connection | None = conn
        self._lock = lock
        self._identity = identity
        self._owner_epoch = owner_epoch
        self._instance_id = instance_id

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        clock: Clock,
        random: RandomSource,
        keys: LedgerKeys | None = None,
    ) -> SqliteJournal:
        """Lock, check the schema, verify the whole journal, then claim a new epoch.

        ``keys`` are the ledger's owner and permit keys. Without them, owner
        operations and permits are refused (fail closed)."""
        if keys is not None and type(keys) is not LedgerKeys:
            raise JournalArgumentError("keys must be LedgerKeys")
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
            identity = _database_identity(path)
            _configure_before_inspection(conn)
            is_new = _inspect_schema(conn)
            _enable_wal(conn)
            instance_id = _new_instance_id(random)
            acquired_at = clock.now_ms()
            if type(acquired_at) is not int or not 0 <= acquired_at <= MAX_INT64:
                raise JournalStorageError("clock returned an invalid time")
            epoch = _initialize_verify_and_claim(conn, is_new, instance_id, acquired_at)
            if _database_identity(path) != identity:
                raise JournalFileChangedError("journal file changed while opening")
        except sqlite3.Error as exc:
            _close_quietly(conn)
            lock.release()
            raise JournalStorageError(f"cannot open journal: {type(exc).__name__}") from exc
        except BaseException:
            _close_quietly(conn)
            lock.release()
            raise
        return cls(
            path=path,
            conn=conn,
            lock=lock,
            identity=identity,
            owner_epoch=epoch,
            instance_id=instance_id,
            clock=clock,
            keys=keys,
        )

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
        """Close the connection first, then release the lock (A 5.3)."""
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
        names = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "trusted_schema")
        return {name: conn.execute(f"PRAGMA {name}").fetchone()[0] for name in names}

    # ------------------------------------------------------------ append

    def append(self, draft: EventDraft, *, expected: HeadExpectation) -> Event:
        return self.append_with_ledger(draft, expected=expected, ledger=())

    def append_with_ledger(
        self, draft: EventDraft, *, expected: HeadExpectation, ledger: tuple[LedgerOp, ...]
    ) -> Event:
        """Append one event and apply its ledger operations in one transaction."""
        conn = self._require_open()
        if type(draft) is not EventDraft:
            raise JournalArgumentError("append expects an EventDraft")
        if type(expected) is not HeadExpectation:
            raise JournalArgumentError("append expects a HeadExpectation")
        if draft.owner_epoch != self._owner_epoch:
            raise StaleEventEpochError("draft owner_epoch is not the current owner epoch")
        ops = self._ops(ledger)
        sqlite_ledger.check_pairing(draft, ops)
        with self._write_transaction(conn) as cur:
            event, digest = self._position(cur, draft, expected)
            now = self._now()
            # The runtime chain and Task limits first, so this event's own new
            # entities are known to its ledger operations (WP09-R02, R06).
            sqlite_ledger.mirror(cur, draft, now)
            for op in ops:
                sqlite_ledger.apply_op(cur, op, now_ms=now, ctx=self._ledger_context())
            self._insert_event(cur, event, digest)
            self._update_heads(cur, event, digest)
        return event

    # ------------------------------------------------------------ ledger

    def _ops(self, ledger: object) -> tuple[LedgerOp, ...]:
        if type(ledger) is not tuple:
            raise JournalArgumentError("ledger operations are a tuple")
        if not ledger:
            return ()
        try:
            # Re-validated: an operation built without validation is not trusted.
            return LedgerBatch.model_validate({"ops": ledger}).ops
        except (ValueError, TypeError) as exc:
            raise JournalArgumentError("ledger operations are invalid") from exc

    def _ledger_context(self) -> sqlite_ledger.LedgerContext:
        return sqlite_ledger.LedgerContext(keys=self._keys, owner_epoch=self._owner_epoch)

    def _now(self) -> int:
        now = self._clock.now_ms()
        if type(now) is not int or not 0 <= now <= MAX_INT64:
            raise JournalStorageError("clock returned an invalid time")
        return now

    def apply_ledger(self, ledger: tuple[LedgerOp, ...]) -> None:
        """Standalone operations (usage, reconciliation, void, recovery) in one transaction."""
        conn = self._require_open()
        ops = self._ops(ledger)
        if not ops:
            raise JournalArgumentError("no ledger operations")
        sqlite_ledger.check_standalone(ops)
        with self._write_transaction(conn) as cur:
            now = self._now()
            for op in ops:
                sqlite_ledger.apply_op(cur, op, now_ms=now, ctx=self._ledger_context())

    def apply_owner_ledger(self, batch: LedgerBatch, *, owner_mac: str) -> None:
        """Owner operations, accepted only with the owner channel's MAC."""
        conn = self._require_open()
        if type(batch) is not LedgerBatch:
            raise JournalArgumentError("owner operations come as a LedgerBatch")
        if self._keys is None:
            raise LedgerKeyMissingError("OWNER_KEY")
        if not verify_ledger_mac(self._keys.owner, batch, owner_mac):
            raise OwnerAuthorizationError("OWNER_MAC")
        sqlite_ledger.check_owner_ops(batch.ops)
        with self._write_transaction(conn) as cur:
            now = self._now()
            for op in batch.ops:
                sqlite_ledger.apply_op(cur, op, now_ms=now, ctx=self._ledger_context())

    def reservation(self, reservation_id: str) -> ReservationView | None:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.read_reservation(cur, str(reservation_id))

    def scope_usage(self, scope_type: BudgetScopeType, scope_id: str) -> ScopeUsage:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.scope_usage(cur, BudgetScopeType(scope_type), str(scope_id))

    def counter(
        self, scope_type: BudgetScopeType, scope_id: str, counter: CounterKind
    ) -> CounterView | None:
        with self._read_transaction(self._require_open()) as cur:
            kind = BudgetScopeType(scope_type)
            return sqlite_ledger.read_counter(cur, kind, str(scope_id), counter)

    def ledger_entries(self, reservation_id: str) -> tuple[LedgerEntryView, ...]:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.read_entries(cur, str(reservation_id))

    def permit(self, permit_id: str) -> Permit | None:
        """The stored permit as handed to its consumer (P1 to the PEP)."""
        with self._read_transaction(self._require_open()) as cur:
            found = sqlite_ledger.read_permit(cur, str(permit_id))
        return None if found is None else found[0]

    def permit_state(self, permit_id: str) -> PermitState | None:
        with self._read_transaction(self._require_open()) as cur:
            found = sqlite_ledger.read_permit(cur, str(permit_id))
        return None if found is None else found[1]

    def receipt(self, receipt_id: str) -> Receipt | None:
        with self._read_transaction(self._require_open()) as cur:
            found = sqlite_ledger.read_receipt(cur, str(receipt_id))
        return None if found is None else found[0]

    def receipt_state(self, receipt_id: str) -> ReceiptState | None:
        with self._read_transaction(self._require_open()) as cur:
            found = sqlite_ledger.read_receipt(cur, str(receipt_id))
        return None if found is None else found[1]

    def stop_entries(self) -> tuple[StopEntry, ...]:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.read_stops(cur)

    def audit_records(self) -> tuple[AuditView, ...]:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.read_audit(cur)

    def approval_uses(self, approval_id: str) -> int | None:
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.read_approval_uses(cur, str(approval_id))

    def verify_dispatch(
        self, receipt_id: str, *, pep_instance_id: str, action_hash: str
    ) -> DispatchAuthorization:
        """For a PEP right before it acts (WP-08): the claimed receipt must still
        match the trusted record, the stop state and the revocation generation."""
        with self._read_transaction(self._require_open()) as cur:
            return sqlite_ledger.verify_dispatch(
                cur,
                str(receipt_id),
                pep_instance_id=str(pep_instance_id),
                action_hash=str(action_hash),
                now_ms=self._now(),
                ctx=self._ledger_context(),
            )

    @contextmanager
    def _write_transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
        """One ``BEGIN IMMEDIATE`` transaction with owner checks. All or nothing."""
        try:
            self._check_files()
        except JournalError:
            self._stop()
            raise
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._stop()
            raise JournalStorageError(f"cannot begin write: {type(exc).__name__}") from exc
        cur = conn.cursor()
        try:
            self._check_owner(cur)
            yield cur
            # Ownership is checked again inside the transaction, just before COMMIT.
            self._check_owner(cur)
            self._check_files()
        except (DuplicateIdError, RunTenantMismatchError, JournalHeadMismatchError):
            self._rollback_or_unknown()
            raise
        except JournalError:
            self._rollback_or_unknown()
            self._stop()
            raise
        except sqlite3.Error as exc:
            self._rollback_or_unknown()
            self._stop()
            raise JournalStorageError(f"write failed: {type(exc).__name__}") from exc
        except BaseException:
            # Not a journal error: roll back, then let the original error propagate.
            self._rollback_or_unknown()
            raise
        try:
            self._commit()
        except Exception as exc:
            self._rollback_quietly()
            self._stop()
            raise JournalWriteOutcomeUnknownError("commit failed; outcome unknown") from exc
        except BaseException:
            # Interrupts propagate unchanged, but the outcome is unknown: stop.
            self._rollback_quietly()
            self._stop()
            raise

    def _check_owner(self, cur: sqlite3.Cursor) -> None:
        row = cur.execute(
            "SELECT epoch, instance_id FROM runtime_owner WHERE singleton = 1"
        ).fetchone()
        if row is None or row[0] != self._owner_epoch or row[1] != self._instance_id:
            raise OwnerEpochMismatchError("runtime_owner names another owner")

    def _check_files(self) -> None:
        self._lock.verify()
        if _database_identity(self._path) != self._identity:
            raise JournalFileChangedError("journal path no longer names the opened file")

    def _position(
        self, cur: sqlite3.Cursor, draft: EventDraft, expected: HeadExpectation
    ) -> Verified:
        tenant = str(draft.tenant_id)
        head = _head_row(cur, tenant)
        # Compared inside BEGIN IMMEDIATE, so no other write can come in between.
        if (head or (0, None)) != (expected.tenant_seq, expected.head_hash):
            raise JournalHeadMismatchError("tenant head is not the expected head")
        if head is not None and head[0] >= MAX_INT64:
            raise SequenceExhaustedError("tenant_seq would exceed 2**63-1")
        _check_tenant_tail(cur, tenant, head)
        tenant_seq = 1 if head is None else head[0] + 1
        prev_hash = None if head is None else head[1]
        run_seq = None
        if draft.run_id is not None:
            run = cur.execute(
                "SELECT tenant_id, run_seq FROM run_head WHERE run_id = ?", (str(draft.run_id),)
            ).fetchone()
            if run is not None and run[0] != tenant:
                raise RunTenantMismatchError("run_id belongs to another tenant")
            if run is not None and run[1] >= MAX_INT64:
                raise SequenceExhaustedError("run_seq would exceed 2**63-1")
            _check_run_tail(cur, str(draft.run_id), tenant, None if run is None else int(run[1]))
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
                f"INSERT INTO events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
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
        self._require_open().execute("COMMIT")

    def _rollback(self) -> None:
        self._require_open().execute("ROLLBACK")

    # ------------------------------------------------------------ reads

    def read_tenant(
        self, tenant_id: TenantId, *, after_tenant_seq: int, limit: int
    ) -> tuple[Event, ...]:
        """Events with ``tenant_seq > after_tenant_seq``, in ``tenant_seq`` order.

        Verified: every returned row; the row at ``after_tenant_seq`` (its hash
        must be the first returned event's ``prev_hash``); the row after the page
        (its ``prev_hash``), or the head (sequence and hash) when the page ends
        the journal. Rows before ``after_tenant_seq`` are not re-read.
        """
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        after, limit = _check_position_arguments(after_tenant_seq, limit)
        with self._read_transaction(conn) as cur:
            start = max(after, 1)
            extra = 2 if after >= 1 else 1
            rows = cur.execute(
                _SELECT + "WHERE tenant_id = ? AND tenant_seq >= ? ORDER BY tenant_seq LIMIT ?",
                (str(tenant), start, limit + extra),
            ).fetchall()
            verified = _verify_consecutive(rows, expected_first=start)
            before: Verified | None = None
            if after >= 1:
                if not verified:
                    head = _head_row(cur, str(tenant))
                    if head is not None and head[0] >= after:
                        raise JournalCorruptionError("missing event", after)
                    return ()
                before, verified = verified[0], verified[1:]
            page, beyond = verified[:limit], verified[limit:]
            if not beyond:
                last = page[-1] if page else before
                _check_head_matches(cur, str(tenant), last)
            return tuple(event for event, _ in page)

    def read_run(self, run_id: RunId, *, after_run_seq: int, limit: int) -> tuple[Event, ...]:
        """Events of one run with ``run_seq > after_run_seq``, in ``run_seq`` order.

        Verified: every returned row; for each, the tenant chain on both sides
        (the previous tenant row's hash and the next tenant row's ``prev_hash``,
        or the tenant head); the next run row, or the run head.
        """
        conn = self._require_open()
        run = _typed(run_id, RunId)
        after, limit = _check_position_arguments(after_run_seq, limit)
        with self._read_transaction(conn) as cur:
            head = cur.execute(
                "SELECT tenant_id, run_seq FROM run_head WHERE run_id = ?", (str(run),)
            ).fetchone()
            rows = cur.execute(
                _SELECT + "WHERE run_id = ? AND run_seq > ? ORDER BY run_seq LIMIT ?",
                (str(run), after, limit + 1),
            ).fetchall()
            events: list[Event] = []
            expected = after + 1
            for row in rows:
                event, digest = _load_row(row)
                if event.run_seq != expected:
                    raise JournalCorruptionError("run_seq gap or reorder", event.tenant_seq)
                if head is None or str(event.tenant_id) != head[0]:
                    raise JournalCorruptionError(
                        "run is not bound to this tenant", event.tenant_seq
                    )
                _check_chain_neighbours(cur, event, digest)
                events.append(event)
                expected += 1
            page, beyond = events[:limit], events[limit:]
            head_seq = 0 if head is None else int(head[1])
            if not beyond:
                last = page[-1].run_seq if page else after
                if page and last != head_seq:
                    raise JournalCorruptionError("run head does not match the last event")
                if not page and head_seq > after:
                    raise JournalCorruptionError("run events are missing")
            return tuple(page)

    def head(self, tenant_id: TenantId) -> JournalHead | None:
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        with self._read_transaction(conn) as cur:
            head = _head_row(cur, str(tenant))
            _check_tenant_tail(cur, str(tenant), head)
            if head is None:
                return None
            return JournalHead(tenant_id=tenant, tenant_seq=head[0], head_hash=head[1])

    def verify_tenant(self, tenant_id: TenantId) -> JournalHead | None:
        """Re-verify one tenant completely (plus SQLite's own checks)."""
        conn = self._require_open()
        tenant = _typed(tenant_id, TenantId)
        with self._read_transaction(conn) as cur:
            _check_database(cur)
            return _verify_tenant(cur, tenant)

    @contextmanager
    def _read_transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
        """A read snapshot. Fatal errors stop the instance; others leave it open."""
        try:
            conn.execute("BEGIN DEFERRED")
        except sqlite3.Error as exc:
            self._stop()
            raise JournalStorageError(f"cannot begin read: {type(exc).__name__}") from exc
        try:
            yield conn.cursor()
        except JournalError as exc:
            self._rollback_quietly()
            if exc.fatal:
                self._stop()
            raise
        except sqlite3.Error as exc:
            self._rollback_quietly()
            self._stop()
            raise JournalStorageError(f"read failed: {type(exc).__name__}") from exc
        except BaseException:
            self._rollback_quietly()
            self._stop()
            raise
        try:
            self._end_read()
        except BaseException as exc:
            self._rollback_quietly()
            self._stop()
            if isinstance(exc, sqlite3.Error):
                raise JournalStorageError(f"cannot end read: {type(exc).__name__}") from exc
            raise

    def _end_read(self) -> None:
        self._require_open().execute("COMMIT")

    # ------------------------------------------------------------ helpers

    def _require_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise JournalClosedError("journal is closed")
        return self._conn

    def _stop(self) -> None:
        """Stop this instance after a fatal error: no further reads or writes."""
        self.close()

    def _rollback_or_unknown(self) -> None:
        """Roll back. If that fails, stop and raise "outcome unknown".

        The new error is raised outside the handler of the rollback failure, so
        its ``__context__`` is the original error being handled by the caller;
        the rollback failure is kept as ``rollback_error``.
        """
        rollback_error: BaseException | None = None
        try:
            self._rollback()
        except sqlite3.Error as exc:
            rollback_error = exc
        if rollback_error is None:
            return
        self._stop()
        error = JournalWriteOutcomeUnknownError("rollback failed; outcome unknown")
        error.rollback_error = rollback_error
        raise error

    def _rollback_quietly(self) -> None:
        conn = self._conn
        if conn is not None and conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                # The caller is already raising a fatal error and closes the connection.
                return


# ---------------------------------------------------------------- open helpers


def _database_identity(path: Path) -> tuple[int, int]:
    """(device, inode) of a regular file with exactly one link; never follows a link."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise JournalFileChangedError(f"cannot check journal file: {exc.strerror}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise JournalFileChangedError("journal path is not a regular file")
    if info.st_nlink != 1:
        raise JournalFileChangedError("journal file has another hard link")
    return info.st_dev, info.st_ino


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
    if info.st_nlink != 1:
        raise JournalStorageError("journal file must not have other hard links")


def _configure_before_inspection(conn: sqlite3.Connection) -> None:
    """Settings that do not modify the file (safe before the schema is checked)."""
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA trusted_schema = OFF")


def _inspect_schema(conn: sqlite3.Connection) -> bool:
    """Read-only check. Returns True for a new, empty file. Foreign files are not touched."""
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    objects = conn.execute(_SCHEMA_OBJECTS_SQL).fetchall()
    if application_id == 0 and version == 0 and not objects:
        return True
    if application_id != APPLICATION_ID:
        raise JournalSchemaError("file is not an Agent Core journal")
    if version != JOURNAL_SCHEMA_VERSION:
        raise JournalSchemaError(f"unsupported journal schema version {version}")
    if objects != _expected_schema_objects():
        raise JournalSchemaError(
            f"journal schema objects do not match version {JOURNAL_SCHEMA_VERSION}"
        )
    return False


def _enable_wal(conn: sqlite3.Connection) -> None:
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


def _initialize_verify_and_claim(
    conn: sqlite3.Connection, is_new: bool, instance_id: str, acquired_at: int
) -> int:
    """One write transaction: create or re-check the schema, verify every tenant,
    then take ownership with a new epoch. On any failure nothing is changed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.cursor()
        if is_new:
            if _inspect_schema(conn) is not True:
                raise JournalSchemaError("file changed while opening")
            for ddl in SCHEMA_DDL:
                cur.execute(ddl)
            cur.execute(
                "INSERT INTO runtime_owner (singleton, epoch, instance_id, acquired_at_ms) "
                "VALUES (1, 0, '', 0)"
            )
            for statement in sqlite_ledger.LEDGER_INIT:
                cur.execute(statement)
            cur.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            cur.execute(f"PRAGMA user_version = {JOURNAL_SCHEMA_VERSION}")
        elif _inspect_schema(conn):
            raise JournalSchemaError("file changed while opening")
        _check_database(cur)
        tenants = cur.execute(
            "SELECT tenant_id FROM tenant_head UNION SELECT tenant_id FROM events "
            "UNION SELECT tenant_id FROM run_head ORDER BY 1"
        ).fetchall()
        for (tenant,) in tenants:
            try:
                tenant_id = TenantId(tenant)
            except (InvalidIdError, TypeError) as exc:
                raise JournalCorruptionError("stored tenant_id is invalid") from exc
            _verify_tenant(cur, tenant_id)
        owner_rows = cur.execute("SELECT epoch FROM runtime_owner").fetchall()
        if len(owner_rows) != 1:
            raise JournalCorruptionError("runtime_owner must have exactly one row")
        epoch = owner_rows[0][0]
        if epoch >= MAX_INT64:
            raise SequenceExhaustedError("owner epoch would exceed 2**63-1")
        new_epoch = int(epoch) + 1
        cur.execute(
            "UPDATE runtime_owner SET epoch = ?, instance_id = ?, acquired_at_ms = ? "
            "WHERE singleton = 1",
            (new_epoch, instance_id, acquired_at),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return new_epoch


def _close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------- verification


def _check_database(cur: sqlite3.Cursor) -> None:
    if cur.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise JournalCorruptionError("sqlite quick_check failed")
    if cur.execute("PRAGMA foreign_key_check").fetchall():
        raise JournalCorruptionError("foreign key check failed")


def _load_row(row: tuple[Any, ...]) -> Verified:
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


def _verify_consecutive(rows: list[tuple[Any, ...]], *, expected_first: int) -> list[Verified]:
    """Rows that must be consecutive from ``expected_first`` and linked by ``prev_hash``."""
    verified: list[Verified] = []
    expected = expected_first
    for row in rows:
        event, digest = _load_row(row)
        if event.tenant_seq != expected:
            raise JournalCorruptionError("tenant_seq gap or reorder", expected)
        if verified and event.prev_hash != verified[-1][1]:
            raise JournalCorruptionError("hash chain broken", event.tenant_seq)
        verified.append((event, digest))
        expected += 1
    return verified


def _head_row(cur: sqlite3.Cursor, tenant: str) -> tuple[int, str] | None:
    row = cur.execute(
        "SELECT tenant_seq, head_hash FROM tenant_head WHERE tenant_id = ?", (tenant,)
    ).fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


def _tenant_row(cur: sqlite3.Cursor, tenant: str, tenant_seq: int) -> tuple[Any, ...] | None:
    row: tuple[Any, ...] | None = cur.execute(
        _SELECT + "WHERE tenant_id = ? AND tenant_seq = ?", (tenant, tenant_seq)
    ).fetchone()
    return row


def _check_head_matches(cur: sqlite3.Cursor, tenant: str, last: Verified | None) -> None:
    """The head must name ``last`` exactly (sequence and hash), or be absent if none."""
    head = _head_row(cur, tenant)
    if last is None:
        if head is not None:
            raise JournalCorruptionError("head exists without events")
        return
    event, digest = last
    if head is None or head != (event.tenant_seq, digest):
        raise JournalCorruptionError("tenant head does not match the last event", event.tenant_seq)


def _check_tenant_tail(cur: sqlite3.Cursor, tenant: str, head: tuple[int, str] | None) -> None:
    """The head names the last stored event: that row exists, verifies, has the head's
    hash and is linked to its predecessor, and nothing exists after it."""
    if head is None:
        if cur.execute("SELECT 1 FROM events WHERE tenant_id = ? LIMIT 1", (tenant,)).fetchone():
            raise JournalCorruptionError("events exist without a head")
        return
    seq, head_hash = head
    first = max(seq - 1, 1)
    rows = cur.execute(
        _SELECT + "WHERE tenant_id = ? AND tenant_seq >= ? ORDER BY tenant_seq LIMIT 3",
        (tenant, first),
    ).fetchall()
    verified = _verify_consecutive(rows, expected_first=first)
    if not verified or verified[-1][0].tenant_seq != seq:
        raise JournalCorruptionError("tenant head does not match the stored events", seq)
    if verified[-1][1] != head_hash:
        raise JournalCorruptionError("tenant head hash does not match the last event", seq)


def _check_run_tail(cur: sqlite3.Cursor, run: str, tenant: str, run_seq: int | None) -> None:
    rows = cur.execute(
        _SELECT + "WHERE run_id = ? AND run_seq >= ? ORDER BY run_seq LIMIT 2",
        (run, run_seq or 1),
    ).fetchall()
    if run_seq is None:
        if rows:
            raise JournalCorruptionError("run events exist without a run head")
        return
    if len(rows) != 1:
        raise JournalCorruptionError("run head does not match the stored events")
    event, _ = _load_row(rows[0])
    if event.run_seq != run_seq or str(event.tenant_id) != tenant:
        raise JournalCorruptionError("run head does not match the stored events")


def _check_chain_neighbours(cur: sqlite3.Cursor, event: Event, digest: str) -> None:
    """``event`` is in its tenant chain: linked to the previous row and to the next
    row (or to the head when it is the last)."""
    tenant = str(event.tenant_id)
    if event.tenant_seq > 1:
        before = _tenant_row(cur, tenant, event.tenant_seq - 1)
        if before is None:
            raise JournalCorruptionError("missing event", event.tenant_seq - 1)
        if _load_row(before)[1] != event.prev_hash:
            raise JournalCorruptionError("hash chain broken", event.tenant_seq)
    after = _tenant_row(cur, tenant, event.tenant_seq + 1)
    if after is None:
        if _head_row(cur, tenant) != (event.tenant_seq, digest):
            raise JournalCorruptionError("tenant head does not match the last event")
    elif _load_row(after)[0].prev_hash != digest:
        raise JournalCorruptionError("hash chain broken", event.tenant_seq + 1)


def _verify_tenant(cur: sqlite3.Cursor, tenant: TenantId) -> JournalHead | None:
    rows = cur.execute(
        _SELECT + "WHERE tenant_id = ? ORDER BY tenant_seq", (str(tenant),)
    ).fetchall()
    events = _verify_consecutive(rows, expected_first=1)
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
    _check_head_matches(cur, str(tenant), events[-1] if events else None)
    if not events:
        return None
    last_event, last_hash = events[-1]
    return JournalHead(tenant_id=tenant, tenant_seq=last_event.tenant_seq, head_hash=last_hash)
