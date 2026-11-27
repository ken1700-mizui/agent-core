"""Log sink and monotonic clock ports (WP-11).

A sink stores one complete, already-serialized line per call and never sends
anything over a network. It raises ``LogWriteError`` (or a subclass) when it
cannot store the line; a partial line is never reported as written.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LogSink(Protocol):
    def write(self, line: bytes) -> None:
        """Store one line (ending with a single newline) or raise ``LogWriteError``."""
        ...


@runtime_checkable
class MonotonicClock(Protocol):
    def monotonic_ns(self) -> int:
        """Nanoseconds from an arbitrary origin; never decreases. For durations only."""
        ...
