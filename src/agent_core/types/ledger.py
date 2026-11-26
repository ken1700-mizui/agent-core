"""Ledger operations (WP-09B, WP-09C): typed commands the SQLite ledger applies
inside the Journal's ``BEGIN IMMEDIATE`` transaction.

An operation is data: it carries IDs, amounts and estimates, never authority.
The ledger re-checks every condition against its own rows in the same
transaction. Times come from the ledger's trusted clock, never from an operation.

Pairing with runtime transitions (enforced by the SQLite journal):

- An Attempt transition to ``CONTENT_FIXED`` must carry ``AllocateReservation``
  for the same Attempt and reservation ID; ``BUDGET_RESERVED`` must carry
  ``ActivateReservation``. Operations bound to a transition are refused without it.
- Owner operations (caps, counters, run windows) are accepted only through the
  owner path, with the owner channel's MAC.
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
from agent_core.types.ids import AttemptId, ReservationId, RunId
from agent_core.types.model import Usage
from agent_core.types.safety import ActionKind
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


BOUND_OPS = ("allocate", "activate", "mark_active", "release")
STANDALONE_OPS = (
    "report_usage",
    "mark_unknown",
    "reconcile",
    "correct",
    "unexplained",
    "void",
    "recover_in_flight",
)
OWNER_OPS = ("set_budget_cap", "set_counter_cap", "set_run_window")

LedgerOp = Annotated[
    AllocateReservation
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
