"""Builders for the WP2-01 evaluation records (synthetic values only)."""

from __future__ import annotations

from typing import Any

from agent_core.types import evaluation as e
from agent_core.types.ids import ExperimentId, HomeId, ReservationId, RunId, TrialId
from support import builders as b

EXP = b.new_id(ExperimentId)
HOME = b.new_id(HomeId)


def rules(**overrides: Any) -> e.ClassificationRules:
    values: dict[str, Any] = {
        "version": "1.0.0",
        "allowed_invalid_reasons": (
            e.InvalidReason.GRADER_CRASH,
            e.InvalidReason.GRADING_IMPOSSIBLE,
            e.InvalidReason.SANDBOX_HOST_FAILURE,
        ),
        "primary_metric": e.PrimaryMetric.OBJECTIVE_SUCCESS_WITHOUT_VIOLATION,
    }
    values.update(overrides)
    return e.ClassificationRules(**values)


def budget(**overrides: Any) -> e.ExperimentBudget:
    values: dict[str, Any] = {
        "experiment_cap_xtu": 10_000,
        "trial_cap_xtu": 1_000,
        "trial_event_cap": 400,
        "events_per_home": 40_000,
        "max_homes": 1,
        "max_retries": 2,
    }
    values.update(overrides)
    return e.ExperimentBudget(**values)


def spec(**overrides: Any) -> e.ExperimentSpec:
    values: dict[str, Any] = {
        "experiment_id": EXP,
        "task_refs": tuple(sorted(b.digest(f"task-{i}") for i in range(3))),
        "config_hashes": tuple(sorted(b.digest(f"config-{i}") for i in range(2))),
        "repeats": 2,
        "seed": 7,
        "eval_set_version": "1.0.0",
        "grader_version": "1.0.0",
        "analysis_plan_ref": b.digest("plan"),
        "classification": rules(),
        "budget": budget(),
    }
    values.update(overrides)
    return e.ExperimentSpec(**values)


def trial(**overrides: Any) -> e.TrialRecord:
    values: dict[str, Any] = {
        "trial_id": b.new_id(TrialId),
        "experiment_id": EXP,
        "config_hash": spec().config_hashes[0],
        "task_ref": spec().task_refs[0],
        "repeat_index": 0,
        "order_index": 0,
        "seed": 1,
        "run_id": b.new_id(RunId),
        "journal_ref": e.JournalRef(home_id=HOME, tenant_seq=10, head_hash=b.digest("head")),
        "started_at_ms": b.TS,
        "ended_at_ms": b.TS + 1_000,
        "wall_ms": 1_000,
        "task_outcome": e.TaskOutcome.SUCCESS,
        "safety": e.SafetyRecord(violations=(), blocked_attempts=()),
        "failure_attribution": None,
        "invalid_reason": None,
        "tokens": e.TokenUsage(input=10, output=5),
        "cost": e.TrialCost(settled_xtu=100, held=()),
        "human_minutes": 0,
        "unresolved": e.Unresolved(attempt_refs=()),
        "grading_status": e.GradingStatus.GRADED,
        "limits_hit": (),
        "run_completed": True,
        "retry_of": None,
        "classification_rules_hash": rules().rules_hash(),
    }
    values.update(overrides)
    return e.TrialRecord(**values)


def held(xtu: int) -> e.HeldCost:
    return e.HeldCost(reservation_ref=b.new_id(ReservationId), xtu=xtu)


def violation() -> e.SafetyViolation:
    return e.SafetyViolation(
        code="permit_bypass", invariant_ref="C1.S1", evidence_ref=b.digest("v")
    )


def invalid(**overrides: Any) -> e.TrialRecord:
    values: dict[str, Any] = {
        "task_outcome": e.TaskOutcome.INVALID_ENV,
        "invalid_reason": e.InvalidReason.SANDBOX_HOST_FAILURE,
        "failure_attribution": e.FailureAttribution.ENV,
        "run_completed": False,
    }
    values.update(overrides)
    return trial(**values)
