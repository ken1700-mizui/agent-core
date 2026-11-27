"""Random source port. Production uses the OS cryptographic random source."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RandomSource(Protocol):
    def token_bytes(self, n: int) -> bytes:
        """Return exactly ``n`` random bytes, or raise."""
        ...
