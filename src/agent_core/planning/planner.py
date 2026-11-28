"""Planning Engine v1 (WP2-06; E 7.3, B1 1.1). Pure: proposes and checks, runs nothing.

A plan is data: steps with dependencies (a DAG) ending in a verification, a
version and a hash (``plan_ref``, recorded as Step provenance). The runtime
decides whether to create a Step for a ready plan step; every Step still goes
through the Policy, the RunGrant, the Permit, the budget and the completion
check. This module imports no runtime, adapter or ledger code.

Replanning is bounded: ``max_revisions``, ``max_steps``; a revision may not
change or drop a done step, change the completion criteria or the task, go
back in version, or need more than the remaining budget (``step_cost_xtu``
per step not yet done, a conservative estimate). A failed step with no
revision left stops the plan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.errors import AgentCoreError
from agent_core.types.records import StepKind
from agent_core.types.values import Count, Digest, require_sorted_unique

StepKey = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class PlanRejectedError(AgentCoreError):
    code = "PLAN_REJECTED"


class PlanLimitError(AgentCoreError):
    code = "PLAN_LIMIT"


class Difficulty(StrEnum):
    SIMPLE = "SIMPLE"
    COMPLEX = "COMPLEX"


class PlanStatus(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    NEEDS_REVISION = "NEEDS_REVISION"
    STOPPED = "STOPPED"


class TaskShape(FrozenModel):
    criteria: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    target_files: Count
    declared_parts: Annotated[int, Field(strict=True, ge=1, le=1_000)]


def difficulty(shape: TaskShape) -> Difficulty:
    """Fixed rules (v1): more than one criterion, more than two target files or
    more than one declared part makes a task complex."""
    complex_ = shape.criteria > 1 or shape.target_files > 2 or shape.declared_parts > 1
    return Difficulty.COMPLEX if complex_ else Difficulty.SIMPLE


class PlanStep(FrozenModel):
    key: StepKey
    kind: StepKind
    depends_on: tuple[StepKey, ...]
    purpose_ref: Digest  # a digest of the step's purpose text (no body here)

    @model_validator(mode="after")
    def _sorted(self) -> Self:
        require_sorted_unique(self.depends_on, "depends_on")
        if self.key in self.depends_on:
            raise ValueError("a step does not depend on itself")
        return self


class Plan(FrozenModel):
    task_ref: Digest
    criteria: Annotated[tuple[Digest, ...], Field(min_length=1)]
    version: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    steps: Annotated[tuple[PlanStep, ...], Field(min_length=1)]

    def plan_ref(self) -> Digest:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(b"agent-core/plan/v1\x00" + data.encode()).hexdigest()

    def keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.steps)


class PlanLimits(FrozenModel):
    max_steps: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    max_revisions: Annotated[int, Field(strict=True, ge=0, le=100)]
    step_cost_xtu: Count


def validate(plan: Plan, limits: PlanLimits) -> Plan:
    if len(plan.steps) > limits.max_steps:
        raise PlanRejectedError("the plan has more steps than allowed")
    keys = plan.keys()
    if len(set(keys)) != len(keys):
        raise PlanRejectedError("step keys repeat")
    known = set(keys)
    for step in plan.steps:
        if any(d not in known for d in step.depends_on):
            raise PlanRejectedError("a step depends on an unknown step")
    _no_cycle(plan)
    if plan.steps[-1].kind is not StepKind.VERIFICATION:
        raise PlanRejectedError("a plan ends with a verification step")
    return plan


def _no_cycle(plan: Plan) -> None:
    deps = {s.key: set(s.depends_on) for s in plan.steps}
    done: set[str] = set()
    while len(done) < len(deps):
        free = [k for k, d in deps.items() if k not in done and d <= done]
        if not free:
            raise PlanRejectedError("the plan has a cycle")
        done.update(free)


def simple_plan(*, task_ref: Digest, criteria: tuple[Digest, ...]) -> Plan:
    """The Phase 1 loop as a plan: propose (model), execute (tool), verify."""

    def step(key: str, kind: StepKind, *deps: str) -> PlanStep:
        purpose = "sha256:" + hashlib.sha256(f"simple/{key}".encode()).hexdigest()
        return PlanStep(key=key, kind=kind, depends_on=tuple(sorted(deps)), purpose_ref=purpose)

    return Plan(
        task_ref=task_ref,
        criteria=criteria,
        version=1,
        steps=(
            step("propose", StepKind.MODEL_CALL),
            step("execute", StepKind.TOOL_CALL, "propose"),
            step("verify", StepKind.VERIFICATION, "execute"),
        ),
    )


@dataclass(frozen=True)
class Progress:
    """What happened to the plan's steps (recorded by the runtime's Steps)."""

    done_keys: frozenset[str] = frozenset()
    failed_keys: frozenset[str] = frozenset()

    def done(self, key: str) -> Progress:
        return Progress(self.done_keys | {key}, self.failed_keys - {key})

    def failed(self, key: str) -> Progress:
        return Progress(self.done_keys, self.failed_keys | {key})

    def check_against(self, plan: Plan) -> None:
        deps = {s.key: set(s.depends_on) for s in plan.steps}
        for key in self.done_keys | self.failed_keys:
            if key not in deps:
                raise PlanRejectedError("progress names a step outside the plan")
            if not deps[key] <= self.done_keys:
                raise PlanRejectedError("a step finished that was not ready")


def ready(plan: Plan, progress: Progress) -> tuple[str, ...]:
    if progress.failed_keys:
        return ()
    return tuple(
        s.key
        for s in plan.steps
        if s.key not in progress.done_keys and set(s.depends_on) <= progress.done_keys
    )


def status(plan: Plan, progress: Progress, *, revisions_left: int) -> PlanStatus:
    if set(plan.keys()) <= progress.done_keys:
        return PlanStatus.DONE
    if progress.failed_keys:
        return PlanStatus.NEEDS_REVISION if revisions_left > 0 else PlanStatus.STOPPED
    return PlanStatus.RUNNING


def revise(
    plan: Plan,
    proposal: Plan,
    progress: Progress,
    *,
    limits: PlanLimits,
    revisions_used: int,
    remaining_budget_xtu: int,
) -> Plan:
    """A bounded revision. Raises; never returns a plan that breaks a rule."""
    if revisions_used >= limits.max_revisions:
        raise PlanLimitError("no revisions left")
    if proposal.version <= plan.version:
        raise PlanRejectedError("a revision has a later version")
    if proposal.task_ref != plan.task_ref or proposal.criteria != plan.criteria:
        raise PlanRejectedError("a revision cannot change the task or its criteria")
    validate(proposal, limits)
    old = {s.key: s for s in plan.steps}
    new = {s.key: s for s in proposal.steps}
    for key in progress.done_keys:
        if new.get(key) != old.get(key):
            raise PlanRejectedError("a revision cannot change or drop a done step")
    pending = [k for k in new if k not in progress.done_keys]
    if len(pending) * limits.step_cost_xtu > remaining_budget_xtu:
        raise PlanLimitError("the revision needs more than the remaining budget")
    return proposal
