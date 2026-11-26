"""Cost types (WP-09B; D 10-11; E 4, 38; D.V10, V11, E.Z6, Z9, Z42).

Amounts are integers in micro XTU (``Budget.micro_units``, 64-bit). No float
is used anywhere in cost arithmetic. Unknown usage is never a known zero.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from agent_core.types.base import FrozenModel
from agent_core.types.ids import ExperimentId, RunId, StepId, TaskId, TenantId, UserId
from agent_core.types.values import Budget, Count, Identifier, SemVer

# A synthetic budget period ("synthetic-2026-11"). Real calendar months and time
# zones are AUDIT-02 (E 39) and are not used in Phase 1.
PeriodId = Annotated[str, StringConstraints(strict=True, pattern=r"^synthetic-\d{4}-\d{2}$")]
TokenCeiling = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
MICRO_PER_MTOK = 1_000_000


class ReservationState(StrEnum):
    """Budget reservation states (E 4.1, E 38.1)."""

    ID_ALLOCATED = "ID_ALLOCATED"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    ACTIVE = "ACTIVE"
    USAGE_REPORTED = "USAGE_REPORTED"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"
    RELEASED = "RELEASED"
    VOID = "VOID"


class AdjustmentKind(StrEnum):
    """Adjustment entries appended beside the state entries (E 4.1, D 10.2)."""

    CORRECTION = "CORRECTION"
    UNEXPLAINED = "UNEXPLAINED"
    OVERRUN = "OVERRUN"


class BudgetScopeType(StrEnum):
    """E 4.4: call/Attempt (the descriptor's cost cap) within Step, Run, Task,
    user, tenant and a synthetic period; an experiment within tenant and period."""

    STEP = "STEP"
    RUN = "RUN"
    TASK = "TASK"
    USER = "USER"
    TENANT = "TENANT"
    PERIOD = "PERIOD"
    EXPERIMENT = "EXPERIMENT"


class CounterKind(StrEnum):
    """Non-monetary limits (D 11.2). Enforced now: model calls, tool calls,
    retries and work units. The others are a type and accounting contract only
    until their Engines exist."""

    MODEL_CALLS = "MODEL_CALLS"
    TOOL_CALLS = "TOOL_CALLS"
    RETRIES = "RETRIES"
    WORK_UNITS = "WORK_UNITS"
    CONTEXT_REBUILDS = "CONTEXT_REBUILDS"
    REPLANS = "REPLANS"
    MEMORY_OPERATIONS = "MEMORY_OPERATIONS"


ENFORCED_COUNTERS = frozenset(
    {CounterKind.MODEL_CALLS, CounterKind.TOOL_CALLS, CounterKind.RETRIES, CounterKind.WORK_UNITS}
)


class PriceItem(FrozenModel):
    """Micro XTU per one million tokens (D 10.3). Synthetic test prices only."""

    input_micro_per_mtok: Count
    output_micro_per_mtok: Count


class PriceTable(FrozenModel):
    price_table_id: Identifier
    version: SemVer
    status: Literal["test"]
    items: tuple[tuple[Identifier, PriceItem], ...]

    def item(self, model_id: str) -> PriceItem | None:
        for name, item in self.items:
            if name == model_id:
                return item
        return None


class ModelCallEstimate(FrozenModel):
    """Upper bound of one model call: token ceilings times the test price."""

    kind: Literal["model"]
    price: PriceItem
    max_input_tokens: TokenCeiling
    max_output_tokens: TokenCeiling


class FixedEstimate(FrozenModel):
    """A fixed upper bound, such as a tool manifest's ``cost_estimate``."""

    kind: Literal["fixed"]
    amount: Budget


CostEstimate = Annotated[ModelCallEstimate | FixedEstimate, Field(discriminator="kind")]


class ReservationScopes(FrozenModel):
    """Every budget scope one reservation counts against."""

    tenant_id: TenantId
    user_id: UserId
    task_id: TaskId
    run_id: RunId
    step_id: StepId
    period_id: PeriodId
    experiment_id: ExperimentId | None


# ---------------------------------------------------------------- views (read only)


class ReservationView(FrozenModel):
    reservation_id: str
    attempt_id: str
    state: ReservationState
    reserved_micro: Count
    hold_micro: Count
    spent_micro: Count
    spent_lower_micro: Count
    # False while the cost is not final (UNKNOWN): spent_micro is then not a total.
    spent_known: bool
    action_hash: str | None
    permit_id: str | None
    receipt_id: str | None


class ScopeUsage(FrozenModel):
    scope_type: BudgetScopeType
    scope_id: str
    cap_micro: Count | None
    hold_micro: Count
    spent_micro: Count
    blocked: bool


class CounterView(FrozenModel):
    used: Count
    cap: Count


class LedgerEntryView(FrozenModel):
    entry_seq: Count
    kind: str
    state: ReservationState | None
    hold_micro: Count
    spent_micro: Count
    delta_micro: Count
