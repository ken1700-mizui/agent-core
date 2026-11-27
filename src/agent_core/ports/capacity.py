"""Capacity probe port (P-2). Implemented by ``agent_core.adapters.sqlite_journal``.

``capacity_usage`` measures the journal as it is stored: the event count of
every tenant and the bytes of the database file plus its write-ahead log
(``agent_core.types.capacity``). It reads only; it never changes the journal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.capacity import CapacityUsage


@runtime_checkable
class CapacityProbe(Protocol):
    def capacity_usage(self) -> CapacityUsage: ...
