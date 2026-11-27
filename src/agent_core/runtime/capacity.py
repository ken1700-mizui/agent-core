"""P-2 operating limits: the Runtime's admission of new work.

``CapacityGate.admit`` is called by ``RuntimeStateMachine`` at the start of each
command that creates new work (a Task, a Run, a Step), inside the command's lock
and before anything is planned or written, so the decision and the start of the
work see the same journal. At ``LIMITED`` (the usage plus the reserve for one
piece of new work) it refuses with ``CapacityLimitedError``; nothing is written.

It is never asked for anything else: finishing an existing Attempt, stop and
cancel, status, reconciliation and recovery, and safety records go on at any
level. A limit never releases a hold and never deletes or truncates the journal.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.ports.capacity import CapacityProbe
from agent_core.types.capacity import (
    CapacityLevel,
    CapacityLimits,
    CapacityUsage,
    NewWork,
    capacity_level,
)
from agent_core.types.errors import CapacityLimitedError


@dataclass(frozen=True)
class CapacityGate:
    limits: CapacityLimits | None
    probe: CapacityProbe

    def usage(self) -> CapacityUsage:
        return self.probe.capacity_usage()

    def level(self) -> CapacityLevel:
        """The level now (without a reserve), for status."""
        return capacity_level(self.limits, self.usage())

    def admit(self, work: NewWork, *, extra_events: int = 0) -> CapacityLevel:
        """The level the new work would reach; refused at ``LIMITED``.

        ``extra_events``: events a caller knows it will write right after, so a
        composite start (a Task and its Run) is admitted or refused as a whole."""
        usage = self.usage()
        if extra_events:
            usage = usage.model_copy(update={"events": usage.events + extra_events})
        level = capacity_level(self.limits, usage, new_work=True)
        if level is CapacityLevel.LIMITED:
            raise CapacityLimitedError(work.value)
        return level
