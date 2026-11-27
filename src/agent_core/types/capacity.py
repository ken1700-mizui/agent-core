"""P-2 operating limits: the journal's capacity levels (no I/O).

Three levels, decided from measured quantities, never from a guess:

- ``NORMAL``: below every warning threshold;
- ``WARNING``: at or above a warning threshold (new work goes on; the owner is told);
- ``LIMITED``: at or above a limit (new Tasks, Runs, Steps and Attempts are refused).

``CapacityUsage`` is what is measured: the journal's event count (every tenant),
its storage in bytes, defined as the database file plus its write-ahead log
(the ``-shm`` index is not counted: it holds no data), and the start-up time of
this process (opening the journal and the Rebuild). Events are never deleted and
the database is never vacuumed, so the event count never falls and the database
file never shrinks; the write-ahead log can shrink at a checkpoint (its pages move
into the database file), so the byte sum can fall by at most the log's size. The
start-up time is measured again at each start. A new piece of work is admitted by
the level of the usage it would reach (``reserve_events`` and ``reserve_bytes``
added), decided when the work starts. The reserves are estimates, not a
guarantee of physical space.

The thresholds are configuration (``CapacityLimits``); without limits the level is
always ``NORMAL`` and only the usage is reported.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.values import Count


class CapacityLevel(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    LIMITED = "LIMITED"


class NewWork(StrEnum):
    """The kinds of new work a limit refuses. Everything else (finishing an
    existing Attempt, stop and cancel, status, reconciliation and recovery,
    safety records) is never refused by a limit."""

    TASK = "TASK"
    RUN = "RUN"
    STEP = "STEP"
    ATTEMPT = "ATTEMPT"


class CapacityUsage(FrozenModel):
    events: Count
    # The database file plus its write-ahead log.
    bytes: Count
    # How long this process took to open the journal and rebuild (None: not known).
    startup_ms: Count | None = None


class CapacityLimits(FrozenModel):
    warning_events: Count
    limit_events: Count
    warning_bytes: Count
    limit_bytes: Count
    # What one piece of new work is assumed to add before the next check.
    reserve_events: Count = 0
    reserve_bytes: Count = 0
    # The start-up time (opening and Rebuild), in milliseconds: both or neither.
    warning_startup_ms: Count | None = None
    limit_startup_ms: Count | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not 0 < self.warning_events <= self.limit_events:
            raise ValueError("0 < warning_events <= limit_events")
        if not 0 < self.warning_bytes <= self.limit_bytes:
            raise ValueError("0 < warning_bytes <= limit_bytes")
        warning, limit = self.warning_startup_ms, self.limit_startup_ms
        if (warning is None) != (limit is None):
            raise ValueError("warning_startup_ms and limit_startup_ms are set together")
        if warning is not None and limit is not None and not 0 < warning <= limit:
            raise ValueError("0 < warning_startup_ms <= limit_startup_ms")
        return self


def _reached(value: int | None, threshold: int | None) -> bool:
    return value is not None and threshold is not None and value >= threshold


def capacity_level(
    limits: CapacityLimits | None, usage: CapacityUsage, *, new_work: bool = False
) -> CapacityLevel:
    """The level of ``usage`` (with the reserve added when ``new_work``)."""
    if limits is None:
        return CapacityLevel.NORMAL
    events = usage.events + (limits.reserve_events if new_work else 0)
    size = usage.bytes + (limits.reserve_bytes if new_work else 0)
    if (
        events >= limits.limit_events
        or size >= limits.limit_bytes
        or _reached(usage.startup_ms, limits.limit_startup_ms)
    ):
        return CapacityLevel.LIMITED
    if (
        events >= limits.warning_events
        or size >= limits.warning_bytes
        or _reached(usage.startup_ms, limits.warning_startup_ms)
    ):
        return CapacityLevel.WARNING
    return CapacityLevel.NORMAL
