"""WP2-06: Planning Engine v1 (pure; proposals have no authority).

- Difficulty: simple or complex by fixed rules.
- A plan is a DAG of steps (dependencies exist, no cycle) with a hash (plan_ref).
- ``ready`` lists the steps whose dependencies are done; progress is recorded;
  a failed step with no revision left stops the plan.
- Revisions are bounded: a cap on revisions and steps; done steps cannot be
  changed or removed; the completion criteria and the limits cannot be
  changed; a revision that would need more than the remaining budget is refused.
- The engine never creates steps, permits or approvals: it imports nothing of
  the runtime (import contract), and its outputs are plain data.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_core.planning import planner as pl
from agent_core.types.records import StepKind
from support import builders as b

LIMITS = pl.PlanLimits(max_steps=8, max_revisions=2, step_cost_xtu=100)


def _step(key: str, *deps: str, kind: StepKind = StepKind.TOOL_CALL) -> pl.PlanStep:
    return pl.PlanStep(
        key=key, kind=kind, depends_on=tuple(sorted(deps)), purpose_ref=b.digest(key)
    )


def _plan(*steps: pl.PlanStep, version: int = 1) -> pl.Plan:
    return pl.Plan(
        task_ref=b.digest("task"), criteria=(b.digest("c1"),), version=version, steps=steps
    )


def test_difficulty_rules() -> None:
    assert (
        pl.difficulty(pl.TaskShape(criteria=1, target_files=1, declared_parts=1))
        is pl.Difficulty.SIMPLE
    )
    assert (
        pl.difficulty(pl.TaskShape(criteria=3, target_files=1, declared_parts=1))
        is pl.Difficulty.COMPLEX
    )
    assert (
        pl.difficulty(pl.TaskShape(criteria=1, target_files=4, declared_parts=1))
        is pl.Difficulty.COMPLEX
    )
    assert (
        pl.difficulty(pl.TaskShape(criteria=1, target_files=1, declared_parts=2))
        is pl.Difficulty.COMPLEX
    )


def test_a_simple_task_gets_the_fixed_three_step_plan() -> None:
    plan = pl.simple_plan(task_ref=b.digest("task"), criteria=(b.digest("c1"),))
    assert [s.kind for s in plan.steps] == [
        StepKind.MODEL_CALL,
        StepKind.TOOL_CALL,
        StepKind.VERIFICATION,
    ]
    assert pl.ready(plan, pl.Progress()) == ("propose",)


def test_plans_are_dags_with_known_dependencies() -> None:
    with pytest.raises(pl.PlanRejectedError, match="unknown"):
        pl.validate(_plan(_step("a", "zzz")), LIMITS)
    with pytest.raises(pl.PlanRejectedError, match="cycle"):
        pl.validate(_plan(_step("a", "b"), _step("b", "a")), LIMITS)
    with pytest.raises(pl.PlanRejectedError, match="steps"):
        pl.validate(_plan(*(_step(f"s{i}") for i in range(9))), LIMITS)
    with pytest.raises(pl.PlanRejectedError, match="verification"):
        pl.validate(_plan(_step("a")), LIMITS)  # a plan ends with a verification


def test_the_plan_hash_is_deterministic() -> None:
    plan = _plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION))
    assert (
        plan.plan_ref() == _plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION)).plan_ref()
    )
    assert plan.plan_ref() != _plan(_step("a"), _step("v", kind=StepKind.VERIFICATION)).plan_ref()


def test_ready_follows_dependencies_and_progress() -> None:
    plan = pl.validate(
        _plan(
            _step("a"),
            _step("b"),
            _step("c", "a", "b"),
            _step("v", "c", kind=StepKind.VERIFICATION),
        ),
        LIMITS,
    )
    progress = pl.Progress()
    assert pl.ready(plan, progress) == ("a", "b")
    progress = progress.done("a")
    assert pl.ready(plan, progress) == ("b",)
    progress = progress.done("b")
    assert pl.ready(plan, progress) == ("c",)
    with pytest.raises(pl.PlanRejectedError, match="not ready"):
        progress.done("v").check_against(plan)


def test_a_failure_without_a_revision_left_stops_the_plan() -> None:
    plan = pl.validate(_plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION)), LIMITS)
    progress = pl.Progress().failed("a")
    assert pl.status(plan, progress, revisions_left=0) is pl.PlanStatus.STOPPED
    assert pl.status(plan, progress, revisions_left=1) is pl.PlanStatus.NEEDS_REVISION
    assert (
        pl.status(plan, pl.Progress().done("a").done("v"), revisions_left=0) is pl.PlanStatus.DONE
    )


def _revise(plan: pl.Plan, proposal: pl.Plan, progress: pl.Progress, **kw: Any) -> pl.Plan:
    args: dict[str, Any] = {"limits": LIMITS, "revisions_used": 0, "remaining_budget_xtu": 10_000}
    args.update(kw)
    return pl.revise(plan, proposal, progress, **args)


def test_a_revision_keeps_done_steps_and_criteria() -> None:
    plan = pl.validate(
        _plan(_step("a"), _step("b", "a"), _step("v", "b", kind=StepKind.VERIFICATION)), LIMITS
    )
    progress = pl.Progress().done("a").failed("b")
    better = _plan(
        _step("a"), _step("b2", "a"), _step("v", "b2", kind=StepKind.VERIFICATION), version=2
    )
    revised = _revise(plan, better, progress)
    assert revised.version == 2
    with pytest.raises(pl.PlanRejectedError, match="done"):
        _revise(
            plan,
            _plan(_step("b2"), _step("v", "b2", kind=StepKind.VERIFICATION), version=2),
            progress,
        )
    changed = better.model_copy(update={"criteria": (b.digest("easier"),)})
    with pytest.raises(pl.PlanRejectedError, match="criteria"):
        _revise(plan, changed, progress)


def test_revisions_are_bounded() -> None:
    plan = pl.validate(_plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION)), LIMITS)
    proposal = _plan(_step("a2"), _step("v", "a2", kind=StepKind.VERIFICATION), version=2)
    with pytest.raises(pl.PlanLimitError, match="revisions"):
        _revise(plan, proposal, pl.Progress().failed("a"), revisions_used=2)


def test_a_revision_beyond_the_remaining_budget_is_refused() -> None:
    plan = pl.validate(_plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION)), LIMITS)
    proposal = _plan(
        *(_step(f"x{i}") for i in range(6)),
        _step("v", *(f"x{i}" for i in range(6)), kind=StepKind.VERIFICATION),
        version=2,
    )
    with pytest.raises(pl.PlanLimitError, match="budget"):
        _revise(plan, proposal, pl.Progress().failed("a"), remaining_budget_xtu=300)


def test_a_revision_cannot_go_back_in_version_or_reuse_one() -> None:
    plan = pl.validate(
        _plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION), version=3), LIMITS
    )
    proposal = _plan(_step("a"), _step("v", "a", kind=StepKind.VERIFICATION), version=3)
    with pytest.raises(pl.PlanRejectedError, match="version"):
        _revise(plan, proposal, pl.Progress().failed("a"))


def test_the_planner_has_no_runtime_authority() -> None:
    from pathlib import Path

    import agent_core.planning.planner as module

    text = Path(module.__file__).read_text()
    for word in ("agent_core.runtime", "agent_core.adapters", "advance_attempt", "IssuePermit"):
        assert word not in text
