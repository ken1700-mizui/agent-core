"""WP-12 stage 2: Rebuild, ProjectionRepair, Attempt and Run recovery, costs,
reconciliation, deadlines and temporary files (tests first).

A crash is simulated in-process here: the state is built up to a crash point,
then the journal is closed and opened again (a new owner epoch; unused permits
and receipts of the old owner are revoked), the runtime is rebuilt, and the
owner runs the recovery explicitly. Real process kills are in stage 3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.file_broker import temp_tag
from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.runtime import recovery as rc
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.state_machine import rebuild_with_head
from agent_core.types.cost import ReservationState
from agent_core.types.errors import (
    AgentCoreError,
    JournalCorruptionError,
    LedgerInconsistencyError,
)
from agent_core.types.files import TEMP_FILE_PREFIX
from agent_core.types.ledger import RecoverInFlight
from agent_core.types.model import CostStatus
from agent_core.types.records import AttemptState, RunStatus, StepStatus
from agent_core.types.safety import StopScopeType
from agent_core.types.transitions import ReconciliationTrigger, RunTransition, Termination
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = RunStatus
RS = ReservationState
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)


# ---------------------------------------------------------------- crash points


def _at(st: lw.Setup, state: AttemptState) -> Any:
    """An attempt of a running Run, built up to ``state`` through the real path."""
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    if state is A.PROPOSED:
        return st.sm.create_attempt(step)
    if state is A.VALIDATED:
        attempt = st.sm.create_attempt(step)
        return st.sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    if state in (A.APPROVAL_PENDING, A.APPROVED):
        # A tool whose effect needs the owner's approval (the PDP's own rule).
        attempt = lw.fixed_attempt(
            st,
            step,
            scopes,
            registration=s.SYNTH_CHANGE_TOOL,
            canonical_input=tw.SYNTHETIC_INPUT,
        )
        pending = st.sm.advance_attempt(lw.allowed(st, attempt), A.APPROVAL_PENDING)
        return pending if state is A.APPROVAL_PENDING else lw.approved(st, pending)
    if state is A.OUTCOME_UNKNOWN:

        def boom(_authorization: Any) -> SyntheticResult:
            raise RuntimeError("synthetic: the operation's result is lost")

        attempt = lw.consume(st, lw.issued(st, step))
        with pytest.raises(RuntimeError):
            lw.gate(st).execute(attempt, run=boom, **lw.actual(st, attempt))
        return st.sm.state.attempts[attempt.attempt_id]
    attempt = lw.fixed_attempt(st, step, scopes)
    if state is A.CONTENT_FIXED:
        return attempt
    if state is A.POLICY_ALLOWED:
        return lw.allowed(st, attempt)
    attempt = lw.reserve(st, attempt)
    if state is A.BUDGET_RESERVED:
        return attempt
    attempt = lw.intent(st, attempt)
    if state is A.INTENT_RECORDED:
        return attempt
    attempt = lw.issue(st, attempt)
    if state is A.PERMIT_ISSUED:
        return attempt
    attempt = lw.consume(st, attempt)
    if state is A.PERMIT_CONSUMED:
        return attempt
    gate = lw.gate(st)
    claimed = gate.claim(attempt, **lw.actual(st, attempt))
    if state is A.DISPATCH_AUTHORIZED:
        return claimed
    started, _ = gate.start(claimed)
    assert state is A.DISPATCH_STARTED
    return started


def _restart(st: lw.Setup) -> tuple[lw.Setup, rc.RecoveryDeps]:
    again = st.reopen()
    return again, _deps(again)


def _deps(st: lw.Setup) -> rc.RecoveryDeps:
    return rc.RecoveryDeps(
        sm=st.sm,
        ledger=st.journal,
        owner=st.owner,
        clock=st.clock,
        report_key=st.keys.permit,
        journal=st.journal,
    )


def _all(deps: rc.RecoveryDeps) -> list[Any]:
    """Every Run of this Runtime (none is live in a session here)."""
    return list(deps.sm.state.runs)


def _view(st: lw.Setup, attempt: Any) -> Any:
    if attempt.reservation_id is None:
        return None
    return st.journal.reservation(str(attempt.reservation_id))


def _run_of(st: lw.Setup, attempt: Any) -> Any:
    return st.sm.state.runs[st.sm.state.steps[attempt.step_id].run_id]


NOT_RESERVED = [
    A.PROPOSED,
    A.VALIDATED,
    A.CONTENT_FIXED,
    A.POLICY_ALLOWED,
    A.APPROVAL_PENDING,
    A.APPROVED,
]
RESERVED = [A.BUDGET_RESERVED, A.INTENT_RECORDED, A.PERMIT_ISSUED]
CONSUMED_NOT_STARTED = [A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED]
STARTED = [A.DISPATCH_STARTED, A.OUTCOME_UNKNOWN]


# ================================================================ the recovery table


def test_every_attempt_state_has_a_documented_rule() -> None:
    assert set(rc.RECOVERY_TABLE) == set(AttemptState)
    for state, rule in rc.RECOVERY_TABLE.items():
        if state in STARTED:
            assert rule.provably_not_executed is False
            assert rule.keep_unknown is True
        if state in NOT_RESERVED + RESERVED + CONSUMED_NOT_STARTED:
            assert rule.provably_not_executed is True


# ================================================================ recovery per crash point


@pytest.mark.parametrize("state", NOT_RESERVED + RESERVED)
def test_a_crash_before_the_permit_is_consumed(tmp_path: Path, state: AttemptState) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, state)
    st2, deps = _restart(st)
    plan = rc.assess(st2.sm.state, st2.journal)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    after = st2.sm.state.attempts[attempt.attempt_id]
    assert after.state is A.ABORTED_BEFORE_DISPATCH
    view = _view(st2, after)
    if view is not None:
        assert view.state in (RS.RELEASED, RS.VOID)
        assert view.hold_micro == 0
    run = _run_of(st2, after)
    assert run.status is R.FAILED  # the transcript is gone: ended, never resumed
    assert st2.sm.state.steps[after.step_id].status is StepStatus.FAILED
    assert plan.runs[0].attempts[0].rule == rc.RECOVERY_TABLE[state].code


@pytest.mark.parametrize("state", CONSUMED_NOT_STARTED)
def test_a_crash_after_consumption_but_before_the_start(
    tmp_path: Path, state: AttemptState
) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, state)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    after = st2.sm.state.attempts[attempt.attempt_id]
    # The journal proves the start was never recorded (the operation runs only
    # after it): not executed, the hold released with a recorded zero cost.
    assert after.state is A.FAILED_DEFINITE
    view = _view(st2, after)
    assert (view.state, view.hold_micro, view.spent_micro) == (RS.RECONCILED, 0, 0)
    assert _run_of(st2, after).status is R.FAILED


@pytest.mark.parametrize("state", STARTED)
def test_a_crash_after_the_start_is_kept_unknown(tmp_path: Path, state: AttemptState) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, state)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    after = st2.sm.state.attempts[attempt.attempt_id]
    assert after.state is A.OUTCOME_UNKNOWN
    view = _view(st2, after)
    assert view.state is RS.UNKNOWN
    assert view.hold_micro > 0  # never released without evidence
    assert _run_of(st2, after).status is R.RECONCILIATION_REQUIRED
    assert st2.sm.state.steps[after.step_id].status is StepStatus.RECONCILIATION_REQUIRED


def test_recovery_is_idempotent(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    unknown = _at(st, A.DISPATCH_STARTED)
    _at(st, A.PERMIT_ISSUED)
    st2, deps = _restart(st)
    first = rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    snapshot = (st2.sm.state, [_view(st2, a) for a in st2.sm.state.attempts.values()])
    second = rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert (st2.sm.state, [_view(st2, a) for a in st2.sm.state.attempts.values()]) == snapshot
    assert second.changed is False
    assert first.changed is True
    assert st2.sm.state.attempts[unknown.attempt_id].state is A.OUTCOME_UNKNOWN


def test_assessment_changes_nothing(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.DISPATCH_STARTED)
    st2, _ = _restart(st)
    before = list(sqlite3.connect(st2.path).iterdump())
    plan = rc.assess(st2.sm.state, st2.journal)
    assert plan.runs[0].attempts[0].action == "MARK_UNKNOWN"
    assert list(sqlite3.connect(st2.path).iterdump()) == before


def test_a_crash_during_the_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ref").mkdir()
    (tmp_path / "crash").mkdir()
    reference_st = lw.setup(tmp_path / "ref")
    _at(reference_st, A.PERMIT_CONSUMED)
    ref2, ref_deps = _restart(reference_st)
    rc.recover(ref_deps, deadlines=DEADLINES, run_ids=_all(ref_deps))
    expected = sorted(a.state.value for a in ref2.sm.state.attempts.values())

    st = lw.setup(tmp_path / "crash")
    _at(st, A.PERMIT_CONSUMED)
    st2, deps = _restart(st)
    real = st2.journal.append_with_ledger
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("synthetic crash during recovery")
        return real(*args, **kwargs)

    monkeypatch.setattr(st2.journal, "append_with_ledger", flaky)
    with pytest.raises(sqlite3.OperationalError):
        rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    monkeypatch.undo()
    st3, deps3 = _restart(st2)
    rc.recover(deps3, deadlines=DEADLINES, run_ids=_all(deps3))
    assert sorted(a.state.value for a in st3.sm.state.attempts.values()) == expected


def test_a_ledger_unknown_is_matched_by_the_attempt(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.PERMIT_CONSUMED)
    st.journal.apply_ledger((RecoverInFlight(op="recover_in_flight"),))  # ledger: UNKNOWN
    st2, deps = _restart(st)
    assert _view(st2, attempt).state is RS.UNKNOWN
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    after = st2.sm.state.attempts[attempt.attempt_id]
    assert after.state is A.FAILED_DEFINITE  # proven not started, reconciled at zero
    assert _view(st2, after).state is RS.RECONCILED


def test_no_unknown_work_is_run_again(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.DISPATCH_STARTED)
    st2, deps = _restart(st)
    before = set(st2.sm.state.attempts)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert set(st2.sm.state.attempts) == before  # no new Attempt
    assert _run_of(st2, attempt).status is not R.RUNNING


# ================================================================ costs


def test_no_double_release_and_no_double_count(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.BUDGET_RESERVED)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    view = _view(st2, st2.sm.state.attempts[attempt.attempt_id])
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert _view(st2, st2.sm.state.attempts[attempt.attempt_id]) == view
    from agent_core.types.ledger import ReleaseReservation

    with pytest.raises(AgentCoreError):
        st2.journal.apply_ledger(
            (
                ReleaseReservation(
                    op="release",
                    reservation_id=attempt.reservation_id,
                    attempt_id=attempt.attempt_id,
                    report=None,
                ),
            )
        )


def test_the_task_budget_is_kept_across_runs(tmp_path: Path) -> None:
    from agent_core.types.cost import BudgetScopeType

    st = lw.setup(tmp_path)
    attempt = _at(st, A.DISPATCH_STARTED)
    task_id = _run_of(st, attempt).task_id
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    usage = st2.journal.scope_usage(BudgetScopeType.TASK, str(task_id))
    assert usage.hold_micro > 0  # the unknown hold still counts for the Task


# ================================================================ reconciliation


def _report(st: lw.Setup, attempt: Any, outcome: str) -> Any:
    """The live gate's signed result report, made before the crash (then lost)."""
    current = st.sm.state.attempts[attempt.attempt_id]
    # The gate's report carries the final, known cost (an unknown cost is no evidence).
    usage = lw.zero_usage().model_copy(
        update={
            "cost": Budget(currency=Currency.XTU, micro_units=0),
            "cost_status": CostStatus.FINAL,
        }
    )
    result = SyntheticResult(outcome=outcome, usage=usage, final=True)  # type: ignore[arg-type]
    return lw.gate(st).execution_report(current, result)


def _started_with_report(st: lw.Setup, outcome: str = "COMPLETED") -> tuple[Any, Any]:
    attempt = _at(st, A.DISPATCH_STARTED)
    return attempt, _report(st, attempt, outcome)


def test_reconciliation_with_a_trusted_execution_report(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt, report = _started_with_report(st)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    result = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert result.outcome is rc.Reconciled.EXECUTED
    after = st2.sm.state.attempts[attempt.attempt_id]
    assert after.state is A.COMPLETED
    view = _view(st2, after)
    assert (view.state, view.hold_micro) == (RS.RECONCILED, 0)
    again = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert again.outcome is rc.Reconciled.ALREADY_SETTLED
    assert _view(st2, after) == view  # not counted twice


def test_a_report_of_a_definite_failure(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt, report = _started_with_report(st, "FAILED_DEFINITE")
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    result = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert result.outcome is rc.Reconciled.FAILED
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.FAILED_DEFINITE


@pytest.mark.parametrize(
    "tamper",
    [
        {"mac": "0" * 64},
        {"attempt_id": "OTHER"},
        {"receipt_id": "OTHER"},
        {"reservation_id": "OTHER"},
    ],
)
def test_invalid_reconciliation_evidence_is_refused(tmp_path: Path, tamper: dict[str, Any]) -> None:
    st = lw.setup(tmp_path)
    attempt, report = _started_with_report(st)
    other = _at(st, A.DISPATCH_STARTED)
    values = {
        "mac": tamper.get("mac", report.mac),
        "attempt_id": other.attempt_id if "attempt_id" in tamper else report.attempt_id,
        "receipt_id": other.receipt_id if "receipt_id" in tamper else report.receipt_id,
        "reservation_id": other.reservation_id
        if "reservation_id" in tamper
        else report.reservation_id,
    }
    forged = report.model_copy(update=values)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    with pytest.raises(rc.ReconciliationEvidenceError):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=forged))
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert _view(st2, attempt).state is RS.UNKNOWN


def test_without_evidence_it_stays_unknown(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.DISPATCH_STARTED)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert rc.reconcile(deps, attempt.attempt_id, None).outcome is rc.Reconciled.STILL_UNKNOWN
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_an_unresolved_external_effect_fails_the_run_and_keeps_the_hold(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.DISPATCH_STARTED)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    run = rc.declare_unresolved(deps, _run_of(st2, attempt).run_id)
    assert run.status is R.FAILED
    view = _view(st2, attempt)
    assert (view.state, view.hold_micro > 0) == (RS.UNKNOWN, True)
    statuses = [
        t.evidence.reason.value
        for e in st2.journal.read_run(run.run_id, after_run_seq=0, limit=1_000)
        if e.payload is not None
        for t in e.payload.transitions
        if isinstance(t, RunTransition)
        and t.after.status is R.FAILED
        and isinstance(t.evidence, Termination)
    ]
    assert statuses == ["UNRESOLVED_EXTERNAL_EFFECT"]


# ================================================================ stops, permits, approvals


def test_stops_survive_the_restart_and_the_recovery(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.BUDGET_RESERVED)
    run_id = _run_of(st, attempt).run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert any(e.scope_id == str(run_id) for e in st2.journal.stop_entries())
    assert st2.sm.state.runs[run_id].status is R.CANCELLED


def test_an_old_permit_cannot_be_consumed_after_the_restart(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _at(st, A.PERMIT_ISSUED)
    st2, _ = _restart(st)
    with pytest.raises(AgentCoreError):  # revoked at the new owner epoch
        lw.consume(st2, st2.sm.state.attempts[attempt.attempt_id])


def test_an_old_approval_is_not_used_again(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    pending = _at(st, A.APPROVAL_PENDING)
    approval_id = lw.record_approval(st, pending)
    lw.approved(st, pending, approval_id)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    # The same tool and arguments again, in a new Attempt: a new reservation, so a
    # new action hash. The old exact approval does not approve it.
    step = lw.step_in(st2)
    scopes = lw.scopes_of(st2.sm, step)
    lw.caps(st2, scopes)
    again = lw.fixed_attempt(
        st2, step, scopes, registration=s.SYNTH_CHANGE_TOOL, canonical_input=tw.SYNTHETIC_INPUT
    )
    waiting = st2.sm.advance_attempt(lw.allowed(st2, again), A.APPROVAL_PENDING)
    with pytest.raises(AgentCoreError):
        lw.approved(st2, waiting, approval_id)
    assert st2.sm.state.attempts[waiting.attempt_id].state is A.APPROVAL_PENDING


# ================================================================ deadlines and time


def _entered_recovering(st: lw.Setup) -> Any:
    attempt = _at(st, A.DISPATCH_STARTED)
    run = _run_of(st, attempt)
    return st.sm.set_run_status(run, R.RECOVERING), attempt


def _trigger(st: lw.Setup, run_id: Any) -> list[str]:
    return [
        t.evidence.reason.value
        for e in st.journal.read_run(run_id, after_run_seq=0, limit=1_000)
        if e.payload is not None
        for t in e.payload.transitions
        if isinstance(t, RunTransition) and isinstance(t.evidence, ReconciliationTrigger)
    ]


def test_a_recovery_past_its_deadline_is_escalated(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run, _ = _entered_recovering(st)
    st.clock.now += DEADLINES.recovering_ms + 1
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert st2.sm.state.runs[run.run_id].status is R.RECONCILIATION_REQUIRED
    assert _trigger(st2, run.run_id) == ["DEADLINE_EXCEEDED"]


def test_a_clock_going_back_does_not_extend_the_deadline(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run, _ = _entered_recovering(st)
    st.clock.now += DEADLINES.recovering_ms + 1
    w.task(st.sm)  # an event recorded at the later time
    st.clock.now -= 10 * DEADLINES.recovering_ms  # the wall clock goes back
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert _trigger(st2, run.run_id) == ["DEADLINE_EXCEEDED"]


def test_the_deadline_values_are_explicit() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        rc.RecoveryDeadlines(recovering_ms=0, cancelling_ms=1)


# ================================================================ Rebuild


def _events(st: lw.Setup) -> list[Any]:
    found: list[Any] = []
    while True:
        page = st.journal.read_tenant(b.TENANT, after_tenant_seq=len(found), limit=1_000)
        if not page:
            return found
        found.extend(page)


def test_rebuild_is_deterministic_and_writes_nothing(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.DISPATCH_STARTED)
    st.journal.close()
    before = list(sqlite3.connect(st.path).iterdump())
    ro = SqliteJournal.open_read_only(st.path, clock=st.clock)
    try:
        events = list(ro.read_tenant(b.TENANT, after_tenant_seq=0, limit=1_000))
        first = rebuild_with_head(events)
        second = rebuild_with_head(events)
        assert first == second
    finally:
        ro.close()
    assert list(sqlite3.connect(st.path).iterdump()) == before


@pytest.mark.parametrize("change", ["order", "version", "chain", "type"])
def test_rebuild_refuses_bad_events(tmp_path: Path, change: str) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.BUDGET_RESERVED)
    events = _events(st)
    if change == "order":
        events = [events[1], events[0], *events[2:]]
    elif change == "version":
        events[-1] = events[-1].model_copy(update={"schema_version": 99})
    elif change == "chain":
        events[-1] = events[-1].model_copy(update={"prev_hash": "sha256:" + "0" * 64})
    else:
        events[-1] = events[-1].model_copy(update={"type": "unknown.event"})
    with pytest.raises(AgentCoreError):
        rebuild_with_head(events)


# ================================================================ ProjectionRepair


def _open(st: lw.Setup) -> SqliteJournal:
    return SqliteJournal.open(st.path, clock=st.clock, random=OsRandomSource(), keys=st.keys)


def test_a_damaged_projection_is_rebuilt_from_the_journal(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.DISPATCH_STARTED)
    st.journal.close()
    conn = sqlite3.connect(st.path)
    conn.execute("DELETE FROM rt_attempts")
    conn.execute("UPDATE rt_runs SET status = 'COMPLETED'")
    conn.commit()
    conn.close()
    with pytest.raises(LedgerInconsistencyError):
        _open(st).close()
    repaired = SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert set(repaired) == {"rt_attempts", "rt_runs"}
    _open(st).close()  # opens again
    assert SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys) == ()


def test_repair_never_touches_a_cost_or_safety_record(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.DISPATCH_STARTED)
    st.journal.close()
    conn = sqlite3.connect(st.path)
    conn.execute("UPDATE reservations SET hold_micro = 0")  # damage to a source of truth
    conn.commit()
    before = list(conn.iterdump())
    conn.close()
    with pytest.raises(LedgerInconsistencyError):
        SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert list(sqlite3.connect(st.path).iterdump()) == before  # quarantined, unchanged


def test_repair_refuses_a_damaged_journal(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _at(st, A.BUDGET_RESERVED)
    st.journal.close()
    conn = sqlite3.connect(st.path)
    conn.execute("UPDATE events SET body = body || ' ' WHERE tenant_seq = 2")
    conn.commit()
    before = list(conn.iterdump())
    conn.close()
    with pytest.raises((JournalCorruptionError, AgentCoreError)):
        SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert list(sqlite3.connect(st.path).iterdump()) == before


# ================================================================ temporary files


class _DyingWrite:
    """The broker writes its temporary file, then the process dies (simulated)."""

    def __init__(self, real: Any, plant: Any) -> None:
        self._real = real
        self._plant = plant

    def prepare(self, descriptor: Any, policy: Any) -> Any:
        prepared = self._real.prepare(descriptor, policy)
        plant = self._plant

        class Prepared:
            def __getattr__(self, name: str) -> Any:
                return getattr(prepared, name)

            def write(self, *_args: Any) -> Any:
                plant()
                raise OSError("synthetic: the process died after the temporary file")

        return Prepared()


def _unknown_write(tmp_path: Path) -> tuple[Any, Any, Any, Path]:
    from agent_core.runtime.file_execution import run_file_operation
    from support import files_world as fw

    st, tree = fw.world(tmp_path)
    attempt = fw.consumed(st, tree, write=True)
    temp = (
        tree.root
        / "workspace"
        / "out"
        / (TEMP_FILE_PREFIX + temp_tag(str(attempt.receipt_id)) + "0" * 16)
    )

    def plant() -> None:
        temp.write_bytes(b"synthetic partial\n")
        temp.chmod(0o600)

    with pytest.raises(OSError, match="synthetic"):
        run_file_operation(
            gate=lw.gate(st),
            ledger=st.journal,
            files=_DyingWrite(fw.broker(st.keys.owner, tree), plant),
            registry=s.REGISTRY,
            attempt=attempt,
            write_content=fw.NEW_CONTENT.encode(),
            **lw.actual(st, attempt),
        )
    return st, tree, attempt, temp


def test_recovery_itself_never_removes_a_file(tmp_path: Path) -> None:
    st, _, attempt, temp = _unknown_write(tmp_path)
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    assert temp.exists()
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_the_explicit_temporary_recovery(tmp_path: Path) -> None:
    from support import files_world as fw

    st, tree, attempt, temp = _unknown_write(tmp_path)
    out = tree.root / "workspace" / "out"
    stranger = out / (TEMP_FILE_PREFIX + temp_tag("rcp_unknown") + "1" * 16)
    stranger.write_bytes(b"not ours to judge\n")
    stranger.chmod(0o600)
    target = out / "new.txt"
    st2, deps = _restart(st)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(deps))
    result = rc.recover_temporaries(
        deps, fw.broker(st2.keys.owner, tree), workspace_ref=tree.digest
    )
    assert result.listed_receipts == (str(attempt.receipt_id),)
    assert not temp.exists()  # the unknown write's own temporary file
    assert stranger.exists()  # a file no receipt accounts for: kept, reported
    assert len(result.kept) == 1
    assert not target.exists()  # the target is never touched or written
    after = st2.sm.state.attempts[attempt.attempt_id]
    assert after.state is A.OUTCOME_UNKNOWN  # nothing proven, nothing re-run
    assert _view(st2, after).hold_micro > 0
