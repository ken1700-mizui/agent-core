"""WP2-01: evaluation types and the derived summaries (D.V19, D 7-8).

Design: ``PHASE_2_EVALUATION_CONTRACT.md`` 2 (2.4 rules, 2.5 cost model) and 7.8.

- Cost: each trial's cost is split once by settlement (settled or held per
  reservation). The four reporting buckets are views of that split, so one XTU
  is never counted twice; retry cost is a subset, never added to the total.
- Validity (``INVALID_ENV``) and settlement are separate attributes.
- Safety violations are counted over every trial, ``INVALID_ENV`` included.
- ``objective_success`` and ``safe_completion`` are derived, never stored.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.types import evaluation as e
from agent_core.types.ids import AttemptId, ReservationId, TrialId
from support import builders as b
from support import evaluation_world as ew

EXP = ew.EXP
rules, budget, spec, trial = ew.rules, ew.budget, ew.spec, ew.trial
held, violation, invalid = ew.held, ew.violation, ew.invalid


# ---------------------------------------------------------------- specs and hashes


def test_spec_hash_is_deterministic_and_covers_every_field() -> None:
    assert spec().spec_hash() == spec().spec_hash()
    assert spec(seed=8).spec_hash() != spec().spec_hash()
    assert spec(classification=rules(version="1.0.1")).spec_hash() != spec().spec_hash()
    assert spec(budget=budget(max_retries=3)).spec_hash() != spec().spec_hash()


def test_task_and_config_refs_are_hashes_of_their_specs() -> None:
    task = e.TaskSpec(
        task_key="dev_rename_symbol",
        version="1.0.0",
        source=e.TaskSource.DEV,
        goal_ref=b.digest("goal"),
        done_criteria=(b.digest("c1"),),
        grader_ref=b.digest("grader"),
        repo_cluster="cluster_a",
        stratum="small",
    )
    assert task.task_ref() == task.task_ref()
    assert task.task_ref() != task.model_copy(update={"version": "1.0.1"}).task_ref()
    config = e.ConfigSpec(
        config_key="baseline_b",
        model_profile_ref=b.digest("p"),
        context_strategy_ref=b.digest("c"),
        planning_strategy_ref=b.digest("pl"),
        verification_strategy_ref=b.digest("v"),
        prompt_template_ref=b.digest("t"),
        tool_manifest_ref=b.digest("m"),
        sandbox_profile_hash=None,
    )
    assert config.config_hash().startswith("sha256:")


def test_planned_trials_and_required_homes() -> None:
    s = spec()
    assert s.planned_trials == 3 * 2 * 2 + 2
    assert s.required_homes == 1
    # 14 trials x 400 events > 1 home of 4,000 events: refused unless enough homes
    with pytest.raises(ValidationError, match="max_homes"):
        spec(budget=budget(events_per_home=4_000))
    assert spec(budget=budget(events_per_home=4_000, max_homes=2)).required_homes == 2


def test_zero_cost_trials_still_bound_the_number_of_homes() -> None:
    """The home count comes from events, not XTU: a zero-cost trial still uses
    events, and the number of trials is fixed by the spec (7.8)."""
    s = spec(
        budget=budget(trial_cap_xtu=0, experiment_cap_xtu=0, events_per_home=4_000, max_homes=2)
    )
    assert s.required_homes == 2


@pytest.mark.parametrize(
    "change",
    [
        {"trial_cap_xtu": 20_000},  # above the experiment cap
        {"trial_event_cap": 50_000},  # above one home
        {"max_homes": 0},
    ],
)
def test_budget_rules(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        spec(budget=budget(**change))


def test_rules_need_at_least_one_invalid_reason_and_a_fixed_order() -> None:
    with pytest.raises(ValidationError):
        rules(allowed_invalid_reasons=())
    with pytest.raises(ValidationError):
        rules(
            allowed_invalid_reasons=(
                e.InvalidReason.SANDBOX_HOST_FAILURE,
                e.InvalidReason.GRADER_CRASH,
            )
        )


# ---------------------------------------------------------------- trial records


def test_invalid_reason_only_with_invalid_env() -> None:
    with pytest.raises(ValidationError):
        trial(invalid_reason=e.InvalidReason.GRADER_CRASH)
    with pytest.raises(ValidationError):
        invalid(invalid_reason=None)


def test_an_agent_failure_is_never_invalid_env() -> None:
    with pytest.raises(ValidationError, match="agent"):
        invalid(failure_attribution=e.FailureAttribution.AGENT)


def test_limits_match_the_outcome() -> None:
    with pytest.raises(ValidationError):
        trial(task_outcome=e.TaskOutcome.TIMEOUT, run_completed=False)
    trial(task_outcome=e.TaskOutcome.TIMEOUT, limits_hit=(e.LimitKind.TIME,), run_completed=False)
    with pytest.raises(ValidationError):
        trial(task_outcome=e.TaskOutcome.BUDGET_EXCEEDED, run_completed=False)


def test_not_gradable_is_either_the_evaluators_or_the_agents_fault() -> None:
    invalid(
        grading_status=e.GradingStatus.NOT_GRADABLE,
        invalid_reason=e.InvalidReason.GRADING_IMPOSSIBLE,
    )
    trial(
        grading_status=e.GradingStatus.NOT_GRADABLE,
        task_outcome=e.TaskOutcome.FAIL,
        failure_attribution=e.FailureAttribution.AGENT,
    )
    with pytest.raises(ValidationError):
        trial(grading_status=e.GradingStatus.NOT_GRADABLE)  # SUCCESS without grading
    with pytest.raises(ValidationError):
        invalid(invalid_reason=e.InvalidReason.GRADING_IMPOSSIBLE)  # but graded


def test_objective_success_and_safe_completion_are_different() -> None:
    clean = trial()
    assert clean.objective_success
    assert clean.safe_completion
    unresolved = trial(unresolved=e.Unresolved(attempt_refs=(b.new_id(AttemptId),)))
    assert unresolved.objective_success
    assert not unresolved.safe_completion
    unsafe = trial(safety=e.SafetyRecord(violations=(violation(),), blocked_attempts=()))
    assert unsafe.objective_success
    assert not unsafe.safe_completion
    unfinished = trial(run_completed=False)
    assert unfinished.objective_success
    assert not unfinished.safe_completion
    failed = trial(task_outcome=e.TaskOutcome.FAIL, failure_attribution=e.FailureAttribution.AGENT)
    assert not failed.objective_success
    assert not failed.safe_completion


def test_derived_values_cannot_be_written() -> None:
    with pytest.raises(ValidationError):
        trial(objective_success=False)
    with pytest.raises(ValidationError):
        trial(invalid_trial_xtu=5)


def test_held_cost_is_per_reservation_and_never_zero() -> None:
    with pytest.raises(ValidationError):
        e.TrialCost(settled_xtu=0, held=(held(0),))
    duplicate = held(5)
    with pytest.raises(ValidationError):
        e.TrialCost(settled_xtu=0, held=(duplicate, duplicate))


# ---------------------------------------------------------------- cost summary


def test_costs_are_split_once_and_buckets_are_views() -> None:
    valid = trial(cost=e.TrialCost(settled_xtu=100, held=(held(30),)))
    bad = invalid(cost=e.TrialCost(settled_xtu=40, held=(held(10),)))
    retry = trial(retry_of=bad.trial_id, cost=e.TrialCost(settled_xtu=70, held=()))
    summary = e.cost_summary((valid, bad, retry), ())
    # additive: each XTU appears once
    assert summary.settled_total == 100 + 40 + 70
    assert summary.held_total == 30 + 10
    assert summary.upper_bound == summary.settled_total + summary.held_total
    # views (not added to the totals)
    assert summary.settled_valid == 170
    assert summary.invalid_trial_xtu == 50  # settled and held of the invalid trial
    assert summary.retry_xtu == 70  # also inside settled_valid; never added again
    assert summary.held_unknown_xtu == 40


def test_unknown_cost_is_not_zero() -> None:
    record = trial(
        cost=e.TrialCost(settled_xtu=0, held=(held(25),)),
        unresolved=e.Unresolved(attempt_refs=(b.new_id(AttemptId),)),
    )
    summary = e.cost_summary((record,), ())
    assert summary.settled_total == 0
    assert summary.held_total == 25
    assert summary.upper_bound == 25


def test_reconciliation_moves_a_hold_once() -> None:
    hold = held(30)
    record = trial(cost=e.TrialCost(settled_xtu=100, held=(hold,)))
    rec = e.CostReconciliation(
        trial_id=record.trial_id,
        reservation_ref=hold.reservation_ref,
        settled_xtu=20,
        released_xtu=10,
        evidence_ref=b.digest("ledger"),
    )
    summary = e.cost_summary((record,), (rec,))
    assert (summary.settled_total, summary.held_total, summary.released_total) == (120, 0, 10)
    with pytest.raises(e.CostAccountingError, match="once"):
        e.cost_summary((record,), (rec, rec))


@pytest.mark.parametrize(
    ("settled", "released", "match"),
    [(20, 20, "hold"), (40, 0, "hold"), (10, 10, "hold")],
)
def test_reconciliation_must_account_for_the_whole_hold(
    settled: int, released: int, match: str
) -> None:
    hold = held(30)
    record = trial(cost=e.TrialCost(settled_xtu=0, held=(hold,)))
    rec = e.CostReconciliation(
        trial_id=record.trial_id,
        reservation_ref=hold.reservation_ref,
        settled_xtu=settled,
        released_xtu=released,
        evidence_ref=b.digest("ledger"),
    )
    with pytest.raises(e.CostAccountingError, match=match):
        e.cost_summary((record,), (rec,))


def test_reconciliation_of_an_unknown_hold_is_refused() -> None:
    record = trial()
    rec = e.CostReconciliation(
        trial_id=record.trial_id,
        reservation_ref=b.new_id(ReservationId),
        settled_xtu=1,
        released_xtu=0,
        evidence_ref=b.digest("ledger"),
    )
    with pytest.raises(e.CostAccountingError, match="hold"):
        e.cost_summary((record,), (rec,))


def test_a_trial_is_counted_once() -> None:
    record = trial()
    with pytest.raises(e.CostAccountingError, match="once"):
        e.cost_summary((record, record), ())


# ---------------------------------------------------------------- safety and success


def test_violations_stay_in_the_safety_summary_when_the_trial_is_invalid() -> None:
    bad = invalid(safety=e.SafetyRecord(violations=(violation(),), blocked_attempts=()))
    summary = e.safety_summary((trial(), bad))
    assert summary.violations == 1
    assert summary.trials_with_violations == 1
    assert summary.trials == 2
    counts = e.success_counts((trial(), bad))
    assert counts.denominator == 1  # invalid trials leave only the success-rate denominator
    assert counts.invalid == 1


def test_blocked_attempts_are_not_violations() -> None:
    blocked = e.BlockedAttempt(deny_reason="policy_denied", attempt_ref=b.new_id(AttemptId))
    record = trial(safety=e.SafetyRecord(violations=(), blocked_attempts=(blocked,)))
    summary = e.safety_summary((record,))
    assert (summary.violations, summary.blocked_attempts) == (0, 1)
    assert record.safe_completion


def test_success_counts_follow_the_rules() -> None:
    records = (
        trial(),
        trial(safety=e.SafetyRecord(violations=(violation(),), blocked_attempts=())),
        trial(unresolved=e.Unresolved(attempt_refs=(b.new_id(AttemptId),))),
        trial(task_outcome=e.TaskOutcome.FAIL, failure_attribution=e.FailureAttribution.AGENT),
        invalid(),
    )
    counts = e.success_counts(records)
    assert counts.denominator == 4
    assert counts.primary_successes == 2  # success without a violation (D 7.1)
    assert counts.safe_completions == 1
    assert counts.unresolved_trials == 1
    assert counts.invalid == 1


# ---------------------------------------------------------------- scores, analysis, decision


def test_score_record_rules() -> None:
    e.ScoreRecord(
        trial_id=b.new_id(TrialId),
        experiment_id=EXP,
        grader_version="1.0.0",
        grading_status=e.GradingStatus.GRADED,
        task_outcome=e.TaskOutcome.SUCCESS,
        points=3,
        max_points=3,
        evidence_ref=b.digest("score"),
    )
    with pytest.raises(ValidationError):
        e.ScoreRecord(
            trial_id=b.new_id(TrialId),
            experiment_id=EXP,
            grader_version="1.0.0",
            grading_status=e.GradingStatus.GRADED,
            task_outcome=e.TaskOutcome.SUCCESS,
            points=2,
            max_points=3,
            evidence_ref=b.digest("score"),
        )


def test_analysis_bounds_and_decision_needs_the_safety_gate() -> None:
    result = e.AnalysisResult(
        experiment_id=EXP,
        spec_hash=spec().spec_hash(),
        metric=e.PrimaryMetric.OBJECTIVE_SUCCESS_WITHOUT_VIOLATION,
        method="cluster_bootstrap",
        estimate_ppm=500_000,
        ci_low_ppm=400_000,
        ci_high_ppm=600_000,
        n_tasks=3,
        n_trials_valid=12,
        n_trials_invalid=0,
        violations=0,
        results_head=b.digest("head"),
    )
    with pytest.raises(ValidationError):
        result.model_validate({**result.model_dump(), "ci_low_ppm": 700_000})
    with pytest.raises(ValidationError, match="safety"):
        e.Decision(
            experiment_id=EXP,
            analysis_ref=b.digest("analysis"),
            verdict=e.Verdict.ADOPT,
            safety_gate_passed=False,
            reasons=("estimate_above_margin",),
        )
