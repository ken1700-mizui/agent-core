"""Typed transition records stored in runtime journal events (WP-04).

A runtime event (``type`` = ``RUNTIME_EVENT_TYPE``, ``schema_version`` 3)
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
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from agent_core.types.base import FrozenModel
from agent_core.types.criteria import CriterionCheck
from agent_core.types.ids import AttemptId, PermitId, ReceiptId, ReservationId
from agent_core.types.provenance import (
    AttemptProvenance,
    OutcomeKind,
    OutcomeProvenance,
    Provenance,
    RunProvenance,
    StepProvenance,
)
from agent_core.types.records import Attempt, AttemptState, Run, Step, Task
from agent_core.types.values import Digest, PolicyVersion

RUNTIME_EVENT_TYPE = "runtime.transition"
# The version new runtime events are written in (WP2-01: 4, with provenance).
# Version 2 is retired (see agent_core.types.events).
RUNTIME_EVENT_SCHEMA_VERSION = 4
# Runtime event versions that are read. 3 has no provenance; 4 may have it.
READABLE_RUNTIME_SCHEMA_VERSIONS = frozenset({3, 4})


class AbortReason(StrEnum):
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    CANCELLED = "CANCELLED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    STOPPED = "STOPPED"
    # WP-08: refused before the start by the file broker's preflight.
    WORKSPACE_REJECTED = "WORKSPACE_REJECTED"
    SAFE_FILE_ACCESS_UNAVAILABLE = "SAFE_FILE_ACCESS_UNAVAILABLE"
    WORKSPACE_BUSY = "WORKSPACE_BUSY"
    ARTIFACT_MISMATCH = "ARTIFACT_MISMATCH"


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
    # WP-15: a loop limit (repairs, calls, iterations, time) was reached.
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    # WP-15: only simple tasks are planned in Phase 1.
    PLANNING_UNSUPPORTED = "PLANNING_UNSUPPORTED"


class ReconciliationTriggerReason(StrEnum):
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    # WP-12: an interrupted Run whose transcript is gone (it is never continued).
    SESSION_LOST = "SESSION_LOST"


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
    # WP15-R06, R07: per criterion, what was expected, what the verification read
    # observed and which read. The completion transaction checks every one.
    checks: tuple[CriterionCheck, ...] = ()

    @property
    def verified_reads(self) -> tuple[AttemptId, ...]:
        return tuple(c.read_attempt_id for c in self.checks if c.read_attempt_id is not None)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        reads = self.verified_reads
        if len(set(reads)) != len(reads):
            raise ValueError("one read is the evidence of one criterion only")
        if len({c.criterion_ref for c in self.checks}) != len(self.checks):
            raise ValueError("one check per criterion")
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


def outcome_kind(transition: AttemptTransition) -> OutcomeKind | None:
    """The result stage of an attempt transition, or None when it is no result."""
    if transition.before is None or transition.before.state is transition.after.state:
        return None
    state = transition.after.state
    if state is AttemptState.ABORTED_BEFORE_DISPATCH:
        return OutcomeKind.ABORTED_BEFORE_DISPATCH
    if state is AttemptState.OUTCOME_UNKNOWN:
        return OutcomeKind.OUTCOME_UNKNOWN
    if state not in (AttemptState.COMPLETED, AttemptState.FAILED_DEFINITE):
        return None
    completed = state is AttemptState.COMPLETED
    if isinstance(transition.evidence, ReconciliationEvidence):
        return OutcomeKind.RECONCILED_COMPLETED if completed else OutcomeKind.RECONCILED_FAILED
    return OutcomeKind.COMPLETED if completed else OutcomeKind.FAILED_DEFINITE


def fixes_content(transition: AttemptTransition) -> bool:
    return (
        transition.before is not None
        and transition.before.state is not AttemptState.CONTENT_FIXED
        and transition.after.state is AttemptState.CONTENT_FIXED
    )


def _record_key(record: Provenance) -> tuple[str, str]:
    if isinstance(record, RunProvenance):
        return record.stage, str(record.run_id)
    if isinstance(record, StepProvenance):
        return record.stage, str(record.step_id)
    return record.stage, str(record.attempt_id)


def _matches(record: Provenance, transitions: tuple[Transition, ...]) -> bool:
    for t in transitions:
        if isinstance(record, RunProvenance):
            if (
                isinstance(t, RunTransition)
                and t.action is EntityAction.CREATE
                and t.after.run_id == record.run_id
            ):
                return True
        elif isinstance(record, StepProvenance):
            if (
                isinstance(t, StepTransition)
                and t.action is EntityAction.CREATE
                and t.after.step_id == record.step_id
            ):
                return True
        elif isinstance(t, AttemptTransition) and t.after.attempt_id == record.attempt_id:
            if isinstance(record, AttemptProvenance) and fixes_content(t):
                return True
            if isinstance(record, OutcomeProvenance):
                kind = outcome_kind(t)
                if kind is not None and kind is not record.result:
                    raise ValueError("an outcome record names the transition's result")
                if kind is not None:
                    return True
    return False


class RuntimePayload(FrozenModel):
    transitions: Annotated[tuple[Transition, ...], Field(min_length=1)]
    # WP2-01 (Journal v4): provenance of transitions of this payload. Empty in v3,
    # and then absent from the serialized form, so v3 bytes and hashes are unchanged.
    provenance: tuple[Provenance, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_empty_provenance(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if not self.provenance and isinstance(data, dict):
            data.pop("provenance", None)
        return data

    @model_validator(mode="after")
    def _records_match_transitions(self) -> Self:
        keys = [_record_key(r) for r in self.provenance]
        if len(set(keys)) != len(keys):
            raise ValueError("one provenance record per transition and stage, recorded once")
        for record in self.provenance:
            if not _matches(record, self.transitions):
                raise ValueError("a provenance record has no matching transition in its event")
        return self
