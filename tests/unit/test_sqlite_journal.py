"""SQLite Journal (WP-03): schema, append, sequences, hash chain, integrity, owner epoch.

All data is synthetic. Fault injection here raises exceptions inside the
process (``monkeypatch``); real process kills are in test_journal_processes.py.
"""

from __future__ import annotations

import json
import os
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
    JournalFileChangedError,
    JournalSchemaError,
    JournalStorageError,
    JournalWriteOutcomeUnknownError,
    OwnerEpochMismatchError,
    RunTenantMismatchError,
    RuntimeLockFileError,
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
def test_sequence_at_int64_limit_fails_safely(
    journal: SqliteJournal, db_path: Path, table: str, column: str
) -> None:
    run_id = b.new_id(RunId)
    journal.append(_draft(journal, run_id=run_id))
    _tamper(db_path, f"UPDATE {table} SET {column} = ?", (MAX_INT64,))  # noqa: S608
    with pytest.raises(SequenceExhaustedError):
        journal.append(_draft(journal, run_id=run_id))
    assert _count(db_path, "events") == 1
    with pytest.raises(JournalClosedError):
        journal.append(_draft(journal, run_id=run_id))


def test_owner_epoch_at_int64_limit_refuses_to_open(db_path: Path) -> None:
    _open(db_path).close()
    _tamper(db_path, "UPDATE runtime_owner SET epoch = ?", (MAX_INT64,))
    with pytest.raises(SequenceExhaustedError):
        _open(db_path)


# ---------------------------------------------------------------- integrity helpers


def _tamper(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = _raw(db_path)
    try:
        conn.execute(sql, params)
    finally:
        conn.close()


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


def _rewrite_body_and_hash(db_path: Path, tenant_seq: int) -> None:
    """Change a row and recompute its own hash, as an attacker editing one row would."""
    body = _body(db_path, tenant_seq)
    body["labels"] = ["tampered"]
    _set_body(db_path, tenant_seq, body)
    forged = Event.model_validate_json(json.dumps(body))
    _tamper(
        db_path,
        "UPDATE events SET event_hash = ? WHERE tenant_seq = ?",
        (event_hash(forged), tenant_seq),
    )


def _open_with_events(db_path: Path, count: int = 3, **overrides: Any) -> SqliteJournal:
    journal = _open(db_path)
    for _ in range(count):
        journal.append(_draft(journal, **overrides))
    return journal


def _db_bytes(db_path: Path) -> bytes:
    return db_path.read_bytes()


def _assert_detected_then_stopped(journal: SqliteJournal, db_path: Path, read: Any) -> None:
    """A read detects the damage, the instance stops, and reopening is refused."""
    with pytest.raises(JournalCorruptionError):
        read(journal)
    assert journal.closed
    with pytest.raises(JournalClosedError):
        journal.append(b.event_draft(owner_epoch=journal.owner_epoch))
    epoch_before = _owner_epoch(db_path)
    with pytest.raises(JournalCorruptionError):
        _open(db_path)
    # Opening a damaged journal changes nothing (no epoch increment, no repair).
    assert _owner_epoch(db_path) == epoch_before


def _owner_epoch(db_path: Path) -> int:
    conn = _raw(db_path)
    try:
        return int(conn.execute("SELECT epoch FROM runtime_owner").fetchone()[0])
    finally:
        conn.close()


def _read_all(journal: SqliteJournal) -> Any:
    return journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10)


# ---------------------------------------------------------------- integrity: open-time verification


def test_untampered_journal_verifies_and_reopens(db_path: Path) -> None:
    journal = _open_with_events(db_path)
    last = _read_all(journal)[-1]
    journal.close()
    reopened = _open(db_path)
    try:
        head = reopened.verify_tenant(b.TENANT)
        assert head is not None
        assert (head.tenant_seq, head.head_hash) == (3, event_hash(last))
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "damage",
    [
        "body",
        "body_and_hash",
        "missing_middle",
        "missing_tail",
        "reorder",
        "schema_version",
        "hash_version",
        "column",
        "non_canonical",
        "head_hash",
        "orphan_head",
        "foreign_key",
        "run_head",
    ],
)
def test_open_refuses_a_damaged_journal_without_changing_it(db_path: Path, damage: str) -> None:
    _open_with_events(db_path, run_id=b.new_id(RunId)).close()
    _apply_damage(db_path, damage)
    epoch_before = _owner_epoch(db_path)
    with pytest.raises(JournalCorruptionError):
        _open(db_path)
    assert _owner_epoch(db_path) == epoch_before


def _apply_damage(db_path: Path, damage: str) -> None:
    if damage == "body":
        body = _body(db_path, 2)
        body["labels"] = ["tampered"]
        _set_body(db_path, 2, body)
    elif damage == "body_and_hash":
        _rewrite_body_and_hash(db_path, 2)
    elif damage == "missing_middle":
        _tamper(db_path, "DELETE FROM events WHERE tenant_seq = 2")
    elif damage == "missing_tail":
        _tamper(db_path, "DELETE FROM events WHERE tenant_seq = 3")
    elif damage == "reorder":
        _tamper(db_path, "UPDATE events SET tenant_seq = 99 WHERE tenant_seq = 1")
        _tamper(db_path, "UPDATE events SET tenant_seq = 1 WHERE tenant_seq = 2")
        _tamper(db_path, "UPDATE events SET tenant_seq = 2 WHERE tenant_seq = 99")
    elif damage == "schema_version":
        body = _body(db_path, 1)
        body["schema_version"] = 2
        _set_body(db_path, 1, body)
    elif damage == "hash_version":
        _tamper(db_path, "UPDATE events SET hash_version = 2 WHERE tenant_seq = 1")
    elif damage == "column":
        other = str(EventId.compose(b.SITE, b.TS, 424242))
        _tamper(db_path, "UPDATE events SET event_id = ? WHERE tenant_seq = 2", (other,))
    elif damage == "non_canonical":
        text = json.dumps(_body(db_path, 1), indent=2, sort_keys=True)
        _tamper(db_path, "UPDATE events SET body = ? WHERE tenant_seq = 1", (text,))
    elif damage == "head_hash":
        _tamper(db_path, "UPDATE tenant_head SET head_hash = ?", (b.digest("forged"),))
    elif damage == "orphan_head":
        _tamper(
            db_path,
            "INSERT INTO tenant_head VALUES (?, 1, ?)",
            (str(b.new_id(TenantId)), b.digest("orphan")),
        )
    elif damage == "foreign_key":
        _tamper(db_path, "DELETE FROM tenant_head")
    elif damage == "run_head":
        _tamper(db_path, "UPDATE run_head SET run_seq = run_seq + 1")
    else:
        raise AssertionError(damage)


# ------------------------------------------------------------ integrity: reads on an open journal


@pytest.mark.parametrize("damage", ["body", "body_and_hash", "missing_middle", "reorder", "column"])
def test_read_tenant_detects_damage_made_while_open(db_path: Path, damage: str) -> None:
    journal = _open_with_events(db_path)
    _apply_damage(db_path, damage)
    _assert_detected_then_stopped(journal, db_path, _read_all)


def test_read_run_detects_a_rewritten_body_and_hash(db_path: Path) -> None:
    run_id = b.new_id(RunId)
    journal = _open(db_path)
    journal.append(_draft(journal))
    journal.append(_draft(journal, run_id=run_id))
    journal.append(_draft(journal))
    _rewrite_body_and_hash(db_path, 2)
    _assert_detected_then_stopped(
        journal, db_path, lambda j: j.read_run(run_id, after_run_seq=0, limit=10)
    )


def test_read_run_detects_a_rewritten_tail_event(db_path: Path) -> None:
    run_id = b.new_id(RunId)
    journal = _open(db_path)
    journal.append(_draft(journal))
    journal.append(_draft(journal, run_id=run_id))
    _rewrite_body_and_hash(db_path, 2)
    _assert_detected_then_stopped(
        journal, db_path, lambda j: j.read_run(run_id, after_run_seq=0, limit=1)
    )


@pytest.mark.parametrize("limit", [1, 2, 3, 10], ids=["short", "fewer", "exact", "more"])
def test_tail_hash_must_match_head_hash_for_every_limit(db_path: Path, limit: int) -> None:
    journal = _open_with_events(db_path)
    _tamper(db_path, "UPDATE tenant_head SET head_hash = ?", (b.digest("forged"),))
    after = 3 - min(limit, 3)
    _assert_detected_then_stopped(
        journal, db_path, lambda j: j.read_tenant(b.TENANT, after_tenant_seq=after, limit=limit)
    )


def test_limit_equal_to_remaining_rows_still_checks_the_tail(db_path: Path) -> None:
    journal = _open_with_events(db_path)
    _rewrite_body_and_hash(db_path, 3)
    _assert_detected_then_stopped(
        journal, db_path, lambda j: j.read_tenant(b.TENANT, after_tenant_seq=1, limit=2)
    )


def test_page_checks_the_link_to_the_previous_row(db_path: Path) -> None:
    journal = _open_with_events(db_path)
    _rewrite_body_and_hash(db_path, 2)
    _assert_detected_then_stopped(
        journal, db_path, lambda j: j.read_tenant(b.TENANT, after_tenant_seq=2, limit=1)
    )


def test_damage_before_the_page_is_outside_a_partial_read(db_path: Path) -> None:
    # Documented guarantee: a page verifies its rows, the row just before it and
    # the link after it. Older rows are covered by open-time verification and
    # verify_tenant, not by every partial read.
    journal = _open_with_events(db_path, count=4)
    try:
        body = _body(db_path, 1)
        body["labels"] = ["tampered"]
        _set_body(db_path, 1, body)
        page = journal.read_tenant(b.TENANT, after_tenant_seq=2, limit=2)
        assert [e.tenant_seq for e in page] == [3, 4]
        with pytest.raises(JournalCorruptionError):
            journal.verify_tenant(b.TENANT)
        assert journal.closed
    finally:
        journal.close()


def test_head_detects_damage_made_while_open(db_path: Path) -> None:
    journal = _open_with_events(db_path)
    _apply_damage(db_path, "missing_tail")
    _assert_detected_then_stopped(journal, db_path, lambda j: j.head(b.TENANT))


def test_append_checks_the_current_head_against_the_tail(db_path: Path) -> None:
    journal = _open_with_events(db_path)
    _tamper(db_path, "UPDATE tenant_head SET head_hash = ?", (b.digest("forged"),))
    with pytest.raises(JournalCorruptionError):
        journal.append(_draft(journal))
    assert _count(db_path, "events") == 3
    assert journal.closed


def test_append_checks_the_run_head_against_its_tail(db_path: Path) -> None:
    run_id = b.new_id(RunId)
    journal = _open_with_events(db_path, run_id=run_id)
    _tamper(db_path, "UPDATE run_head SET run_seq = 2")
    with pytest.raises(JournalCorruptionError):
        journal.append(_draft(journal, run_id=run_id))
    assert _count(db_path, "events") == 3


def test_non_fatal_argument_error_keeps_the_journal_open(journal: SqliteJournal) -> None:
    with pytest.raises(JournalArgumentError):
        journal.read_tenant(b.TENANT, after_tenant_seq=-1, limit=1)
    assert not journal.closed
    assert journal.append(_draft(journal)).tenant_seq == 1


# ---------------------------------------------------------------- transaction end failures


def test_read_transaction_end_failure_stops_the_journal(
    journal: SqliteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_end() -> None:
        raise sqlite3.OperationalError("synthetic read end failure")

    monkeypatch.setattr(journal, "_end_read", failing_end)
    with pytest.raises(JournalStorageError):
        journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1)
    assert journal.closed


@pytest.mark.parametrize("error", [RuntimeError("synthetic"), MemoryError()])
def test_unusual_exception_during_commit_is_an_unknown_outcome(
    journal: SqliteJournal, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def failing_commit() -> None:
        raise error

    monkeypatch.setattr(journal, "_commit", failing_commit)
    with pytest.raises(JournalWriteOutcomeUnknownError) as excinfo:
        journal.append(_draft(journal))
    assert excinfo.value.__cause__ is error
    assert journal.closed


def test_interrupt_during_commit_stops_the_journal_and_propagates(
    journal: SqliteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_commit() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(journal, "_commit", interrupted_commit)
    with pytest.raises(KeyboardInterrupt):
        journal.append(_draft(journal))
    assert journal.closed


def test_rollback_failure_keeps_the_original_error_as_context(
    journal: SqliteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RuntimeError("synthetic original failure")

    def failing_update(*_args: Any) -> None:
        raise original

    def failing_rollback() -> None:
        raise sqlite3.OperationalError("synthetic rollback failure")

    monkeypatch.setattr(journal, "_update_heads", failing_update)
    monkeypatch.setattr(journal, "_rollback", failing_rollback)
    with pytest.raises(JournalWriteOutcomeUnknownError) as excinfo:
        journal.append(_draft(journal))
    assert excinfo.value.__context__ is original
    assert journal.closed


def test_connection_is_closed_before_the_lock_is_released(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _open(db_path)
    lock = journal._lock
    original_release = lock.release
    observed: list[bool] = []

    def release() -> None:
        observed.append(journal.closed)
        original_release()

    monkeypatch.setattr(lock, "release", release)
    journal.close()
    assert observed == [True]


# ---------------------------------------------------------------- files, aliases and links


def test_hard_linked_database_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "journal.db"
    _open(db_path).close()
    alias = tmp_path / "alias.db"
    os.link(db_path, alias)
    with pytest.raises(JournalStorageError):
        _open(alias)
    with pytest.raises(JournalStorageError):
        _open(db_path)


def test_hard_link_created_while_open_stops_writes(journal: SqliteJournal, db_path: Path) -> None:
    os.link(db_path, db_path.with_name("alias.db"))
    with pytest.raises(JournalFileChangedError):
        journal.append(_draft(journal))
    assert journal.closed
    assert _count(db_path, "events") == 0


def test_database_replaced_while_open_stops_writes(
    journal: SqliteJournal, db_path: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other.db"
    other.write_bytes(b"")
    os.replace(other, db_path)
    with pytest.raises(JournalFileChangedError):
        journal.append(_draft(journal))
    assert journal.closed


def test_directory_alias_shares_the_same_lock(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    journal = _open(real / "journal.db")
    try:
        with pytest.raises(RuntimeLockHeldError):
            _open(alias / "journal.db")
    finally:
        journal.close()


@pytest.mark.parametrize("content", [b"important data\n", b"pid 12 (display only)\nextra"])
def test_unexpected_lock_file_content_is_refused_and_kept(db_path: Path, content: bytes) -> None:
    lock_path = db_path.with_name(db_path.name + ".lock")
    lock_path.write_bytes(content)
    with pytest.raises(RuntimeLockFileError):
        _open(db_path)
    assert lock_path.read_bytes() == content


def test_hard_linked_lock_file_is_refused_and_kept(db_path: Path, tmp_path: Path) -> None:
    precious = tmp_path / "precious.txt"
    precious.write_bytes(b"")
    os.link(precious, db_path.with_name(db_path.name + ".lock"))
    with pytest.raises(RuntimeLockFileError):
        _open(db_path)
    assert precious.read_bytes() == b""


def test_foreign_database_is_not_modified(db_path: Path) -> None:
    conn = _raw(db_path)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.close()
    before = _db_bytes(db_path)
    with pytest.raises(JournalSchemaError):
        _open(db_path)
    assert _db_bytes(db_path) == before
    assert not db_path.with_name(db_path.name + "-wal").exists()
