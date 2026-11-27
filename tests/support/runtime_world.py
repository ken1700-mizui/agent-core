"""Synthetic helpers for runtime state-machine tests (no real authority anywhere)."""

from __future__ import annotations

from typing import Any

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.ports.journal import JournalPort
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.types.errors import EvidenceRejectedError
from agent_core.types.ids import PermitId, ReceiptId, ReservationId, RunGrantId
from agent_core.types.profile import PHASE1_SYNTHETIC
from agent_core.types.records import Attempt, AttemptState, Run, RunStatus, Step, StepKind, Task
from agent_core.types.transitions import (
    AbortEvidence,
    AbortReason,
    ApplyPlanStatus,
    ApprovalEvidence,
    ContentFixedEvidence,
    CriterionResult,
    DispatchEvidence,
    IntentEvidence,
    OutcomeEvidence,
    PermitConsumedEvidence,
    PermitIssuedEvidence,
    PolicyDecisionEvidence,
    ReservationEvidence,
    ValidationEvidence,
    VerificationEvidence,
)
from support import builders as b
from support.fakes import InMemoryJournal


class AcceptSyntheticEvidence:
    """Test-only verifier: accepts every piece of synthetic evidence."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def verify(self, evidence: Any, *, subject: Any) -> None:
        self.seen.append((evidence, subject))


class RejectEvidence:
    def verify(self, evidence: Any, *, subject: Any) -> None:
        raise EvidenceRejectedError(f"synthetic rejection of {evidence.kind}")


def machine(journal: JournalPort | None = None, verifier: Any = None) -> RuntimeStateMachine:
    return RuntimeStateMachine.load(
        journal if journal is not None else InMemoryJournal(),
        ids=IdGenerator(b.SITE, SystemClock(), OsRandomSource()),
        clock=SystemClock(),
        verifier=verifier if verifier is not None else AcceptSyntheticEvidence(),
        tenant_id=b.TENANT,
        engine=b.ENGINE,
        policy_version="policy-1",
        profile=PHASE1_SYNTHETIC,
    )


def task(sm: RuntimeStateMachine, criteria: int = 1) -> Task:
    return sm.create_task(
        goal_ref=b.digest("goal"),
        done_criteria=tuple(sorted(b.digest(f"criterion-{i}") for i in range(criteria))),
        budget_total=b.task().budget_total,
        caps_total=b.caps(),
    )


def running_run(sm: RuntimeStateMachine, the_task: Task | None = None) -> Run:
    t = the_task if the_task is not None else task(sm)
    run = sm.start_run(t, run_grant_ref=b.new_id(RunGrantId))
    run = sm.set_run_status(run, RunStatus.QUEUED)
    return sm.set_run_status(run, RunStatus.RUNNING)


def step_in_progress(sm: RuntimeStateMachine, run: Run) -> Step:
    from agent_core.types.records import StepStatus

    step = sm.create_step(run, StepKind.TOOL_CALL)
    return sm.set_step_status(step, StepStatus.IN_PROGRESS)


ACTION = b.digest("action")


def _set[T](value: T | None) -> T:
    if value is None:
        raise AssertionError("the attempt has not reached this stage yet")
    return value


def evidence_for(attempt: Attempt, target: AttemptState, **extra: Any) -> Any:
    """Matching synthetic evidence for the next transition of ``attempt``."""
    action = attempt.action_hash or ACTION
    if target is AttemptState.VALIDATED:
        return ValidationEvidence(kind="validation", schema_ref=b.digest("schema"))
    if target is AttemptState.CONTENT_FIXED:
        return ContentFixedEvidence(
            kind="content_fixed",
            action_hash=ACTION,
            reservation_id=extra.get("reservation_id", b.new_id(ReservationId)),
        )
    if target is AttemptState.POLICY_ALLOWED:
        return PolicyDecisionEvidence(
            kind="policy_decision",
            decision_ref=b.digest("decision"),
            action_hash=action,
            policy_version="policy-1",
            approval_required=extra.get("approval_required", False),
        )
    if target is AttemptState.APPROVED:
        return ApprovalEvidence(
            kind="approval",
            approval_ref=b.digest("approval"),
            action_hash=action,
            reservation_id=_set(attempt.reservation_id),
        )
    if target is AttemptState.BUDGET_RESERVED:
        return ReservationEvidence(
            kind="reservation", reservation_id=_set(attempt.reservation_id), action_hash=action
        )
    if target is AttemptState.INTENT_RECORDED:
        return IntentEvidence(kind="intent", intent_ref=b.digest("intent"), action_hash=action)
    if target is AttemptState.PERMIT_ISSUED:
        return PermitIssuedEvidence(
            kind="permit_issued",
            permit_id=extra.get("permit_id", b.new_id(PermitId)),
            action_hash=action,
            reservation_id=_set(attempt.reservation_id),
        )
    if target is AttemptState.PERMIT_CONSUMED:
        return PermitConsumedEvidence(
            kind="permit_consumed",
            permit_id=_set(attempt.permit_id),
            receipt_id=extra.get("receipt_id", b.new_id(ReceiptId)),
        )
    if target in (AttemptState.DISPATCH_AUTHORIZED, AttemptState.DISPATCH_STARTED):
        return DispatchEvidence(
            kind="dispatch", receipt_id=_set(attempt.receipt_id), report_ref=b.digest("pep")
        )
    if target in (
        AttemptState.COMPLETED,
        AttemptState.FAILED_DEFINITE,
        AttemptState.OUTCOME_UNKNOWN,
    ):
        return OutcomeEvidence(
            kind="outcome", receipt_id=_set(attempt.receipt_id), report_ref=b.digest("result")
        )
    if target is AttemptState.ABORTED_BEFORE_DISPATCH:
        return AbortEvidence(
            kind="abort",
            reason=AbortReason.CANCELLED,
            not_dispatched_ref=extra.get("not_dispatched_ref", b.digest("not-sent")),
        )
    return None


PATH_WITHOUT_APPROVAL = (
    AttemptState.VALIDATED,
    AttemptState.CONTENT_FIXED,
    AttemptState.POLICY_ALLOWED,
    AttemptState.BUDGET_RESERVED,
    AttemptState.INTENT_RECORDED,
    AttemptState.PERMIT_ISSUED,
    AttemptState.PERMIT_CONSUMED,
    AttemptState.DISPATCH_AUTHORIZED,
    AttemptState.DISPATCH_STARTED,
)


def drive(
    sm: RuntimeStateMachine,
    attempt: Attempt,
    path: tuple[AttemptState, ...],
    **extra: Any,
) -> Attempt:
    for target in path:
        attempt = sm.advance_attempt(attempt, target, evidence_for(attempt, target, **extra))
    return attempt


def dispatched_attempt(sm: RuntimeStateMachine, step: Step) -> Attempt:
    return drive(sm, sm.create_attempt(step), PATH_WITHOUT_APPROVAL)


def passing_verification(the_task: Task) -> VerificationEvidence:
    artifact = b.digest("artifact")
    return VerificationEvidence(
        kind="verification",
        criteria=tuple(
            CriterionResult(criterion_ref=c, passed=True) for c in the_task.done_criteria
        ),
        artifact_hash=artifact,
        expected_artifact_hash=artifact,
        apply_plan=ApplyPlanStatus.NOT_REQUIRED,
        passed=True,
    )
