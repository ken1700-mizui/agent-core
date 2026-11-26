"""Ledger port (WP-09 limited fixes): what the runtime's execution gate needs
from the budget and authority ledger. Implemented by the SQLite journal."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.cost import ReservationView
from agent_core.types.ledger import LedgerBatch, LedgerOp
from agent_core.types.safety import (
    ActionDescriptor,
    Permit,
    Policy,
    Receipt,
    ReceiptState,
    RunGrant,
    StopEntry,
)


@runtime_checkable
class LedgerPort(Protocol):
    @property
    def owner_epoch(self) -> int: ...

    def receipt(self, receipt_id: str) -> Receipt | None: ...

    def receipt_state(self, receipt_id: str) -> ReceiptState | None: ...

    def recorded_descriptor(self, attempt_id: str) -> ActionDescriptor | None: ...

    def policy(self, tenant_id: str) -> Policy | None: ...

    def run_grant(self, run_id: str) -> RunGrant | None: ...

    def access_epoch(self, tenant_id: str) -> int: ...

    def permit(self, permit_id: str) -> Permit | None: ...

    def reservation(self, reservation_id: str) -> ReservationView | None: ...

    def stop_entries(self) -> tuple[StopEntry, ...]: ...

    def apply_ledger(self, ledger: tuple[LedgerOp, ...]) -> None: ...

    def apply_owner_ledger(self, batch: LedgerBatch, *, owner_mac: str) -> None: ...
