"""Common records (E_INTEGRATION 8): Task, Run, Step, Attempt, EngineRequest.

Event and EventDraft live in ``agent_core.types.events``.

Field names follow E_INTEGRATION 8. Statuses follow A 6 (Run) and E 3.1
(Attempt). Task and Step statuses and Step kinds are not fixed by the design;
the enums below are PROVISIONAL (WP-02) and are confirmed by the state
machine work package (WP-04).

These are data only. They do not run transitions, persist anything, or grant
authority: an EngineRequest is a request, never a permission.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Self

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from agent_core.types.base import ImmutableRecord, StateSnapshot
from agent_core.types.ids import (
    AttemptId,
    PermitId,
    ReceiptId,
    RequestId,
    ReservationId,
    RunGrantId,
    RunId,
    StepId,
    TaskId,
    TenantId,
)
from agent_core.types.values import (
    Budget,
    Digest,
    EngineVersion,
    Epoch,
    Identifier,
    Label,
    NonMonetaryCaps,
    PolicyVersion,
    Seq,
    require_sorted_unique,
    require_unique,
)

# ---------------------------------------------------------------- statuses


class TaskStatus(StrEnum):
    """PROVISIONAL (not fixed by the design; confirmed in WP-04)."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    """Run states (A 6)."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED_BUDGET = "PAUSED_BUDGET"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    CANCELLING = "CANCELLING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ABORTED_SAFETY = "ABORTED_SAFETY"


TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.ABORTED_SAFETY}
)


class StepKind(StrEnum):
    """PROVISIONAL. The Phase 1 loop: propose (model), execute (tool), verify (E 7.3)."""

    MODEL_CALL = "MODEL_CALL"
    TOOL_CALL = "TOOL_CALL"
    VERIFICATION = "VERIFICATION"


class StepStatus(StrEnum):
    """PROVISIONAL. Per-attempt detail lives in AttemptState (A 15 maps A 3.1 to E 3.1)."""

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class AttemptState(StrEnum):
    """Attempt states (E 3.1), in design order."""

    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    CONTENT_FIXED = "CONTENT_FIXED"
    POLICY_ALLOWED = "POLICY_ALLOWED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    BUDGET_RESERVED = "BUDGET_RESERVED"
    INTENT_RECORDED = "INTENT_RECORDED"
    PERMIT_ISSUED = "PERMIT_ISSUED"
    PERMIT_CONSUMED = "PERMIT_CONSUMED"
    DISPATCH_AUTHORIZED = "DISPATCH_AUTHORIZED"
    DISPATCH_STARTED = "DISPATCH_STARTED"
    COMPLETED = "COMPLETED"
    FAILED_DEFINITE = "FAILED_DEFINITE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"


# The content is not fixed yet, so there is no action hash.
_BEFORE_CONTENT_FIXED = frozenset({AttemptState.PROPOSED, AttemptState.VALIDATED})
# Reached only through PERMIT_ISSUED, so a permit must exist.
_PERMIT_REQUIRED = frozenset(
    {
        AttemptState.PERMIT_ISSUED,
        AttemptState.PERMIT_CONSUMED,
        AttemptState.DISPATCH_AUTHORIZED,
        AttemptState.DISPATCH_STARTED,
        AttemptState.COMPLETED,
        AttemptState.FAILED_DEFINITE,
        AttemptState.OUTCOME_UNKNOWN,
    }
)
_APPROVAL_STATES = frozenset({AttemptState.APPROVAL_PENDING, AttemptState.APPROVED})
# Reached only through PERMIT_CONSUMED, so a receipt must exist.
_RECEIPT_REQUIRED = _PERMIT_REQUIRED - {AttemptState.PERMIT_ISSUED}


def _invalid(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, message)


# ---------------------------------------------------------------- state snapshots


class Task(StateSnapshot):
    """A long-running unit of work (E 9). Budgets and caps apply to the whole Task."""

    # The Task-wide budget and caps never change: a new Run cannot reset them (E 9).
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"task_id", "goal_ref", "done_criteria", "budget_total", "caps_total"}
    )

    task_id: TaskId
    goal_ref: Digest
    done_criteria: Annotated[tuple[Digest, ...], Field(min_length=1)]
    budget_total: Budget
    caps_total: NonMonetaryCaps
    status: TaskStatus
    runs: tuple[RunId, ...]

    @field_validator("done_criteria", "runs")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_unique(value, "items")
        return value


class Run(StateSnapshot):
    """A finite section of a Task (E 9, A 6)."""

    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"run_id", "task_id", "run_grant_ref", "parent_run_id"}
    )

    run_id: RunId
    task_id: TaskId
    run_grant_ref: RunGrantId
    status: RunStatus
    owner_epoch: Epoch
    parent_run_id: RunId | None

    @model_validator(mode="after")
    def _not_own_parent(self) -> Self:
        if self.parent_run_id == self.run_id:
            raise _invalid("own_parent", "a run cannot be its own parent")
        return self


class Step(StateSnapshot):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset({"step_id", "run_id", "run_seq", "kind"})

    step_id: StepId
    run_id: RunId
    run_seq: Seq
    kind: StepKind
    status: StepStatus


class Attempt(StateSnapshot):
    """One attempt to perform a Step's action (E 3.1).

    Record-level consistency only (which IDs must exist in which state).
    Whether a transition is allowed is WP-04; issuing permits and reserving
    budget is WP-09. Holding a ``reservation_id`` grants nothing: under
    AUDIT-01 the ID can be allocated before the ActionDescriptor is fixed.
    """

    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset({"attempt_id", "step_id"})
    WRITE_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"action_hash", "permit_id", "receipt_id", "reservation_id", "approval_required"}
    )

    attempt_id: AttemptId
    step_id: StepId
    state: AttemptState
    action_hash: Digest | None
    permit_id: PermitId | None
    receipt_id: ReceiptId | None
    reservation_id: ReservationId | None
    # Fixed by the policy decision at POLICY_ALLOWED (WP-04): whether this attempt
    # must pass APPROVAL_PENDING -> APPROVED. None until the decision.
    approval_required: bool | None

    @model_validator(mode="after")
    def _ids_match_state(self) -> Self:
        state = self.state
        if state in _APPROVAL_STATES and self.approval_required is not True:
            raise _invalid("approval_not_required", "approval states need approval_required")
        if (
            self.action_hash is None
            and state not in _BEFORE_CONTENT_FIXED
            and state is not AttemptState.ABORTED_BEFORE_DISPATCH
        ):
            raise _invalid("missing_action_hash", "action_hash is required after VALIDATED")
        if self.action_hash is not None and state in _BEFORE_CONTENT_FIXED:
            raise _invalid("early_action_hash", "action_hash is fixed at CONTENT_FIXED")
        if state in _PERMIT_REQUIRED and self.permit_id is None:
            raise _invalid("missing_permit", "permit_id is required from PERMIT_ISSUED")
        if (
            self.permit_id is not None
            and state not in _PERMIT_REQUIRED
            and state is not AttemptState.ABORTED_BEFORE_DISPATCH
        ):
            raise _invalid("early_permit", "permit_id cannot exist before PERMIT_ISSUED")
        if state in _RECEIPT_REQUIRED and self.receipt_id is None:
            raise _invalid("missing_receipt", "receipt_id is required from PERMIT_CONSUMED")
        if self.receipt_id is not None and self.permit_id is None:
            raise _invalid("receipt_without_permit", "a receipt requires a permit")
        if self.receipt_id is not None and state is AttemptState.PERMIT_ISSUED:
            raise _invalid("early_receipt", "receipt_id cannot exist before PERMIT_CONSUMED")
        return self


# ---------------------------------------------------------------- immutable records


class EngineRequest(ImmutableRecord):
    """A request from one Engine to another (E 8). It never carries authority."""

    request_id: RequestId
    kind: Identifier
    engine: EngineVersion
    run_id: RunId | None
    step_id: StepId | None
    attempt_id: AttemptId | None
    tenant_id: TenantId
    payload_ref: Digest
    labels: tuple[Label, ...]
    estimated_cost: Budget | None
    policy_version: PolicyVersion
    access_epoch: Epoch

    @field_validator("labels")
    @classmethod
    def _canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_sorted_unique(value, "labels")
        return value

    @model_validator(mode="after")
    def _id_chain(self) -> Self:
        if self.attempt_id is not None and self.step_id is None:
            raise _invalid("id_chain", "attempt_id requires step_id")
        if self.step_id is not None and self.run_id is None:
            raise _invalid("id_chain", "step_id requires run_id")
        return self
