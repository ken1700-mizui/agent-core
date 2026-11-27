"""Journal open-time checks read the events as a stream (tests first).

``sqlite_journal._verify_tenant`` (the tenant's chain) and
``sqlite_ledger._journal_view`` (the runtime chain as the events record it) read
the events from the cursor one at a time instead of building a list of every
event. Every check stays: format, hash, chain, first and last event, tenant_seq,
run_seq, tenant and run heads, tenants, the ledger; gaps, duplicates and
reordering are refused; everything is read in the one read transaction.

Compared here: the results are those of the list-based reference (the earlier
algorithm, kept in this file), the checks still refuse damage deep in a long
journal, and the memory the checks hold no longer grows with every event.
"""

from __future__ import annotations

import shutil
import sqlite3
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_journal as sj
from agent_core.adapters import sqlite_ledger as sl
from agent_core.types.errors import JournalCorruptionError, LedgerInconsistencyError
from agent_core.types.events import Event
from agent_core.types.ids import TenantId
from agent_core.types.transitions import RuntimePayload
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw

STEPS = 1000  # about 3,000 events


@pytest.fixture(scope="module")
def long_journal(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("long")
    st = iw.shape_a(lw.setup(base), steps=STEPS)
    st.journal.close()
    return st.path


@pytest.fixture
def copy(long_journal: Path, tmp_path: Path) -> Path:
    target = tmp_path / "journal.db"
    shutil.copyfile(long_journal, target)
    return target


@contextmanager
def _reading(path: Path) -> Iterator[sqlite3.Cursor]:
    """One read transaction, as the journal's open checks use."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        yield cur
        cur.execute("ROLLBACK")
    finally:
        conn.close()


# ---------------------------------------------------------------- the reference


def _reference_verify_tenant(cur: sqlite3.Cursor, tenant: TenantId) -> Any:
    """The earlier list-based algorithm: the oracle for the result."""
    rows = cur.execute(
        sj._SELECT + "WHERE tenant_id = ? ORDER BY tenant_seq", (str(tenant),)
    ).fetchall()
    events = sj._verify_consecutive(rows, expected_first=1)
    if not events:
        return None
    last, digest = events[-1]
    return (last.tenant_seq, digest)


def _reference_view(cur: sqlite3.Cursor) -> dict[str, dict[str, Any]]:
    view: dict[str, dict[str, Any]] = {
        "tasks": {},
        "runs": {},
        "steps": {},
        "attempts": {},
        "tenants": {},
        "completions": {},
    }
    for (body,) in cur.execute("SELECT body FROM events ORDER BY tenant_id, tenant_seq").fetchall():
        event = Event.model_validate_json(body)
        if not isinstance(event.payload, RuntimePayload):
            continue
        for t in event.payload.transitions:
            name = {
                "task": ("tasks", "task_id"),
                "run": ("runs", "run_id"),
                "step": ("steps", "step_id"),
                "attempt": ("attempts", "attempt_id"),
            }[t.entity]
            key = str(getattr(t.after, name[1]))
            view[name[0]][key] = t.after
            view["tenants"][key] = str(event.tenant_id)
            if t.entity == "run" and t.after.status.value == "COMPLETED":
                view["completions"][key] = t.evidence
    return view


def test_the_tenant_check_gives_the_result_of_the_reference(copy: Path) -> None:
    with _reading(copy) as cur:
        head = sj._verify_tenant(cur, b.TENANT)
        assert head is not None
        assert (head.tenant_seq, head.head_hash) == _reference_verify_tenant(cur, b.TENANT)


def test_the_journal_view_is_the_view_of_the_reference(copy: Path) -> None:
    with _reading(copy) as cur:
        assert sl._journal_view(cur) == _reference_view(cur)


def test_the_whole_open_still_verifies(copy: Path) -> None:
    with _reading(copy) as cur:
        sj._check_database(cur)
        sj._verify_tenant(cur, b.TENANT)
        sl.verify_consistency(cur)


# ---------------------------------------------------------------- memory


def _peak(fn: Any) -> int:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        fn()
        return tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()


def _bodies(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT SUM(LENGTH(body)) FROM events").fetchone()[0])
    finally:
        conn.close()


def test_the_tenant_check_does_not_hold_every_event(copy: Path) -> None:
    with _reading(copy) as cur:
        peak = _peak(lambda: sj._verify_tenant(cur, b.TENANT))
    # A list of every parsed event is several times the stored bodies; a stream
    # holds a few rows at a time.
    assert peak < _bodies(copy) // 4, peak


def test_the_journal_view_holds_the_entities_not_every_event(copy: Path) -> None:
    with _reading(copy) as cur:
        streamed = _peak(lambda: sl._journal_view(cur))
        listed = _peak(lambda: _reference_view(cur))
    assert streamed < listed * 3 // 4, (streamed, listed)


# ---------------------------------------------------------------- damage deep in the journal


def _tamper(path: Path, *sql: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for statement in sql:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


# Damage at tenant_seq 2500, far past the first rows read.
DAMAGE = [
    pytest.param(("DELETE FROM events WHERE tenant_seq = 2500",), id="gap"),
    pytest.param(
        (
            "UPDATE events SET tenant_seq = 999999 WHERE tenant_seq = 2500",
            "UPDATE events SET tenant_seq = 2500 WHERE tenant_seq = 2501",
        ),
        id="reorder",
    ),
    pytest.param(
        (
            "UPDATE events SET event_hash = substr(event_hash, 1, length(event_hash) - 1)"
            " || CASE WHEN substr(event_hash, -1) = '0' THEN '1' ELSE '0' END"
            " WHERE tenant_seq = 2500",
        ),
        id="hash",
    ),
    pytest.param(
        ("UPDATE events SET run_seq = 999999 WHERE tenant_seq = 2500",), id="run-seq-column"
    ),
    pytest.param(("UPDATE tenant_head SET tenant_seq = tenant_seq - 1",), id="tenant-head"),
    pytest.param(("UPDATE run_head SET run_seq = run_seq + 1",), id="run-head"),
]


def _check_all(path: Path) -> None:
    with _reading(path) as cur:
        sj._verify_tenant(cur, b.TENANT)
        sl.verify_consistency(cur)


@pytest.mark.parametrize("damage", DAMAGE)
def test_damage_deep_in_a_long_journal_is_refused(copy: Path, damage: tuple[str, ...]) -> None:
    _tamper(copy, *damage)
    with pytest.raises((JournalCorruptionError, LedgerInconsistencyError)):
        _check_all(copy)
