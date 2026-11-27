"""QR-02 and QR-03 (independent quality review, MEDIUM): the tenant of the
runtime chain and the three tables added in WP-12/WP-15 are checked at every
open, normal and read-only (regression tests, written first).

QR-02: the journal's events are the source of each Task's, Run's, Step's and
Attempt's tenant; the mirror's copies (``rt_tasks``, ``rt_runs``) must agree,
and a parent and its children must be of one tenant.

QR-03: ``entity_times``, ``run_status_times`` and ``operation_results`` are the
ledger's own records (sources of truth, never re-derivable from the events):
each must exist where the contract needs it, name a known entity, keep its
times within the ledger's own clock, agree with the journal's states, and is
never guessed by a repair.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_ledger
from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.types.errors import LedgerInconsistencyError
from agent_core.types.events import Event
from agent_core.types.records import RunStatus
from agent_core.types.transitions import RunTransition, StepTransition, VerificationEvidence
from support import builders as b
from support import ledger_world as lw
from support import loop_world as lp
from support import runtime_world as w

R = RunStatus


def _sql(path: Path, *statements: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(path)
    try:
        for statement in statements:
            conn.execute(statement, params if "?" in statement else ())
        conn.commit()
    finally:
        conn.close()


def _one(path: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def _open(st: lw.Setup) -> SqliteJournal:
    return SqliteJournal.open(st.path, clock=st.clock, random=OsRandomSource(), keys=st.keys)


def _open_read_only(st: lw.Setup) -> SqliteJournal:
    return SqliteJournal.open_read_only(st.path, clock=st.clock, keys=st.keys)


def _refused_by_both_opens(st: lw.Setup) -> None:
    for opener in (_open, _open_read_only):
        with pytest.raises(LedgerInconsistencyError):
            opener(st).close()


def _running(tmp_path: Path) -> tuple[lw.Setup, Any]:
    st = lw.setup(tmp_path)
    run = w.running_run(st.sm)
    st.journal.close()
    return st, run


def _high_water(st: lw.Setup) -> int:
    return int(_one(st.path, "SELECT high_water_ms FROM ledger_clock"))


# ================================================================ QR-02 tenant


def test_a_tasks_tenant_differing_from_the_journal_is_refused_at_open(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "UPDATE rt_tasks SET tenant_id = 'tnt_other'")
    _refused_by_both_opens(st)


def test_a_runs_tenant_differing_from_the_journal_is_refused_at_open(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "UPDATE rt_runs SET tenant_id = 'tnt_other'")
    _refused_by_both_opens(st)


def _rewrite_event(
    path: Path, pick: Callable[[Event], bool], change: Callable[[Any], None]
) -> None:
    conn = sqlite3.connect(path)
    try:
        for rowid, body in conn.execute("SELECT rowid, body FROM events").fetchall():
            if pick(Event.model_validate_json(body)):
                data = json.loads(body)
                change(data)
                conn.execute(
                    "UPDATE events SET body = ? WHERE rowid = ?", (json.dumps(data), rowid)
                )
                conn.commit()
                return
    finally:
        conn.close()
    raise AssertionError("no such event")


def _creates_a_step(event: Event) -> bool:
    payload = event.payload
    return payload is not None and any(
        isinstance(t, StepTransition) and t.before is None
        for t in getattr(payload, "transitions", ())
    )


def test_a_child_of_another_tenant_than_its_parent_is_refused(tmp_path: Path) -> None:
    """The journal's own events disagree (a Step recorded under another tenant
    than its Run). The hash chain refuses such a rewrite at open first; the
    ledger's check refuses it independently, as a consistency error."""
    st = lw.setup(tmp_path)
    run = w.running_run(st.sm)
    lw.step_in(st, run)
    st.journal.close()
    copy = tmp_path / "copy.db"
    shutil.copy(st.path, copy)
    other = str(b.new_id(type(b.TENANT)))
    _rewrite_event(copy, _creates_a_step, lambda data: data.update(tenant_id=other))
    conn = sqlite3.connect(copy)
    try:
        with pytest.raises(LedgerInconsistencyError, match="TENANT_MISMATCH"):
            sqlite_ledger.verify_consistency(conn.cursor())
        with pytest.raises(LedgerInconsistencyError, match="TENANT_MISMATCH"):
            sqlite_ledger.repair_mirror(conn.cursor())  # never "repaired" from a broken source
    finally:
        conn.close()


def test_a_projection_pointing_at_another_parent_is_refused(tmp_path: Path) -> None:
    st, run = _running(tmp_path)
    other = str(b.new_id(type(run.task_id)))
    _sql(st.path, "UPDATE rt_runs SET task_id = ?", params=(other,))
    _refused_by_both_opens(st)


def test_the_repair_restores_the_tenant_and_is_idempotent(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(
        st.path,
        "UPDATE rt_tasks SET tenant_id = 'tnt_other'",
        "UPDATE rt_runs SET tenant_id = 'tnt_other'",
    )
    repaired = SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert set(repaired) == {"rt_tasks", "rt_runs"}
    _open(st).close()
    _open_read_only(st).close()
    assert SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys) == ()
    assert _one(st.path, "SELECT tenant_id FROM rt_runs") == str(b.TENANT)


# ================================================================ QR-03 entity_times


def test_a_missing_creation_time_is_refused(tmp_path: Path) -> None:
    st, run = _running(tmp_path)
    _sql(st.path, "DELETE FROM entity_times WHERE entity_id = ?", params=(str(run.run_id),))
    _refused_by_both_opens(st)


def test_an_orphan_creation_time_is_refused(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "INSERT INTO entity_times (entity_id, created_at_ms) VALUES ('run_orphan', 1)")
    _refused_by_both_opens(st)


def test_a_creation_time_after_the_ledgers_clock_is_refused(tmp_path: Path) -> None:
    st, run = _running(tmp_path)
    later = _high_water(st) + 1_000
    _sql(
        st.path,
        "UPDATE entity_times SET created_at_ms = ? WHERE entity_id = ?",
        params=(later, str(run.run_id)),
    )
    _sql(st.path, "UPDATE rt_runs SET created_at_ms = ?", params=(later,))
    _refused_by_both_opens(st)


def test_a_run_created_before_its_task_is_refused(tmp_path: Path) -> None:
    st, run = _running(tmp_path)
    task_time = int(
        _one(
            st.path,
            "SELECT created_at_ms FROM entity_times WHERE entity_id = ?",
            (str(run.task_id),),
        )
    )
    _sql(
        st.path,
        "UPDATE entity_times SET created_at_ms = ? WHERE entity_id = ?",
        params=(task_time - 1, str(run.run_id)),
    )
    _sql(st.path, "UPDATE rt_runs SET created_at_ms = ?", params=(task_time - 1,))
    _refused_by_both_opens(st)


# ================================================================ QR-03 run_status_times


def test_a_missing_status_time_is_refused(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "DELETE FROM run_status_times")
    _refused_by_both_opens(st)


def test_an_orphan_status_time_is_refused(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(
        st.path,
        "INSERT INTO run_status_times (run_id, status, since_ms) "
        "VALUES ('run_orphan', 'RUNNING', 1)",
    )
    _refused_by_both_opens(st)


def test_a_status_time_of_another_status_is_refused(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "UPDATE run_status_times SET status = 'CANCELLING'")
    _refused_by_both_opens(st)


@pytest.mark.parametrize("where", ["before_the_run", "after_the_clock"])
def test_a_status_time_out_of_its_bounds_is_refused(tmp_path: Path, where: str) -> None:
    st, run = _running(tmp_path)
    created = int(
        _one(
            st.path,
            "SELECT created_at_ms FROM entity_times WHERE entity_id = ?",
            (str(run.run_id),),
        )
    )
    since = created - 1 if where == "before_the_run" else _high_water(st) + 1
    _sql(st.path, "UPDATE run_status_times SET since_ms = ?", params=(since,))
    _refused_by_both_opens(st)


def test_an_event_that_keeps_the_status_does_not_extend_the_time(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run = w.running_run(st.sm)
    since = st.journal.run_status_since(run.run_id)
    st.clock.now += 5_000
    lw.step_in(st, run)  # a new event of the Run, its status unchanged
    assert st.journal.run_status_since(run.run_id) == since


def test_a_status_time_read_is_refused_when_it_disagrees(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run = w.running_run(st.sm)
    _sql(st.path, "UPDATE run_status_times SET status = 'CANCELLING'")  # under the open journal
    with pytest.raises(LedgerInconsistencyError):
        st.journal.run_status_since(run.run_id)


# ================================================================ QR-03 operation_results


def _completed(tmp_path: Path) -> tuple[lp.World, Any]:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    outcome = wd.loop.run(wd.run.run_id)
    for _ in range(10):
        if outcome.status is not R.WAITING_APPROVAL or outcome.pending_action_hash is None:
            break
        outcome = lp.approve_pending(wd, outcome)
    assert outcome.status is R.COMPLETED
    evidence = None
    for event in wd.st.journal.read_run(wd.run.run_id, after_run_seq=0, limit=1_000):
        for t in event.payload.transitions if event.payload else ():
            if isinstance(t, RunTransition) and t.after.status is R.COMPLETED:
                evidence = t.evidence
    assert isinstance(evidence, VerificationEvidence)
    [check] = evidence.checks
    wd.st.journal.close()
    return wd, check


def test_a_completed_runs_verification_result_must_be_recorded(tmp_path: Path) -> None:
    wd, check = _completed(tmp_path)
    _sql(
        wd.st.path,
        "DELETE FROM operation_results WHERE attempt_id = ?",
        params=(str(check.read_attempt_id),),
    )
    _refused_by_both_opens(wd.st)


def test_a_verification_result_differing_from_the_completion_is_refused(tmp_path: Path) -> None:
    wd, check = _completed(tmp_path)
    _sql(
        wd.st.path,
        "UPDATE operation_results SET result_digest = ? WHERE attempt_id = ?",
        params=(b.digest("another content"), str(check.read_attempt_id)),
    )
    _refused_by_both_opens(wd.st)


def test_the_completed_world_opens_cleanly(tmp_path: Path) -> None:
    wd, _ = _completed(tmp_path)
    _open(wd.st).close()
    _open_read_only(wd.st).close()


def test_an_orphan_result_is_refused(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(
        st.path,
        "INSERT INTO operation_results (attempt_id, result_digest) VALUES ('att_orphan', ?)",
        params=(b.digest("x"),),
    )
    _refused_by_both_opens(st)


def test_a_result_of_an_operation_never_started_is_refused(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = lw.issued(st, lw.step_in(st))  # PERMIT_ISSUED: never started
    st.journal.close()
    _sql(
        st.path,
        "INSERT INTO operation_results (attempt_id, result_digest) VALUES (?, ?)",
        params=(str(attempt.attempt_id), b.digest("x")),
    )
    _refused_by_both_opens(st)


def test_a_malformed_result_digest_is_refused(tmp_path: Path) -> None:
    wd, check = _completed(tmp_path)
    _sql(
        wd.st.path,
        "UPDATE operation_results SET result_digest = 'not-a-digest' WHERE attempt_id = ?",
        params=(str(check.read_attempt_id),),
    )
    _refused_by_both_opens(wd.st)


def test_a_repair_never_rewrites_the_ledgers_own_records(tmp_path: Path) -> None:
    st, _ = _running(tmp_path)
    _sql(st.path, "DELETE FROM run_status_times")  # not derivable from the events
    with pytest.raises(LedgerInconsistencyError):
        SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert _one(st.path, "SELECT COUNT(*) FROM run_status_times") == 0  # nothing guessed
