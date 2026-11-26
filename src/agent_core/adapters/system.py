"""System adapters: wall clock and OS cryptographic random source (stdlib only)."""

from __future__ import annotations

import os
import time


class SystemClock:
    """Wall clock in milliseconds since the Unix epoch. It may go backwards."""

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000


class OsRandomSource:
    """``os.urandom``: the OS cryptographic random source. Errors propagate."""

    def token_bytes(self, n: int) -> bytes:
        if type(n) is not int or n <= 0:
            raise ValueError("n must be a positive int")
        return os.urandom(n)
