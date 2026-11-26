"""SQLite Journal (WP-03): schema, append, sequences, hash chain, integrity, owner epoch.

All data is synthetic. Fault injection here raises exceptions inside the
process (``monkeypatch``); real process kills are in test_journal_processes.py.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import (
    APPLICATION_ID,
    BUSY_TIMEOUT_MS,
    JOURNAL_SCHEMA_VERSION,
    MAX_READ_LIMIT,
    SqliteJournal,
)
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.ports.journal import JournalPort, JournalVerifier
from agent_core.types.errors import (
    DuplicateIdError,
    JournalArgumentError,
    JournalClosedError,
    JournalCorruptionError,
    JournalSchemaError,
    JournalStorageError,
    JournalWriteOutcomeUnknownError,
    OwnerEpochMismatchError,
    RunTenantMismatchError,
    RuntimeLockHeldError,
    RuntimeLockLostError,
    SequenceExhaustedError,
    StaleEventEpochError,
)
from agent_core.types.event_hash import event_hash
from agent_core.types.ids import EventId, RunId, TenantId
from agent_core.types.records import Event
from agent_core.types.values import MAX_INT64
from support import builders as b
from support.fakes import ScriptedRandomSource


def _open(path: Path) -> SqliteJournal:
    return SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "journal.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[SqliteJournal]:
    opened = _open(db_path)
    try:
        yield opened
    finally:
        opened.close()


def _draft(journal: SqliteJournal, **overrides: Any) -> Any:
    return b.event_draft(owner_epoch=journal.owner_epoch, **overrides)


def _raw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _count(db_path: Path, table: str) -> int:
    conn = _raw(db_path)
    try:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608
    finally:
        conn.close()


# ---------------------------------------------------------------- initialization


def test_new_database_is_initialized_with_wal_and_schema_version(db_path: Path) -> None:
    journal = _open(db_path)
    try:
        assert isinstance(journal, JournalPort)
        assert isinstance(journal, JournalVerifier)
        assert journal.owner_epoch == 1
        assert journal.head(b.TENANT) is None
    finally:
        journal.close()
    conn = _raw(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == JOURNAL_SCHEMA_VERSION
        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    finally:
        conn.close()


def test_connection_pragmas_are_applied(journal: SqliteJournal) -> None:
    settings = journal.connection_settings()
    assert settings == {
        "journal_mode": "wal",
        "synchronous": 2,  # FULL
        "foreign_keys": 1,
        "busy_timeout": BUSY_TIMEOUT_MS,
        "trusted_schema": 0,
    }


def test_unknown_schema_version_is_rejected(db_path: Path) -> None:
    _open(db_path).close()
    conn = _raw(db_path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(JournalSchemaError):
        _open(db_path)


def test_foreign_database_file_is_rejected(db_path: Path) -> None:
    conn = _raw(db_path)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.close()
    with pytest.raises(JournalSchemaError):
        _open(db_path)


def test_unexpected_schema_object_is_rejected(db_path: Path) -> None:
    _open(db_path).close()
    conn = _raw(db_path)
    conn.execute("CREATE TABLE extra (x INTEGER)")
    conn.close()
    with pytest.raises(JournalSchemaError):
        _open(db_path)


def test_symlinked_database_path_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    _open(real).close()
    link = tmp_path / "link.db"
    link.symlink_to(real)
    with pytest.raises(JournalStorageError):
        _open(link)


# ---------------------------------------------------------------- append and read


def test_append_assigns_positions_and_chain(journal: SqliteJournal) -> None:
    run_id = b.new_id(RunId)
    first = journal.append(_draft(journal))
    second = journal.append(_draft(journal, run_id=run_id))
    third = journal.append(_draft(journal, run_id=run_id))
    assert [e.tenant_seq for e in (first, second, third)] == [1, 2, 3]
    assert (first.run_seq, second.run_seq, third.run_seq) == (None, 1, 2)
    assert first.prev_hash is None
    assert second.prev_hash == event_hash(first)
    assert third.prev_hash == event_hash(second)
    head = journal.head(b.TENANT)
    assert head is not None
    assert (head.tenant_seq, head.head_hash) == (3, event_hash(third))
    assert journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10) == (first, second, third)
    assert journal.read_tenant(b.TENANT, after_tenant_seq=1, limit=1) == (second,)
    assert journal.read_run(run_id, after_run_seq=0, limit=10) == (second, third)


def test_tenants_and_runs_are_isolated(journal: SqliteJournal) -> None:
    other_tenant = b.new_id(TenantId)
    run_a, run_b = b.new_id(RunId), b.new_id(RunId)
    journal.append(_draft(journal, run_id=run_a))
    journal.append(_draft(journal, tenant_id=other_tenant, run_id=run_b))
    journal.append(_draft(journal, run_id=run_a))
    other = journal.append(_draft(journal, tenant_id=other_tenant, run_id=run_b))
    assert other.tenant_seq == 2
    assert other.run_seq == 2
    assert [
        e.tenant_id for e in journal.read_tenant(other_tenant, after_tenant_seq=0, limit=9)
    ] == [
        other_tenant,
        other_tenant,
    ]
    assert journal.verify_tenant(b.TENANT) is not None
    assert journal.verify_tenant(other_tenant) is not None


def test_run_cannot_move_to_another_tenant(journal: SqliteJournal, db_path: Path) -> None:
    run_id = b.new_id(RunId)
    journal.append(_draft(journal, run_id=run_id))
    with pytest.raises(RunTenantMismatchError):
        journal.append(_draft(journal, tenant_id=b.new_id(TenantId), run_id=run_id))
    assert _count(db_path, "events") == 1
    assert journal.append(_draft(journal, run_id=run_id)).run_seq == 2


def test_stale_owner_epoch_in_draft_is_rejected(journal: SqliteJournal, db_path: Path) -> None:
    with pytest.raises(StaleEventEpochError):
        journal.append(b.event_draft(owner_epoch=journal.owner_epoch + 1))
    assert _count(db_path, "events") == 0


@pytest.mark.parametrize(
    ("after", "limit"),
    [(-1, 1), (0, 0), (0, -1), (0, MAX_READ_LIMIT + 1), (True, 1), (0, True), (0.0, 1)],
)
def test_invalid_read_arguments_are_rejected(
    journal: SqliteJournal, after: Any, limit: Any
) -> None:
    with pytest.raises(JournalArgumentError):
        journal.read_tenant(b.TENANT, after_tenant_seq=after, limit=limit)
    with pytest.raises(JournalArgumentError):
        journal.read_run(b.new_id(RunId), after_run_seq=after, limit=limit)


def test_read_rejects_untyped_tenant_and_run_ids(journal: SqliteJournal) -> None:
    with pytest.raises(JournalArgumentError):
        journal.read_tenant(str(b.TENANT), after_tenant_seq=0, limit=1)  # type: ignore[arg-type]
    with pytest.raises(JournalArgumentError):
        journal.read_run(str(b.new_id(RunId)), after_run_seq=0, limit=1)  # type: ignore[arg-type]


def test_reads_beyond_the_end_are_empty(journal: SqliteJournal) -> None:
    journal.append(_draft(journal))
    assert journal.read_tenant(b.TENANT, after_tenant_seq=1, limit=5) == ()
    assert journal.read_tenant(b.new_id(TenantId), after_tenant_seq=0, limit=5) == ()


# ---------------------------------------------------------------- duplicates and rollback


def test_duplicate_event_id_rolls_back_everything(journal: SqliteJournal, db_path: Path) -> None:
    taken = journal.append(_draft(journal)).event_id
    new_run = b.new_id(RunId)
    with pytest.raises(DuplicateIdError) as excinfo:
        journal.append(_draft(journal, event_id=taken, run_id=new_run))
    assert excinfo.value.entity_id == taken
    assert _count(db_path, "events") == 1
    assert _count(db_path, "run_head") == 0
    head = journal.head(b.TENANT)
    assert head is not None
    assert head.tenant_seq == 1
    assert journal.append(_draft(journal)).tenant_seq == 2


def test_duplicate_across_tenants_is_also_rejected(journal: SqliteJournal) -> None:
    taken = journal.append(_draft(journal)).event_id
    with pytest.raises(DuplicateIdError):
        journal.append(_draft(journal, tenant_id=b.new_id(TenantId), event_id=taken))


def test_non_duplicate_constraint_failure_is_not_a_duplicate(
    journal: SqliteJournal, db_path: Path
) -> None:
    journal.append(_draft(journal))
    conn = _raw(db_path)
    conn.execute(
        "CREATE TRIGGER synthetic_fault BEFORE INSERT ON events "
        "BEGIN SELECT RAISE(ABORT, 'synthetic fault'); END"
    )
    conn.close()
    with pytest.raises(JournalStorageError) as excinfo:
        journal.append(_draft(journal))
    assert not isinstance(excinfo.value, DuplicateIdError)
    assert _count(db_path, "events") == 1
    # A storage failure stops this instance; restart and verification are required.
    with pytest.raises(JournalClosedError):
        journal.append(_draft(journal))


@pytest.mark.parametrize("fault_point", ["_insert_event", "_update_heads"])
def test_exception_inside_the_transaction_leaves_no_partial_write(
    journal: SqliteJournal, db_path: Path, monkeypatch: pytest.MonkeyPatch, fault_point: str
) -> None:
    run_id = b.new_id(RunId)
    journal.append(_draft(journal, run_id=run_id))
    original = getattr(journal, fault_point)

    def fail_after(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("synthetic fault inside transaction")

    monkeypatch.setattr(journal, fault_point, fail_after)
    with pytest.raises(RuntimeError, match="synthetic fault"):
        journal.append(_draft(journal, run_id=run_id))
    monkeypatch.undo()
    assert _count(db_path, "events") == 1
    nxt = journal.append(_draft(journal, run_id=run_id))
    assert (nxt.tenant_seq, nxt.run_seq) == (2, 2)
    assert journal.verify_tenant(b.TENANT) is not None


def test_commit_failure_is_reported_as_unknown_outcome(
    journal: SqliteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_commit() -> None:
        raise sqlite3.OperationalError("synthetic disk I/O error")

    monkeypatch.setattr(journal, "_commit", failing_commit)
    with pytest.raises(JournalWriteOutcomeUnknownError) as excinfo:
        journal.append(_draft(journal))
    assert not isinstance(excinfo.value, DuplicateIdError)
    assert excinfo.value.fatal
    with pytest.raises(JournalClosedError):
        journal.append(_draft(journal))


# ---------------------------------------------------------------- restart


def test_restart_keeps_sequences_head_and_increments_epoch(db_path: Path) -> None:
    run_id = b.new_id(RunId)
    first = _open(db_path)
    first.append(b.event_draft(owner_epoch=first.owner_epoch, run_id=run_id))
    last = first.append(b.event_draft(owner_epoch=first.owner_epoch, run_id=run_id))
    first.close()

    second = _open(db_path)
    try:
        assert second.owner_epoch == 2
        head = second.head(b.TENANT)
        assert head is not None
        assert (head.tenant_seq, head.head_hash) == (2, event_hash(last))
        nxt = second.append(b.event_draft(owner_epoch=2, run_id=run_id))
        assert (nxt.tenant_seq, nxt.run_seq, nxt.prev_hash) == (3, 3, event_hash(last))
    finally:
        second.close()


def test_closed_journal_cannot_be_used(db_path: Path) -> None:
    journal = _open(db_path)
    journal.close()
    journal.close()  # idempotent
    with pytest.raises(JournalClosedError):
        journal.append(b.event_draft(owner_epoch=1))
    with pytest.raises(JournalClosedError):
        journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1)


# ---------------------------------------------------------------- exclusive owner


def test_second_instance_in_the_same_process_is_refused(
    journal: SqliteJournal, db_path: Path
) -> None:
    with pytest.raises(RuntimeLockHeldError):
        _open(db_path)
    assert journal.append(_draft(journal)).tenant_seq == 1


def test_lock_release_allows_a_new_owner_with_a_higher_epoch(db_path: Path) -> None:
    first = _open(db_path)
    first.close()
    second = _open(db_path)
    try:
        assert second.owner_epoch == first.owner_epoch + 1
    finally:
        second.close()


def test_takeover_in_the_database_stops_the_old_owner(
    journal: SqliteJournal, db_path: Path
) -> None:
    journal.append(_draft(journal))
    conn = _raw(db_path)
    conn.execute("UPDATE runtime_owner SET epoch = epoch + 1, instance_id = 'synthetic-other'")
    conn.close()
    with pytest.raises(OwnerEpochMismatchError) as excinfo:
        journal.append(_draft(journal))
    assert excinfo.value.fatal
    assert _count(db_path, "events") == 1
    with pytest.raises(JournalClosedError):
        journal.append(_draft(journal))


def test_same_epoch_with_another_instance_id_is_rejected(
    journal: SqliteJournal, db_path: Path
) -> None:
    conn = _raw(db_path)
    conn.execute("UPDATE runtime_owner SET instance_id = 'synthetic-other'")
    conn.close()
    with pytest.raises(OwnerEpochMismatchError):
        journal.append(_draft(journal))


@pytest.mark.parametrize("action", ["delete", "replace"])
def test_lost_lock_file_stops_writes(journal: SqliteJournal, db_path: Path, action: str) -> None:
    lock_path = db_path.with_name(db_path.name + ".lock")
    lock_path.unlink()
    if action == "replace":
        lock_path.write_text("synthetic replacement\n", encoding="utf-8")
    with pytest.raises(RuntimeLockLostError):
        journal.append(_draft(journal))
    assert _count(db_path, "events") == 0


def test_instance_id_comes_from_the_random_source(db_path: Path) -> None:
    random = ScriptedRandomSource([bytes(range(16))])
    journal = SqliteJournal.open(db_path, clock=SystemClock(), random=random)
    try:
        assert journal.instance_id == bytes(range(16)).hex()
        assert random.requested == [16]
    finally:
        journal.close()


# ---------------------------------------------------------------- integer limits


@pytest.mark.parametrize(
    ("table", "column"), [("tenant_head", "tenant_seq"), ("run_head", "run_seq")]
)
def test_sequence_at_int64_limit_fails_safely(db_path: Path, table: str, column: str) -> None:
    run_id = b.new_id(RunId)
    journal = _open(db_path)
    journal.append(b.event_draft(owner_epoch=1, run_id=run_id))
    journal.close()
    conn = _raw(db_path)
    conn.execute(f"UPDATE {table} SET {column} = ?", (MAX_INT64,))  # noqa: S608
    conn.close()
    journal = _open(db_path)
    try:
        with pytest.raises(SequenceExhaustedError):
            journal.append(b.event_draft(owner_epoch=journal.owner_epoch, run_id=run_id))
        assert _count(db_path, "events") == 1
    finally:
        journal.close()


def test_owner_epoch_at_int64_limit_refuses_to_open(db_path: Path) -> None:
    _open(db_path).close()
    conn = _raw(db_path)
    conn.execute("UPDATE runtime_owner SET epoch = ?", (MAX_INT64,))
    conn.close()
    with pytest.raises(SequenceExhaustedError):
        _open(db_path)


# ---------------------------------------------------------------- integrity


def _tamper(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = _raw(db_path)
    conn.execute(sql, params)
    conn.close()


def _three_events(db_path: Path) -> list[Event]:
    journal = _open(db_path)
    events = [journal.append(b.event_draft(owner_epoch=1)) for _ in range(3)]
    journal.close()
    return events


def _body(db_path: Path, tenant_seq: int) -> dict[str, Any]:
    conn = _raw(db_path)
    try:
        row = conn.execute("SELECT body FROM events WHERE tenant_seq = ?", (tenant_seq,)).fetchone()
        return dict(json.loads(row[0]))
    finally:
        conn.close()


def _set_body(db_path: Path, tenant_seq: int, body: dict[str, Any]) -> None:
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    _tamper(db_path, "UPDATE events SET body = ? WHERE tenant_seq = ?", (text, tenant_seq))


def _assert_corruption_detected(db_path: Path) -> None:
    journal = _open(db_path)
    try:
        with pytest.raises(JournalCorruptionError):
            journal.verify_tenant(b.TENANT)
        with pytest.raises(JournalCorruptionError):
            journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10)
    finally:
        journal.close()


def test_untampered_journal_verifies(db_path: Path) -> None:
    events = _three_events(db_path)
    journal = _open(db_path)
    try:
        head = journal.verify_tenant(b.TENANT)
        assert head is not None
        assert (head.tenant_seq, head.head_hash) == (3, event_hash(events[-1]))
    finally:
        journal.close()


def test_modified_body_is_detected(db_path: Path) -> None:
    _three_events(db_path)
    body = _body(db_path, 2)
    body["labels"] = ["tampered"]
    _set_body(db_path, 2, body)
    _assert_corruption_detected(db_path)


def test_modified_body_with_recomputed_row_hash_breaks_the_chain(db_path: Path) -> None:
    _three_events(db_path)
    body = _body(db_path, 2)
    body["labels"] = ["tampered"]
    _set_body(db_path, 2, body)
    forged = Event.model_validate_json(json.dumps(body))
    _tamper(db_path, "UPDATE events SET event_hash = ? WHERE tenant_seq = 2", (event_hash(forged),))
    _assert_corruption_detected(db_path)


def test_missing_event_is_detected(db_path: Path) -> None:
    _three_events(db_path)
    _tamper(db_path, "DELETE FROM events WHERE tenant_seq = 2")
    _assert_corruption_detected(db_path)


def test_reordered_events_are_detected(db_path: Path) -> None:
    _three_events(db_path)
    _tamper(db_path, "UPDATE events SET tenant_seq = 99 WHERE tenant_seq = 1")
    _tamper(db_path, "UPDATE events SET tenant_seq = 1 WHERE tenant_seq = 2")
    _tamper(db_path, "UPDATE events SET tenant_seq = 2 WHERE tenant_seq = 99")
    _assert_corruption_detected(db_path)


def test_truncated_tail_is_detected_by_the_head(db_path: Path) -> None:
    _three_events(db_path)
    _tamper(db_path, "DELETE FROM events WHERE tenant_seq = 3")
    journal = _open(db_path)
    try:
        with pytest.raises(JournalCorruptionError):
            journal.verify_tenant(b.TENANT)
        with pytest.raises(JournalCorruptionError):
            journal.head(b.TENANT)
    finally:
        journal.close()


def test_unknown_event_schema_version_in_storage_is_rejected(db_path: Path) -> None:
    _three_events(db_path)
    body = _body(db_path, 1)
    body["schema_version"] = 2
    _set_body(db_path, 1, body)
    _assert_corruption_detected(db_path)


def test_column_body_mismatch_is_detected(db_path: Path) -> None:
    _three_events(db_path)
    other = str(EventId.compose(b.SITE, b.TS, 424242))
    _tamper(db_path, "UPDATE events SET event_id = ? WHERE tenant_seq = 2", (other,))
    _assert_corruption_detected(db_path)


def test_unknown_hash_version_is_rejected(db_path: Path) -> None:
    _three_events(db_path)
    _tamper(db_path, "UPDATE events SET hash_version = 2 WHERE tenant_seq = 1")
    _assert_corruption_detected(db_path)


def test_non_canonical_body_is_rejected(db_path: Path) -> None:
    _three_events(db_path)
    body = _body(db_path, 1)
    _tamper(
        db_path,
        "UPDATE events SET body = ? WHERE tenant_seq = 1",
        (json.dumps(body, indent=2, sort_keys=True),),
    )
    _assert_corruption_detected(db_path)
