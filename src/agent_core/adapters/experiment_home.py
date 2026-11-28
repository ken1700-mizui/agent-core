"""One home's Journal and ledger as an ``ExperimentHomePort`` (WP2-08)."""

from __future__ import annotations

from collections.abc import Callable

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    CounterView,
    LedgerEntryView,
    ReservationView,
    ScopeUsage,
)
from agent_core.types.ids import HomeId, TenantId
from agent_core.types.ledger import LedgerOp


class JournalExperimentHome:
    """``owner`` signs and applies owner ledger operations with the owner's key
    (the Benchmark Runner acts for the owner); it is never the agent's path."""

    def __init__(
        self,
        journal: SqliteJournal,
        home_id: HomeId,
        tenant_id: TenantId,
        owner: Callable[..., None],
    ) -> None:
        self._journal = journal
        self._home_id = home_id
        self._tenant_id = tenant_id
        self._owner = owner

    @property
    def home_id(self) -> HomeId:
        return self._home_id

    def scope_usage(self, scope_type: BudgetScopeType, scope_id: str) -> ScopeUsage:
        return self._journal.scope_usage(scope_type, scope_id)

    def counter(
        self, scope_type: BudgetScopeType, scope_id: str, counter: CounterKind
    ) -> CounterView | None:
        return self._journal.counter(scope_type, scope_id, counter)

    def held(self, scope_type: BudgetScopeType, scope_id: str) -> tuple[tuple[str, int], ...]:
        return self._journal.held_reservations(scope_type, scope_id)

    def reservation(self, reservation_id: str) -> ReservationView | None:
        return self._journal.reservation(reservation_id)

    def ledger_entries(self, reservation_id: str) -> tuple[LedgerEntryView, ...]:
        return self._journal.ledger_entries(reservation_id)

    def journal_head(self) -> tuple[int, str | None]:
        head = self._journal.head(self._tenant_id)
        return (0, None) if head is None else (int(head.tenant_seq), str(head.head_hash))

    def apply_owner(self, *ops: LedgerOp) -> None:
        self._owner(*ops)
