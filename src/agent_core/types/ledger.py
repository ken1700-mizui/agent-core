"""Ledger operations (WP-09B, WP-09C): typed commands the SQLite ledger applies
inside the Journal's ``BEGIN IMMEDIATE`` transaction.

An operation is data: it carries IDs, amounts and estimates, never authority.
The ledger re-checks every condition against its own rows in the same
transaction. Times come from the ledger's trusted clock, never from an operation.

Pairing with runtime transitions (enforced by the SQLite journal):

- Every authorizing Attempt transition must carry its operation, for the same
  Attempt: ``CONTENT_FIXED`` (allocate the reservation ID), ``POLICY_ALLOWED``
  (record the PDP decision), ``APPROVED`` (confirm the approval),
  ``BUDGET_RESERVED`` (activate), ``INTENT_RECORDED`` (record the intent),
  ``PERMIT_ISSUED`` (issue), ``PERMIT_CONSUMED`` (consume),
  ``DISPATCH_AUTHORIZED`` (claim the receipt) and ``DISPATCH_STARTED`` (mark
  active). Operations bound to a transition are refused without it.
- Owner operations (policy, grants, caps, counters, run windows, stop and
  resume, revocation, deny list) are accepted only through the owner path, with
  the owner channel's MAC.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from agent_core.types.base import FrozenModel
from agent_core.types.cost import (
    BudgetScopeType,
    CostEstimate,
    CounterKind,
    ReservationScopes,
)
from agent_core.types.ids import (
    ApprovalId,
    AttemptId,
    PepInstanceId,
    PermitId,
    ReceiptId,
    ReservationId,
    RunId,
    TenantId,
)
from agent_core.types.model import Usage
from agent_core.types.safety import (
    ActionDescriptor,
    ActionKind,
    Consumer,
    Permit,
    Policy,
    Receipt,
    RelPath,
    RunGrant,
    SignedApproval,
    StopScopeType,
)
from agent_core.types.tools import ToolRegistration
from agent_core.types.values import MAX_INT64, Budget, Count, Digest, Identifier

PositiveMicro = Annotated[int, Field(strict=True, ge=1, le=MAX_INT64)]
TtlMs = Annotated[int, Field(strict=True, ge=1, le=86_400_000)]


# ---------------------------------------------------------------- bound to a transition


class AllocateReservation(FrozenModel):
    """``CONTENT_FIXED``: allocate the ID only (no hold, no authority; E 38.1)."""

    op: Literal["allocate"]
    reservation_id: ReservationId
    attempt_id: AttemptId
    scopes: ReservationScopes
    action_kind: ActionKind
    ttl_ms: TtlMs


class ActivateReservation(FrozenModel):
    """``BUDGET_RESERVED``: the one ``ID_ALLOCATED -> RESERVED`` step (E 38.1).
    The ledger recomputes the required amount from ``estimate``."""

    op: Literal["activate"]
    reservation_id: ReservationId
    attempt_id: AttemptId
    action_hash: Digest
    estimate: CostEstimate
    amount: Budget
    cost_cap: Budget


class MarkActive(FrozenModel):
    """``DISPATCH_STARTED``: ``CONSUMED -> ACTIVE``."""

    op: Literal["mark_active"]
    reservation_id: ReservationId
    attempt_id: AttemptId


class ReleaseReservation(FrozenModel):
    """``ABORTED_BEFORE_DISPATCH``. From ``CONSUMED`` only with a live PEP's
    not-dispatched report (E.Z6)."""

    op: Literal["release"]
    reservation_id: ReservationId
    attempt_id: AttemptId
    not_dispatched_ref: Digest | None


class RecordDecision(FrozenModel):
    """``POLICY_ALLOWED``: the ledger re-runs the PDP inside the transaction with the
    installed policy and Run grant and the current state, and stores the
    descriptor. ``registration`` is the trusted registry entry (tool calls)."""

    op: Literal["record_decision"]
    attempt_id: AttemptId
    descriptor: ActionDescriptor
    registration: ToolRegistration | None
    # What the transition's evidence states; must equal the ledger's own decision.
    approval_required: bool


class ConfirmApproval(FrozenModel):
    """``APPROVED``: a recorded, valid ExactApproval for this attempt's action."""

    op: Literal["confirm_approval"]
    attempt_id: AttemptId
    approval_id: ApprovalId


class RecordIntent(FrozenModel):
    """``INTENT_RECORDED``: needs the attempt's reservation to be RESERVED."""

    op: Literal["record_intent"]
    attempt_id: AttemptId
    reservation_id: ReservationId
    action_hash: Digest
    intent_ref: Digest


class IssuePermit(FrozenModel):
    """``PERMIT_ISSUED``: every condition of C1 7.4 and E 38.2 is checked again."""

    op: Literal["issue_permit"]
    attempt_id: AttemptId
    permit_id: PermitId
    consumer: Consumer
    ttl_ms: Annotated[int, Field(strict=True, ge=1, le=600_000)]


class ConsumePermit(FrozenModel):
    """``PERMIT_CONSUMED``: the atomic consumption (C1 7.5, E 3.3)."""

    op: Literal["consume_permit"]
    permit: Permit
    consumer: Consumer
    actual_action_hash: Digest
    receipt_id: ReceiptId
    pep_instance_id: PepInstanceId
    receipt_ttl_ms: Annotated[int, Field(strict=True, ge=1, le=600_000)]


class ClaimReceipt(FrozenModel):
    """``DISPATCH_AUTHORIZED``: the receipt goes ``ISSUED -> CLAIMED`` once (E 3.2)."""

    op: Literal["claim_receipt"]
    receipt: Receipt
    pep_instance_id: PepInstanceId
    actual_action_hash: Digest


# ---------------------------------------------------------------- standalone


class ReportUsage(FrozenModel):
    """This call's usage (never a replay's source usage). Deduplicated by
    ``(reservation, usage_event_id)``."""

    op: Literal["report_usage"]
    reservation_id: ReservationId
    attempt_id: AttemptId
    usage_event_id: Identifier
    usage: Usage
    final: bool


class MarkUnknown(FrozenModel):
    op: Literal["mark_unknown"]
    reservation_id: ReservationId
    attempt_id: AttemptId


class Reconcile(FrozenModel):
    op: Literal["reconcile"]
    reservation_id: ReservationId
    actual: Budget
    evidence_ref: Digest


class Correct(FrozenModel):
    """A positive correction only. Lowering a recorded cost needs the owner and is
    not implemented (refused by type)."""

    op: Literal["correct"]
    reservation_id: ReservationId
    delta_micro: PositiveMicro
    refers_to_entry: Count
    evidence_ref: Digest


class Unexplained(FrozenModel):
    op: Literal["unexplained"]
    reservation_id: ReservationId
    delta_micro: PositiveMicro
    evidence_ref: Digest


class VoidAllocation(FrozenModel):
    op: Literal["void"]
    reservation_id: ReservationId


class RecordApproval(FrozenModel):
    """An approval from the owner channel. Its MAC is checked with the owner key."""

    op: Literal["record_approval"]
    signed: SignedApproval


class RecoverInFlight(FrozenModel):
    """After a restart: ``CONSUMED`` and ``ACTIVE`` become ``UNKNOWN`` (hold kept)."""

    op: Literal["recover_in_flight"]


# ---------------------------------------------------------------- owner only


class SetBudgetCap(FrozenModel):
    op: Literal["set_budget_cap"]
    scope_type: BudgetScopeType
    scope_id: Annotated[str, Field(min_length=1, max_length=128)]
    cap: Budget


class SetCounterCap(FrozenModel):
    op: Literal["set_counter_cap"]
    scope_type: Literal[BudgetScopeType.RUN, BudgetScopeType.TASK]
    scope_id: Annotated[str, Field(min_length=1, max_length=128)]
    counter: CounterKind
    cap: Count


class SetRunWindow(FrozenModel):
    """Elapsed-time limit of a Run, from now (ledger clock)."""

    op: Literal["set_run_window"]
    run_id: RunId
    max_elapsed_ms: Annotated[int, Field(strict=True, ge=1, le=86_400_000)]


class InstallPolicy(FrozenModel):
    op: Literal["install_policy"]
    policy: Policy


class InstallRunGrant(FrozenModel):
    op: Literal["install_run_grant"]
    grant: RunGrant


class StopScope(FrozenModel):
    """K1-K3 (C1 11.1): no new permit, consumption or receipt claim in the scope.
    Increments ``revocation_gen``; a tenant or global stop also the access epoch."""

    op: Literal["stop_scope"]
    scope_type: StopScopeType
    scope_id: Annotated[str, Field(min_length=1, max_length=128)]


class ResumeScope(FrozenModel):
    """Owner only. Old permits and receipts stay invalid (new revocation_gen)."""

    op: Literal["resume_scope"]
    scope_type: StopScopeType
    scope_id: Annotated[str, Field(min_length=1, max_length=128)]
    stop_seq: Count


class RevokeAccess(FrozenModel):
    op: Literal["revoke_access"]
    tenant_id: TenantId


class DenyResource(FrozenModel):
    op: Literal["deny_resource"]
    tenant_id: TenantId
    path: RelPath


class RevokeApproval(FrozenModel):
    op: Literal["revoke_approval"]
    approval_id: ApprovalId


BOUND_OPS = (
    "allocate",
    "record_decision",
    "confirm_approval",
    "activate",
    "record_intent",
    "issue_permit",
    "consume_permit",
    "claim_receipt",
    "mark_active",
    "release",
)
STANDALONE_OPS = (
    "report_usage",
    "mark_unknown",
    "reconcile",
    "correct",
    "unexplained",
    "void",
    "recover_in_flight",
    "record_approval",
)
OWNER_OPS = (
    "set_budget_cap",
    "set_counter_cap",
    "set_run_window",
    "install_policy",
    "install_run_grant",
    "stop_scope",
    "resume_scope",
    "revoke_access",
    "deny_resource",
    "revoke_approval",
)

LedgerOp = Annotated[
    AllocateReservation
    | RecordDecision
    | ConfirmApproval
    | RecordIntent
    | IssuePermit
    | ConsumePermit
    | ClaimReceipt
    | RecordApproval
    | InstallPolicy
    | InstallRunGrant
    | StopScope
    | ResumeScope
    | RevokeAccess
    | DenyResource
    | RevokeApproval
    | ActivateReservation
    | MarkActive
    | ReleaseReservation
    | ReportUsage
    | MarkUnknown
    | Reconcile
    | Correct
    | Unexplained
    | VoidAllocation
    | RecoverInFlight
    | SetBudgetCap
    | SetCounterCap
    | SetRunWindow,
    Field(discriminator="op"),
]


class LedgerBatch(FrozenModel):
    """Operations applied together (one transaction). Used for MACs and records."""

    ops: Annotated[tuple[LedgerOp, ...], Field(min_length=1, max_length=64)]
