"""Context port (WP-14): the read-only runtime view the fact derivation reads.

The Context Engine never reads the Journal or SQLite itself: the runtime hands it
its current state through this view (B1 1.1). ``RuntimeState`` satisfies it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_core.types.ids import AttemptId, RunId, StepId, TaskId
from agent_core.types.records import Attempt, Run, Step, Task


class RuntimeView(Protocol):
    @property
    def tasks(self) -> Mapping[TaskId, Task]: ...

    @property
    def runs(self) -> Mapping[RunId, Run]: ...

    @property
    def steps(self) -> Mapping[StepId, Step]: ...

    @property
    def attempts(self) -> Mapping[AttemptId, Attempt]: ...

    @property
    def run_steps(self) -> Mapping[RunId, tuple[StepId, ...]]: ...

    @property
    def step_attempts(self) -> Mapping[StepId, tuple[AttemptId, ...]]: ...
