"""Clock port (wall-clock time for ID timestamps and ``recorded_at_ms``).

Wall-clock time can go backwards. It is never used to order events; order comes
from ``run_seq`` and ``tenant_seq`` (E_INTEGRATION 2.2, 2.3).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now_ms(self) -> int:
        """Milliseconds since the Unix epoch (UTC)."""
        ...
