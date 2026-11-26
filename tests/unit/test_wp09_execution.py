"""WP-09 limited fixes, stage 2: the in-process execution gate (WP09-R03), trusted
not-dispatched and usage reports (WP09-R04), stop, cancel and time limits
(WP09-R05). Synthetic callbacks only; no file operation (WP-08).

Each test reproduces a defect found in review; it failed before the fix."""

from __future__ import annotations

import inspect
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.types.cost import BudgetScopeType, ReservationState
from agent_core.types.errors import (
    CostError,
    IllegalTransitionError,
    OwnerAuthorizationError,
    PermitRejectedError,
    TransitionPreconditionError,
)
from agent_core.types.ids import TenantId
from agent_core.types.ledger import (
    LedgerBatch,
    MarkActive,
    ReleaseReservation,
    ReportUsage,
    ResumeScope,
    RevokeAccess,
    SetBudgetCap,
    SetRunWindow,
)
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.records import AttemptState, RunStatus, StepStatus, TaskStatus
from agent_core.types.safety import (
    NotDispatchedReport,
    ReceiptState,
    StopScopeType,
    StopStatus,
    ToolContent,
)
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s

A = AttemptState
R = ReservationState


@pytest.fixture
def st(tmp_path: Path) -> Any:
    setup = lw.setup(tmp_path)
    yield setup
    if not setup.journal.closed:
        setup.journal.close()


def _usage(micro: int | None, tokens: tuple[int | None, int | None] = (0, 0)) -> Usage:
    known = None not in tokens
    return Usage(
        status=UsageStatus.FINAL if known else UsageStatus.UNKNOWN,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        total_tokens=(tokens[0] + tokens[1]) if known else None,  # type: ignore[operator]
        cost=None if micro is None else lw.xtu(micro),
        cost_status=CostStatus.UNKNOWN if micro is None else CostStatus.FINAL,
        synthetic=True,
    )


def _consumed(st: Any, **kw: Any) -> Any:
    return lw.consume(st, lw.issued(st, lw.step_in(st), **kw))


def _ok(_authorization: Any) -> SyntheticResult:
    return SyntheticResult(outcome="COMPLETED", usage=_usage(0), final=True)


# ---------------------------------------------------------------- WP09-R03: the execution gate


def test_a_receipt_is_bound_to_the_current_owner(st: Any) -> None:
    consumed = _consumed(st)
    st2 = st.reopen()
    try:
        st2.pep = st.pep  # the old PEP ID presented again after the restart
        attempt = st2.sm.state.attempts[consumed.attempt_id]
        # Before: the claim succeeded with the old receipt and the old PEP ID.
        with pytest.raises(PermitRejectedError, match="STALE_OWNER"):
            lw.gate(st2).claim(attempt, **lw.actual(st2, attempt))
    finally:
        st2.journal.close()


def test_a_missing_receipt_record_refuses_the_start(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    attempt = gate.claim(attempt, **lw.actual(st, attempt))
    conn = sqlite3.connect(st.path)
    conn.execute("DELETE FROM receipts WHERE receipt_id = ?", (str(attempt.receipt_id),))
    conn.commit()
    conn.close()
    # Before: _use_receipt returned without a receipt and the start went ahead.
    with pytest.raises(PermitRejectedError, match="UNKNOWN_RECEIPT"):
        gate.start(attempt)
    assert st.journal.reservation(attempt.reservation_id).state is R.CONSUMED


def test_one_receipt_starts_execution_once(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    calls: list[Any] = []

    def run(authorization: Any) -> SyntheticResult:
        calls.append(authorization)
        return _ok(authorization)

    done = gate.execute(attempt, run=run, **lw.actual(st, attempt))
    assert done.state is A.COMPLETED
    with pytest.raises((PermitRejectedError, IllegalTransitionError)):
        gate.execute(st.sm.state.attempts[attempt.attempt_id], run=run, **lw.actual(st, attempt))
    assert len(calls) == 1
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED


def test_a_confirmed_authorization_cannot_be_presented_again(st: Any) -> None:
    # Before: verify_dispatch confirmed by reading only; its result could be kept.
    assert not hasattr(st.journal, "verify_dispatch")
    assert "authorization" not in inspect.signature(SyntheticExecutionGate.execute).parameters
    assert "authorization" not in inspect.signature(SyntheticExecutionGate.start).parameters


def test_the_actual_operation_must_match_at_execution(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    other = ToolContent(kind="tool", canonical_input='{"path":"workspace/other.txt"}')
    calls: list[Any] = []

    def run(authorization: Any) -> SyntheticResult:
        calls.append(authorization)
        return _ok(authorization)

    with pytest.raises(PermitRejectedError, match="DESCRIPTOR_NOT_FROM_CONTENT"):
        gate.execute(attempt, content=other, registration=s.READ_TOOL, run=run)
    assert calls == []
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.ISSUED


def test_a_stop_right_after_the_claim_prevents_the_start(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    attempt = gate.claim(attempt, **lw.actual(st, attempt))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    gate.stop(st.signed(lw.stop_op(StopScopeType.RUN, str(run_id))))
    with pytest.raises(PermitRejectedError):
        gate.start(attempt)
    # Not started: the live gate reports it, and only then is the hold released.
    aborted = gate.abort_not_dispatched(st.sm.state.attempts[attempt.attempt_id])
    assert aborted.state is A.ABORTED_BEFORE_DISPATCH
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro) == (R.RELEASED, 0)


def test_the_gate_cannot_be_reentered_from_the_callback(st: Any) -> None:
    """Start and stop go through one gate lock. The journal connection belongs to one
    thread, so another thread cannot use it at all (it fails closed); within the
    thread, a callback cannot start another execution or stop the scope."""
    gate = lw.gate(st)
    attempt = _consumed(st)
    seen: list[str] = []
    run_id = st.sm.state.steps[attempt.step_id].run_id

    def run(authorization: Any) -> SyntheticResult:
        for call in (
            lambda: gate.stop(st.signed(lw.stop_op(StopScopeType.RUN, str(run_id)))),
            lambda: gate.start(st.sm.state.attempts[attempt.attempt_id]),
        ):
            try:
                call()
            except PermitRejectedError as exc:
                seen.extend(exc.reasons)
        return _ok(authorization)

    gate.execute(attempt, run=run, **lw.actual(st, attempt))
    assert seen == ["GATE_BUSY", "GATE_BUSY"]
    assert threading.current_thread() is threading.main_thread()


# ---------------------------------------------------------------- WP09-R04: trusted reports


def test_an_arbitrary_digest_does_not_release_a_consumed_reservation(st: Any) -> None:
    attempt = _consumed(st)
    abort = w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH, not_dispatched_ref=b.digest("x"))
    op = ReleaseReservation(
        op="release",
        reservation_id=attempt.reservation_id,
        attempt_id=attempt.attempt_id,
        report=None,
    )
    # Before: any not_dispatched_ref released the consumed reservation.
    with pytest.raises((CostError, PermitRejectedError)):
        st.sm.advance_attempt(attempt, A.ABORTED_BEFORE_DISPATCH, abort, ledger=(op,))
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000


def test_a_forged_or_foreign_not_dispatched_report_is_refused(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    other = _consumed(st)
    genuine = gate.not_dispatched_report(attempt)
    forged = genuine.model_copy(update={"mac": "0" * 64})
    foreign = gate.not_dispatched_report(other)
    for report in (forged, foreign):
        with pytest.raises((PermitRejectedError, CostError)):
            lw.abort_with(st, attempt, report)
    assert st.journal.reservation(attempt.reservation_id).state is R.CONSUMED


def test_a_not_dispatched_report_is_used_once(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    report = gate.not_dispatched_report(attempt)
    lw.abort_with(st, attempt, report)
    assert st.journal.reservation(attempt.reservation_id).state is R.RELEASED
    with pytest.raises((PermitRejectedError, CostError, IllegalTransitionError)):
        lw.abort_with(st, st.sm.state.attempts[attempt.attempt_id], report)


def test_a_started_operation_is_never_reported_as_not_dispatched(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    attempt, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    with pytest.raises(PermitRejectedError, match="ALREADY_STARTED"):
        gate.not_dispatched_report(attempt)
    assert st.journal.reservation(attempt.reservation_id).state is R.ACTIVE


def test_usage_needs_a_trusted_execution_report(st: Any) -> None:
    # Before: ReportUsage carried a free Usage and released the hold.
    assert "usage" not in ReportUsage.model_fields
    gate = lw.gate(st)
    attempt = _consumed(st, costly=True)
    attempt, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    report = gate.execution_report(
        attempt, SyntheticResult(outcome="COMPLETED", usage=_usage(300), final=True)
    )
    forged = report.model_copy(update={"mac": "0" * 64})
    with pytest.raises(PermitRejectedError, match="REPORT_MAC_INVALID"):
        lw.report_with(st, attempt, forged)
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000


def test_the_cost_comes_from_the_synthetic_billing(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st, costly=True)
    attempt, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    # The caller states 1 micro XTU; the trusted basis says 300.
    with pytest.raises(PermitRejectedError, match="USAGE_COST_MISMATCH"):
        gate.finish(attempt, SyntheticResult(outcome="COMPLETED", usage=_usage(1), final=True))
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000
    gate.finish(attempt, SyntheticResult(outcome="COMPLETED", usage=_usage(None), final=True))
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro, view.spent_micro) == (R.USAGE_REPORTED, 0, 300)


def test_without_complete_billing_the_hold_stays(tmp_path: Path) -> None:
    st = lw.setup(tmp_path, final_usage_is_complete=False)
    try:
        step = lw.step_in(st)
        scopes = lw.scopes_of(st.sm, step)
        lw.caps(st, scopes)
        attempt = lw.fixed_attempt(st, step, scopes, model=True)
        attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 5_000))))
        gate = lw.gate(st)
        attempt, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
        gate.finish(
            attempt, SyntheticResult(outcome="COMPLETED", usage=_usage(None, (10, 5)), final=True)
        )
        view = st.journal.reservation(lw.rsv(attempt))
        assert view is not None
        assert (view.state, view.hold_micro) == (R.UNKNOWN, 5_000)
    finally:
        st.journal.close()


def test_run_and_task_cannot_complete_with_unsettled_costs(st: Any) -> None:
    the_task = w.task(st.sm)
    run = w.running_run(st.sm, the_task)
    step = w.step_in_progress(st.sm, run)
    attempt = lw.issued(st, step)
    attempt = lw.consume(st, attempt)
    gate = lw.gate(st)
    attempt, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    # The attempt result arrives without a usage report: the cost is unsettled.
    done = st.sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    verifying = st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.VERIFYING)
    with pytest.raises(CostError, match="UNSETTLED_COST"):
        st.sm.set_run_status(verifying, RunStatus.COMPLETED, w.passing_verification(the_task))
    lw.report_with(st, done, gate.execution_report(done, _ok(None)))
    st.sm.set_run_status(
        st.sm.state.runs[run.run_id], RunStatus.COMPLETED, w.passing_verification(the_task)
    )
    assert st.sm.set_task_status(st.sm.state.tasks[the_task.task_id], TaskStatus.COMPLETED)


# ---------------------------------------------------------------- WP09-R05: stop, cancel, time


def test_cancelling_a_run_stops_it(st: Any) -> None:
    attempt = lw.issued(st, lw.step_in(st))
    run = st.sm.state.runs[st.sm.state.steps[attempt.step_id].run_id]
    st.sm.set_run_status(run, RunStatus.CANCELLING)
    entries = {(e.scope_type, e.scope_id): e for e in st.journal.stop_entries()}
    # Before: CANCELLING did not stop the run; the permit could still be consumed.
    assert entries[(StopScopeType.RUN, str(run.run_id))].status is StopStatus.STOPPED
    # The runtime refuses progress in a cancelling run, and the ledger on its own.
    with pytest.raises((PermitRejectedError, TransitionPreconditionError)):
        lw.consume(st, attempt)
    with pytest.raises(PermitRejectedError) as excinfo:
        lw.raw_apply(st, lw.consume_op(st, attempt))
    assert {"STOPPED", "RUN_CANCELLED"} <= set(excinfo.value.reasons)
    seq = entries[(StopScopeType.RUN, str(run.run_id))].stop_seq
    with pytest.raises(PermitRejectedError, match="RUN_CANCELLED"):
        st.owner(
            ResumeScope(
                op="resume_scope",
                scope_type=StopScopeType.RUN,
                scope_id=str(run.run_id),
                stop_seq=seq,
            )
        )


def test_no_execution_starts_after_cancel(st: Any) -> None:
    gate = lw.gate(st)
    attempt = _consumed(st)
    attempt = gate.claim(attempt, **lw.actual(st, attempt))
    run = st.sm.state.runs[st.sm.state.steps[attempt.step_id].run_id]
    st.sm.set_run_status(run, RunStatus.CANCELLING)
    attempt = st.sm.state.attempts[attempt.attempt_id]
    with pytest.raises((PermitRejectedError, TransitionPreconditionError)):
        gate.start(attempt)
    start = MarkActive(
        op="mark_active",
        reservation_id=attempt.reservation_id,
        attempt_id=attempt.attempt_id,
        receipt_id=attempt.receipt_id,
        pep_instance_id=st.pep,
    )
    with pytest.raises(PermitRejectedError) as excinfo:
        lw.raw_apply(st, start)
    assert "RUN_CANCELLED" in excinfo.value.reasons


def test_lowering_a_budget_cap_revokes_outstanding_permits(st: Any) -> None:
    attempt = lw.issued(st, lw.step_in(st))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=BudgetScopeType.RUN,
            scope_id=str(run_id),
            cap=lw.xtu(500),
        )
    )
    # Before: the permit stayed valid under the lowered cap.
    with pytest.raises(PermitRejectedError):
        lw.consume(st, attempt)
    assert st.journal.permit_state(attempt.permit_id).value == "REVOKED"


def test_an_expired_run_window_refuses_consumption(st: Any) -> None:
    attempt = lw.issued(st, lw.step_in(st))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(SetRunWindow(op="set_run_window", run_id=run_id, max_elapsed_ms=1))
    st.clock.now += 2
    # Before: the window was checked only when reserving.
    with pytest.raises(PermitRejectedError, match="ELAPSED"):
        lw.consume(st, attempt)


def test_a_clock_moving_back_does_not_extend_a_permit(st: Any) -> None:
    attempt = lw.issued(st, lw.step_in(st))  # expires 30 s after NOW
    st.clock.now += 31_000
    st.owner(RevokeAccess(op="revoke_access", tenant_id=b.new_id(TenantId)))  # any write
    st.clock.now -= 31_000
    # Before: the permit looked valid again once the wall clock went back.
    with pytest.raises(PermitRejectedError, match="EXPIRED"):
        lw.consume(st, attempt)


def test_approval_and_receipt_expiry_both_apply_at_start(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    approval_id = lw.record_approval(st, attempt, expires_at_ms=s.NOW + 20_000)
    attempt = lw.approved(st, attempt, approval_id)
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 1_000))))
    gate = lw.gate(st)
    attempt = gate.claim(attempt, **lw.actual(st, attempt))
    st.clock.now += 21_000
    with pytest.raises(PermitRejectedError, match="APPROVAL_INVALID"):
        gate.start(attempt)
    st.clock.now += 10_000  # also past the receipt
    with pytest.raises(PermitRejectedError, match="EXPIRED"):
        gate.start(attempt)


def test_an_old_owner_batch_cannot_be_replayed(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    old = st.signed(
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=BudgetScopeType.RUN,
            scope_id=str(scopes.run_id),
            cap=lw.xtu(10_000),
        )
    )
    st.journal.apply_owner_ledger(old[0], owner_mac=old[1])
    st.owner(
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=BudgetScopeType.RUN,
            scope_id=str(scopes.run_id),
            cap=lw.xtu(100),
        )
    )
    # Before: the same MAC-signed batch could be sent again and restore the old cap.
    with pytest.raises(OwnerAuthorizationError, match="REPLAYED"):
        st.journal.apply_owner_ledger(old[0], owner_mac=old[1])
    assert st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id)).cap_micro == 100
    assert "owner_seq" in LedgerBatch.model_fields


def test_a_restart_does_not_lift_a_revocation(st: Any) -> None:
    attempt = lw.issued(st, lw.step_in(st))
    st.owner(RevokeAccess(op="revoke_access", tenant_id=b.TENANT))
    st2 = st.reopen()
    try:
        with pytest.raises(PermitRejectedError):
            lw.consume(st2, st2.sm.state.attempts[attempt.attempt_id])
    finally:
        st2.journal.close()


def test_the_report_type_binds_the_receipt_permit_attempt_and_owner() -> None:
    fields = set(NotDispatchedReport.model_fields)
    assert {
        "receipt_id",
        "permit_id",
        "attempt_id",
        "owner_epoch",
        "pep_instance_id",
        "mac",
    } <= fields
