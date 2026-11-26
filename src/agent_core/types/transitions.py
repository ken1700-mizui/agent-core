"""Typed transition records stored in runtime journal events (WP-04).

A runtime event (``type`` = ``RUNTIME_EVENT_TYPE``, ``schema_version`` 2)
carries a ``RuntimePayload``: one or more transitions applied atomically. Each
transition names the snapshot before and after, the action, and the evidence
the transition required. Everything here is metadata (IDs, digests, enums,
booleans); no content body or secret is stored (E 15.1).

Evidence in WP-04 is synthetic. A typed evidence object is not authority: the
runtime asks an ``EvidenceVerifier`` port to confirm it, and Phase 1 has no
verifier that can confirm a real permit, approval or reservation until WP-09.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import PermitId, ReceiptId, ReservationId
from agent_core.types.records import Attempt, Run, Step, Task
from agent_core.types.values import Digest, PolicyVersion

RUNTIME_EVENT_TYPE = "runtime.transition"
RUNTIME_EVENT_SCHEMA_VERSION = 2


class AbortReason(StrEnum):
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    CANCELLED = "CANCELLED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    STOPPED = "STOPPED"


class ReconciledOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ApplyPlanStatus(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_REQUIRED = "NOT_REQUIRED"


class TerminationReason(StrEnum):
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNRESOLVED_EXTERNAL_EFFECT = "UNRESOLVED_EXTERNAL_EFFECT"
    SAFETY_ABORT = "SAFETY_ABORT"
    OWNER_CANCELLED = "OWNER_CANCELLED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ReconciliationTriggerReason(StrEnum):
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


class ResolutionDecision(StrEnum):
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    FAIL = "FAIL"


# ---------------------------------------------------------------- attempt evidence


class ValidationEvidence(FrozenModel):
    kind: Literal["validation"]
    schema_ref: Digest


class ContentFixedEvidence(FrozenModel):
    """Fixes the action hash and allocates the reservation ID (E 38.1, AUDIT-01)."""

    kind: Literal["content_fixed"]
    action_hash: Digest
    reservation_id: ReservationId


class PolicyDecisionEvidence(FrozenModel):
    kind: Literal["policy_decision"]
    decision_ref: Digest
    action_hash: Digest
    policy_version: PolicyVersion
    approval_required: bool


class ApprovalEvidence(FrozenModel):
    kind: Literal["approval"]
    approval_ref: Digest
    action_hash: Digest
    reservation_id: ReservationId


class ReservationEvidence(FrozenModel):
    kind: Literal["reservation"]
    reservation_id: ReservationId
    action_hash: Digest


class IntentEvidence(FrozenModel):
    kind: Literal["intent"]
    intent_ref: Digest
    action_hash: Digest


class PermitIssuedEvidence(FrozenModel):
    kind: Literal["permit_issued"]
    permit_id: PermitId
    action_hash: Digest
    reservation_id: ReservationId


class PermitConsumedEvidence(FrozenModel):
    kind: Literal["permit_consumed"]
    permit_id: PermitId
    receipt_id: ReceiptId


class DispatchEvidence(FrozenModel):
    kind: Literal["dispatch"]
    receipt_id: ReceiptId
    report_ref: Digest


class OutcomeEvidence(FrozenModel):
    kind: Literal["outcome"]
    receipt_id: ReceiptId
    report_ref: Digest


class AbortEvidence(FrozenModel):
    """``not_dispatched_ref`` is required once a permit was consumed: a live PEP
    must have reported that nothing was sent (E 4.1)."""

    kind: Literal["abort"]
    reason: AbortReason
    not_dispatched_ref: Digest | None


class ReconciliationEvidence(FrozenModel):
    """Resolves OUTCOME_UNKNOWN from external records (WP-12 provides it)."""

    kind: Literal["reconciliation"]
    evidence_ref: Digest
    outcome: ReconciledOutcome


AttemptEvidence = Annotated[
    ValidationEvidence
    | ContentFixedEvidence
    | PolicyDecisionEvidence
    | ApprovalEvidence
    | ReservationEvidence
    | IntentEvidence
    | PermitIssuedEvidence
    | PermitConsumedEvidence
    | DispatchEvidence
    | OutcomeEvidence
    | AbortEvidence
    | ReconciliationEvidence,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------- run evidence


class CriterionResult(FrozenModel):
    criterion_ref: Digest
    passed: bool


class VerificationEvidence(FrozenModel):
    """Result of the VERIFYING stage (E 7.3, 29.5). ``passed`` must agree with the parts."""

    kind: Literal["verification"]
    criteria: tuple[CriterionResult, ...]
    artifact_hash: Digest
    expected_artifact_hash: Digest
    apply_plan: ApplyPlanStatus
    passed: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        derived = bool(self.criteria) and all(c.passed for c in self.criteria)
        derived = derived and self.artifact_hash == self.expected_artifact_hash
        if self.passed != derived:
            raise ValueError("passed must equal the criteria and artifact results")
        return self


class ReconciliationTrigger(FrozenModel):
    kind: Literal["reconciliation_trigger"]
    reason: ReconciliationTriggerReason
    evidence_ref: Digest


class ReconciliationResolution(FrozenModel):
    kind: Literal["reconciliation_resolution"]
    decision: ResolutionDecision
    evidence_ref: Digest


class Termination(FrozenModel):
    kind: Literal["termination"]
    reason: TerminationReason
    evidence_ref: Digest


RunEvidence = Annotated[
    VerificationEvidence | ReconciliationTrigger | ReconciliationResolution | Termination,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------- transitions


class TaskAction(StrEnum):
    CREATE = "CREATE"
    SET_STATUS = "SET_STATUS"
    ATTACH_RUN = "ATTACH_RUN"


class EntityAction(StrEnum):
    CREATE = "CREATE"
    SET_STATUS = "SET_STATUS"


class _Transition(FrozenModel):
    @model_validator(mode="after")
    def _create_has_no_before(self) -> Self:
        action = getattr(self, "action")  # noqa: B009 (defined by subclasses)
        before = getattr(self, "before")  # noqa: B009
        if (action == "CREATE") != (before is None):
            raise ValueError("only CREATE has no 'before' snapshot")
        return self


class TaskTransition(_Transition):
    entity: Literal["task"]
    action: TaskAction
    before: Task | None
    after: Task


class RunTransition(_Transition):
    entity: Literal["run"]
    action: EntityAction
    before: Run | None
    after: Run
    evidence: RunEvidence | None


class StepTransition(_Transition):
    entity: Literal["step"]
    action: EntityAction
    before: Step | None
    after: Step


class AttemptTransition(_Transition):
    entity: Literal["attempt"]
    action: EntityAction
    before: Attempt | None
    after: Attempt
    evidence: AttemptEvidence | None
    # A result recorded while its Run is CANCELLING (A 6, LATE_RESULT): kept for
    # the record and for resolving the attempt, never used to advance the Run.
    late: bool


Transition = Annotated[
    TaskTransition | RunTransition | StepTransition | AttemptTransition,
    Field(discriminator="entity"),
]


class RuntimePayload(FrozenModel):
    transitions: Annotated[tuple[Transition, ...], Field(min_length=1)]
