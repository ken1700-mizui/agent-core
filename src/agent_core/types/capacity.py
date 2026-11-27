"""P-2 operating limits: the journal's capacity levels (no I/O).

Three levels, decided from the journal's own size, never from a guess:

- ``NORMAL``: below every warning threshold;
- ``WARNING``: at or above a warning threshold (new work goes on; the owner is told);
- ``LIMITED``: at or above a limit (new Tasks, new Runs and new Steps are refused).

``CapacityUsage`` is what the journal measures: its event count (every tenant)
and its storage in bytes, defined as the database file plus its write-ahead log
(the ``-shm`` index is not counted: it holds no data). Both only grow: events are
never deleted and the database is never vacuumed, so a level reached stays
reached after a restart. A new piece of work is admitted by the level of the
usage it would reach (``reserve_events`` and ``reserve_bytes`` added), decided
at the moment the work starts, under the Runtime's lock.

The thresholds are configuration (``CapacityLimits``). No value is built in:
the numbers are the owner's decision (P-2); without limits the level is always
``NORMAL`` and only the usage is reported.
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


class CapacityUsage(FrozenModel):
    events: Count
    # The database file plus its write-ahead log.
    bytes: Count


class CapacityLimits(FrozenModel):
    warning_events: Count
    limit_events: Count
    warning_bytes: Count
    limit_bytes: Count
    # What one piece of new work is assumed to add before the next check.
    reserve_events: Count = 0
    reserve_bytes: Count = 0

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not 0 < self.warning_events <= self.limit_events:
            raise ValueError("0 < warning_events <= limit_events")
        if not 0 < self.warning_bytes <= self.limit_bytes:
            raise ValueError("0 < warning_bytes <= limit_bytes")
        return self


def capacity_level(
    limits: CapacityLimits | None, usage: CapacityUsage, *, new_work: bool = False
) -> CapacityLevel:
    """The level of ``usage`` (with the reserve added when ``new_work``)."""
    if limits is None:
        return CapacityLevel.NORMAL
    events = usage.events + (limits.reserve_events if new_work else 0)
    size = usage.bytes + (limits.reserve_bytes if new_work else 0)
    if events >= limits.limit_events or size >= limits.limit_bytes:
        return CapacityLevel.LIMITED
    if events >= limits.warning_events or size >= limits.warning_bytes:
        return CapacityLevel.WARNING
    return CapacityLevel.NORMAL
