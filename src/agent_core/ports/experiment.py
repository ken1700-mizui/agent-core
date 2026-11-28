"""Ports of the experiment budget across homes (WP2-08)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    CounterView,
    LedgerEntryView,
    ReservationView,
    ScopeUsage,
)
from agent_core.types.experiment_budget import (
    CarriedHoldResolved,
    ClosingStatement,
    OpeningStatement,
)
from agent_core.types.ids import ExperimentId, HomeId
from agent_core.types.ledger import LedgerOp


@runtime_checkable
class ExperimentHomePort(Protocol):
    """One home's ledger as the experiment budget sees it. ``apply_owner``
    applies owner-signed ledger operations (the owner's authority)."""

    @property
    def home_id(self) -> HomeId: ...

    def scope_usage(self, scope_type: BudgetScopeType, scope_id: str) -> ScopeUsage: ...

    def counter(
        self, scope_type: BudgetScopeType, scope_id: str, counter: CounterKind
    ) -> CounterView | None: ...

    def held(self, scope_type: BudgetScopeType, scope_id: str) -> tuple[tuple[str, int], ...]: ...

    def reservation(self, reservation_id: str) -> ReservationView | None: ...

    def ledger_entries(self, reservation_id: str) -> tuple[LedgerEntryView, ...]: ...

    def journal_head(self) -> tuple[int, str | None]: ...

    def apply_owner(self, *ops: LedgerOp) -> None: ...


@runtime_checkable
class HomeChainPort(Protocol):
    def record_opening(self, statement: OpeningStatement) -> None: ...

    def record_closing(self, statement: ClosingStatement) -> None: ...

    def record_resolution(self, record: CarriedHoldResolved) -> None: ...

    def openings(self, experiment_id: ExperimentId) -> tuple[OpeningStatement, ...]: ...

    def closings(self, experiment_id: ExperimentId) -> tuple[ClosingStatement, ...]: ...

    def resolutions(self, experiment_id: ExperimentId) -> tuple[CarriedHoldResolved, ...]: ...
