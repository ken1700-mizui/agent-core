"""State-machine preconditions (WP-04), driven through the service with a synthetic journal.

All evidence is synthetic and accepted by a test-only verifier. Holding typed
evidence is not authority: production code has no verifier that accepts it.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_core.types.errors import (
    EvidenceRejectedError,
    IllegalTransitionError,
    ImmutableFieldError,
    StaleStateError,
    TransitionPreconditionError,
    UnknownEntityError,
)
from agent_core.types.ids import PermitId, ReceiptId, ReservationId, RunGrantId
from agent_core.types.records import (
    AttemptState,
    RunStatus,
    StepStatus,
    TaskStatus,
)
from agent_core.types.transitions import (
    AbortEvidence,
    AbortReason,
    ApplyPlanStatus,
    CriterionResult,
    ReconciledOutcome,
    ReconciliationEvidence,
    ReconciliationResolution,
    ReconciliationTrigger,
    ReconciliationTriggerReason,
    ResolutionDecision,
    Termination,
    TerminationReason,
    VerificationEvidence,
)
from support import builders as b
from support import runtime_world as w

A, R, S, T = AttemptState, RunStatus, StepStatus, TaskStatus


def _trigger() -> ReconciliationTrigger:
    return ReconciliationTrigger(
        kind="reconciliation_trigger",
        reason=ReconciliationTriggerReason.OUTCOME_UNKNOWN,
        evidence_ref=b.digest("trigger"),
    )


def _termination(reason: TerminationReason = TerminationReason.INTERNAL_ERROR) -> Termination:
    return Termination(kind="termination", reason=reason, evidence_ref=b.digest("why"))


# ---------------------------------------------------------------- attempt path


def test_path_without_approval_reaches_completed() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.dispatched_attempt(sm, step)
    done = sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    assert done.state is A.COMPLETED
    assert done.permit_id is not None
    assert done.receipt_id is not None
    assert done.approval_required is False


def test_path_without_approval_cannot_enter_approval_pending() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.drive(sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:3])
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, A.APPROVAL_PENDING, None)


def test_path_with_approval_cannot_skip_the_approval() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.drive(
        sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:3], approval_required=True
    )
    assert attempt.approval_required is True
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, A.BUDGET_RESERVED, w.evidence_for(attempt, A.BUDGET_RESERVED))
    pending = sm.advance_attempt(attempt, A.APPROVAL_PENDING, None)
    approved = sm.advance_attempt(pending, A.APPROVED, w.evidence_for(pending, A.APPROVED))
    reserved = sm.advance_attempt(
        approved, A.BUDGET_RESERVED, w.evidence_for(approved, A.BUDGET_RESERVED)
    )
    assert reserved.state is A.BUDGET_RESERVED


def test_approval_must_match_the_action_and_reservation() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.drive(
        sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:3], approval_required=True
    )
    pending = sm.advance_attempt(attempt, A.APPROVAL_PENDING, None)
    good = w.evidence_for(pending, A.APPROVED)
    for bad in (
        good.model_copy(update={"action_hash": b.digest("other-action")}),
        good.model_copy(update={"reservation_id": b.new_id(ReservationId)}),
    ):
        with pytest.raises(TransitionPreconditionError):
            sm.advance_attempt(pending, A.APPROVED, type(good).model_validate(bad.model_dump()))


@pytest.mark.parametrize(
    "target",
    [A.VALIDATED, A.CONTENT_FIXED, A.POLICY_ALLOWED],
)
def test_required_evidence_cannot_be_omitted(target: AttemptState) -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = sm.create_attempt(step)
    index = w.PATH_WITHOUT_APPROVAL.index(target)
    attempt = w.drive(sm, attempt, w.PATH_WITHOUT_APPROVAL[:index])
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, target, None)


def test_permit_stages_need_matching_permit_and_receipt() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.drive(sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:5])
    assert attempt.state is A.INTENT_RECORDED
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, A.PERMIT_ISSUED, None)
    issued = sm.advance_attempt(attempt, A.PERMIT_ISSUED, w.evidence_for(attempt, A.PERMIT_ISSUED))
    wrong_permit = w.evidence_for(issued, A.PERMIT_CONSUMED).model_copy(
        update={"permit_id": b.new_id(PermitId)}
    )
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(issued, A.PERMIT_CONSUMED, wrong_permit)
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(issued, A.PERMIT_CONSUMED, None)
    consumed = sm.advance_attempt(
        issued, A.PERMIT_CONSUMED, w.evidence_for(issued, A.PERMIT_CONSUMED)
    )
    wrong_receipt = w.evidence_for(consumed, A.DISPATCH_AUTHORIZED).model_copy(
        update={"receipt_id": b.new_id(ReceiptId)}
    )
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(consumed, A.DISPATCH_AUTHORIZED, wrong_receipt)


def test_wrong_kind_of_evidence_is_rejected() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = sm.create_attempt(step)
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.INTENT_RECORDED))


def test_evidence_rejected_by_the_verifier_stops_the_transition() -> None:
    journal_events_before: list[Any] = []
    sm = w.machine(verifier=w.RejectEvidence())
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = sm.create_attempt(step)
    journal_events_before.append(sm.state.attempts[attempt.attempt_id])
    with pytest.raises(EvidenceRejectedError):
        sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    assert sm.state.attempts[attempt.attempt_id] == journal_events_before[0]


def test_abort_after_consumption_needs_a_not_dispatched_report() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.drive(sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:7])
    assert attempt.state is A.PERMIT_CONSUMED
    without_report = AbortEvidence(
        kind="abort", reason=AbortReason.REVOKED, not_dispatched_ref=None
    )
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(attempt, A.ABORTED_BEFORE_DISPATCH, without_report)
    aborted = sm.advance_attempt(
        attempt, A.ABORTED_BEFORE_DISPATCH, w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH)
    )
    assert aborted.state is A.ABORTED_BEFORE_DISPATCH


def test_dispatched_attempt_cannot_be_aborted() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.dispatched_attempt(sm, step)
    with pytest.raises(IllegalTransitionError):
        sm.advance_attempt(
            attempt, A.ABORTED_BEFORE_DISPATCH, w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH)
        )


def test_outcome_unknown_needs_reconciliation_evidence_to_resolve() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.dispatched_attempt(sm, step)
    unknown = sm.advance_attempt(
        attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN)
    )
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(unknown, A.COMPLETED, w.evidence_for(unknown, A.COMPLETED))
    mismatched = ReconciliationEvidence(
        kind="reconciliation", evidence_ref=b.digest("rec"), outcome=ReconciledOutcome.FAILED
    )
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(unknown, A.COMPLETED, mismatched)
    resolved = sm.advance_attempt(
        unknown,
        A.FAILED_DEFINITE,
        mismatched,
    )
    assert resolved.state is A.FAILED_DEFINITE


# ---------------------------------------------------------------- retries and attempts


def test_retry_uses_a_new_attempt_and_old_attempts_stay_final() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    first = w.dispatched_attempt(sm, step)
    failed = sm.advance_attempt(first, A.FAILED_DEFINITE, w.evidence_for(first, A.FAILED_DEFINITE))
    with pytest.raises(IllegalTransitionError):
        sm.advance_attempt(failed, A.VALIDATED, w.evidence_for(failed, A.VALIDATED))
    second = sm.create_attempt(step)
    assert second.attempt_id != first.attempt_id
    assert second.state is A.PROPOSED


def test_no_new_attempt_while_one_is_in_flight_or_unknown() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.dispatched_attempt(sm, step)
    with pytest.raises(TransitionPreconditionError):
        sm.create_attempt(step)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    # An unknown outcome is never retried blindly (A.I4).
    with pytest.raises(TransitionPreconditionError):
        sm.create_attempt(step)


def test_step_commit_needs_a_completed_attempt() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(step, S.COMMITTED)
    attempt = w.dispatched_attempt(sm, step)
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    committed = sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    assert committed.status is S.COMMITTED


def test_step_with_unknown_outcome_goes_to_reconciliation() -> None:
    sm = w.machine()
    step = w.step_in_progress(sm, w.running_run(sm))
    attempt = w.dispatched_attempt(sm, step)
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(step, S.RECONCILIATION_REQUIRED)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(step, S.FAILED)
    assert sm.set_step_status(step, S.RECONCILIATION_REQUIRED).status is S.RECONCILIATION_REQUIRED


# ---------------------------------------------------------------- run completion


def _completed_step_run(sm: Any) -> tuple[Any, Any]:
    the_task = w.task(sm)
    run = w.running_run(sm, the_task)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    return the_task, sm.state.runs[run.run_id]


def test_run_completes_only_through_verifying_with_passing_evidence() -> None:
    sm = w.machine()
    the_task, run = _completed_step_run(sm)
    with pytest.raises(IllegalTransitionError):
        sm.set_run_status(run, R.COMPLETED, w.passing_verification(the_task))
    verifying = sm.set_run_status(run, R.VERIFYING)
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(verifying, R.COMPLETED, None)
    done = sm.set_run_status(verifying, R.COMPLETED, w.passing_verification(the_task))
    assert done.status is R.COMPLETED


def test_failed_verification_cannot_complete_but_returns_to_running() -> None:
    sm = w.machine()
    the_task, run = _completed_step_run(sm)
    verifying = sm.set_run_status(run, R.VERIFYING)
    failing = VerificationEvidence(
        kind="verification",
        criteria=tuple(
            CriterionResult(criterion_ref=c, passed=False) for c in the_task.done_criteria
        ),
        artifact_hash=b.digest("artifact"),
        expected_artifact_hash=b.digest("artifact"),
        apply_plan=ApplyPlanStatus.NOT_REQUIRED,
        passed=False,
    )
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(verifying, R.COMPLETED, failing)
    assert sm.set_run_status(verifying, R.RUNNING, failing).status is R.RUNNING


def test_verification_must_cover_exactly_the_task_criteria() -> None:
    sm = w.machine()
    _, run = _completed_step_run(sm)
    verifying = sm.set_run_status(run, R.VERIFYING)
    other = VerificationEvidence(
        kind="verification",
        criteria=(CriterionResult(criterion_ref=b.digest("unrelated"), passed=True),),
        artifact_hash=b.digest("artifact"),
        expected_artifact_hash=b.digest("artifact"),
        apply_plan=ApplyPlanStatus.COMMITTED,
        passed=True,
    )
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(verifying, R.COMPLETED, other)


def test_verifying_requires_no_open_attempts_or_steps() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    w.dispatched_attempt(sm, step)
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(sm.state.runs[run.run_id], R.VERIFYING)


# ---------------------------------------------------------------- cancellation and late results


def test_cancel_with_late_result_is_recorded_but_does_not_advance() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    cancelling = sm.set_run_status(sm.state.runs[run.run_id], R.CANCELLING)
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(cancelling, R.CANCELLED)
    late = sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    assert late.state is A.COMPLETED
    assert sm.last_transition_was_late
    # The late result is not used to make progress: the step cannot be committed.
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    with pytest.raises(TransitionPreconditionError):
        sm.create_attempt(sm.state.steps[step.step_id])
    sm.set_step_status(sm.state.steps[step.step_id], S.CANCELLED)
    assert sm.set_run_status(cancelling, R.CANCELLED).status is R.CANCELLED


def test_unknown_outcome_blocks_cancelled_and_completed() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    cancelling = sm.set_run_status(sm.state.runs[run.run_id], R.CANCELLING)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(cancelling, R.CANCELLED)
    reconciling = sm.set_run_status(cancelling, R.RECONCILIATION_REQUIRED, _trigger())
    resume = ReconciliationResolution(
        kind="reconciliation_resolution",
        decision=ResolutionDecision.CANCEL,
        evidence_ref=b.digest("owner"),
    )
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(reconciling, R.CANCELLED, resume)
    failed = sm.set_run_status(
        reconciling, R.FAILED, _termination(TerminationReason.UNRESOLVED_EXTERNAL_EFFECT)
    )
    assert failed.status is R.FAILED


def test_reconciliation_trigger_for_unknown_needs_an_unknown_attempt() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(run, R.RECONCILIATION_REQUIRED, _trigger())


def test_failed_and_aborted_safety_need_a_termination_reason() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(run, R.FAILED, None)
    stopped = sm.set_run_status(run, R.ABORTED_SAFETY, _termination(TerminationReason.SAFETY_ABORT))
    for target in (R.FAILED, R.RUNNING, R.ABORTED_SAFETY):
        with pytest.raises(IllegalTransitionError):
            sm.set_run_status(stopped, target, _termination())


# ---------------------------------------------------------------- tasks and runs


def test_budget_and_caps_survive_new_runs_and_cannot_be_changed() -> None:
    sm = w.machine()
    the_task = w.task(sm)
    first = sm.start_run(the_task, run_grant_ref=b.new_id(RunGrantId))
    first = sm.set_run_status(first, R.FAILED, _termination())
    second = sm.start_run(sm.state.tasks[the_task.task_id], run_grant_ref=b.new_id(RunGrantId))
    after = sm.state.tasks[the_task.task_id]
    assert after.budget_total == the_task.budget_total
    assert after.caps_total == the_task.caps_total
    assert after.runs == (first.run_id, second.run_id)
    assert second.parent_run_id == first.run_id
    with pytest.raises(ImmutableFieldError):
        after.evolve(budget_total=b.task().budget_total.model_copy(update={"micro_units": 9}))
    with pytest.raises(ImmutableFieldError):
        after.evolve(caps_total=b.caps(model_calls=999))


def test_only_one_active_run_per_task() -> None:
    sm = w.machine()
    the_task = w.task(sm)
    sm.start_run(the_task, run_grant_ref=b.new_id(RunGrantId))
    with pytest.raises(TransitionPreconditionError):
        sm.start_run(sm.state.tasks[the_task.task_id], run_grant_ref=b.new_id(RunGrantId))


def test_task_completes_only_after_its_last_run_completed() -> None:
    sm = w.machine()
    the_task, run = _completed_step_run(sm)
    active = sm.state.tasks[the_task.task_id]
    with pytest.raises(TransitionPreconditionError):
        sm.set_task_status(active, T.COMPLETED)
    verifying = sm.set_run_status(run, R.VERIFYING)
    sm.set_run_status(verifying, R.COMPLETED, w.passing_verification(the_task))
    done = sm.set_task_status(sm.state.tasks[the_task.task_id], T.COMPLETED)
    assert done.status is T.COMPLETED
    with pytest.raises(IllegalTransitionError):
        sm.set_task_status(done, T.ACTIVE)
    with pytest.raises(TransitionPreconditionError):
        sm.start_run(done, run_grant_ref=b.new_id(RunGrantId))


def test_run_needs_an_active_task_to_run() -> None:
    sm = w.machine()
    the_task = w.task(sm)
    run = sm.start_run(the_task, run_grant_ref=b.new_id(RunGrantId))
    queued = sm.set_run_status(run, R.QUEUED)
    task_now = sm.state.tasks[the_task.task_id]
    with pytest.raises(TransitionPreconditionError):
        sm.set_task_status(task_now, T.PAUSED)
    with pytest.raises(TransitionPreconditionError):
        sm.set_task_status(task_now, T.CANCELLED)
    assert sm.set_run_status(queued, R.RUNNING).status is R.RUNNING


# ---------------------------------------------------------------- stale and double updates


def test_stale_snapshot_and_double_update_are_rejected() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    waiting = sm.set_run_status(run, R.WAITING_TOOL)
    with pytest.raises(StaleStateError):
        sm.set_run_status(run, R.WAITING_APPROVAL)
    sm.set_run_status(waiting, R.RUNNING)
    with pytest.raises(StaleStateError):
        sm.set_run_status(waiting, R.RUNNING)


def test_hand_made_snapshot_is_not_a_transition() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    forged = run.evolve(status=R.COMPLETED)
    with pytest.raises(StaleStateError):
        sm.set_run_status(forged, R.FAILED, _termination())


def test_unknown_entities_are_rejected() -> None:
    sm = w.machine()
    with pytest.raises(UnknownEntityError):
        sm.set_run_status(b.run(), R.QUEUED)
    with pytest.raises(UnknownEntityError):
        sm.create_attempt(b.step())
