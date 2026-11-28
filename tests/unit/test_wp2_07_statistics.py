"""WP2-07: Statistics Engine (D 8, D 16.1). Standard library only.

Correctness is checked against answers that do not come from this code: a
table of Student-t quantiles, a permutation p-value enumerated here, the
analytic standard error of a mean, a hand-worked Holm example, hand-worked
weighted means and decision labels. The D 16.1 simulation (2,000 runs per
scenario) is in ``test_wp2_07_simulation.py`` (``simulation`` category).
"""

from __future__ import annotations

import itertools
import math
import random
import statistics

import pytest

from agent_core.stats import engine as se

# ---------------------------------------------------------------- known answers


@pytest.mark.parametrize(
    ("df", "p", "table"),
    [(1, 0.975, 12.706205), (10, 0.975, 2.228139), (29, 0.975, 2.045230), (120, 0.975, 1.979930)],
)
def test_t_quantiles_match_the_table(df: int, p: float, table: float) -> None:
    assert abs(se.t_quantile(p, df) - table) < 1e-4


def test_the_weighted_mean_by_hand() -> None:
    tasks = [
        se.TaskPair(task="t1", cluster="r1", stratum="small", baseline=0.5, candidate=0.75),
        se.TaskPair(task="t2", cluster="r1", stratum="small", baseline=0.5, candidate=0.5),
        se.TaskPair(task="t3", cluster="r2", stratum="large", baseline=0.25, candidate=0.75),
    ]
    # task-count weights: (0.25 + 0 + 0.5) / 3
    assert se.weighted_difference(tasks, se.Weighting.TASK_COUNT) == pytest.approx(0.25)
    # equal strata: small mean 0.125, large mean 0.5 -> 0.3125
    assert se.weighted_difference(tasks, se.Weighting.EQUAL_STRATA) == pytest.approx(0.3125)


def test_the_exact_permutation_p_value_matches_an_enumeration() -> None:
    rng = random.Random(3)
    tasks = [
        se.TaskPair(
            task=f"t{i}",
            cluster=f"c{i % 8}",
            stratum="s",
            baseline=rng.random(),
            candidate=rng.random(),
        )
        for i in range(24)
    ]
    found = se.cluster_permutation_p(tasks, se.Weighting.TASK_COUNT, rng=random.Random(1))
    # independent: flip each cluster's sign in all 2**8 ways, by hand here
    clusters: dict[str, list[float]] = {}
    for t in tasks:
        clusters.setdefault(t.cluster, []).append(t.candidate - t.baseline)
    sums = [sum(v) for v in clusters.values()]
    total = sum(len(v) for v in clusters.values())
    observed = abs(sum(sums) / total)
    hits = sum(
        1
        for signs in itertools.product((1, -1), repeat=len(sums))
        if abs(sum(s * x for s, x in zip(signs, sums, strict=True)) / total) >= observed - 1e-12
    )
    assert found == pytest.approx(hits / 2 ** len(sums))


def test_the_bootstrap_standard_error_is_close_to_the_analytic_one() -> None:
    rng = random.Random(7)
    tasks = [
        se.TaskPair(
            task=f"t{i}", cluster=f"c{i}", stratum="s", baseline=0.0, candidate=rng.gauss(0.1, 0.2)
        )
        for i in range(40)
    ]
    draws = se.cluster_bootstrap(
        tasks, se.Weighting.TASK_COUNT, iterations=4000, rng=random.Random(5)
    )
    diffs = [t.candidate for t in tasks]
    analytic = statistics.pstdev(diffs) / math.sqrt(len(diffs))
    assert statistics.pstdev(draws) == pytest.approx(analytic, rel=0.08)


def test_holm_by_hand() -> None:
    adjusted = se.holm([0.01, 0.04, 0.03])
    assert adjusted == pytest.approx([0.03, 0.06, 0.06])
    assert se.holm_reject([0.01, 0.04, 0.03], alpha=0.05) == [True, False, False]


@pytest.mark.parametrize(
    ("low", "high", "estimate", "label"),
    [
        (0.06, 0.20, 0.10, "IMPROVED_CONFIRMED"),
        (0.01, 0.20, 0.07, "IMPROVED_ESTIMATE"),
        (0.01, 0.06, 0.03, "IMPROVED_EXISTS"),
        (-0.01, 0.06, 0.02, "NON_INFERIOR"),
        (-0.04, -0.01, -0.02, "DEGRADED_WITHIN_MARGIN"),
        (-0.10, -0.06, -0.08, "PRACTICALLY_DEGRADED"),
        (-0.08, 0.06, 0.0, "INCONCLUSIVE"),
    ],
)
def test_the_decision_labels_of_d_8_4(low: float, high: float, estimate: float, label: str) -> None:
    verdict = se.classify(se.Interval(low, estimate, high), delta_ni=0.05, delta_imp=0.05)
    assert verdict.label == label


def test_a_better_result_always_meets_non_inferiority() -> None:
    verdict = se.classify(se.Interval(0.001, 0.01, 0.02), delta_ni=0.05, delta_imp=0.05)
    assert verdict.direction == "BETTER"
    assert verdict.non_inferiority == "NI_MET"


# ---------------------------------------------------------------- metrics and missing data


def test_cost_and_time_are_compared_by_the_negated_log_ratio() -> None:
    better = se.log_ratio_pair("t1", "c1", "s", baseline=[10.0, 10.0], candidate=[5.0, 5.0])
    assert better.candidate - better.baseline == pytest.approx(math.log(2))
    with pytest.raises(ValueError, match="positive"):
        se.log_ratio_pair("t1", "c1", "s", baseline=[0.0], candidate=[1.0])


def test_missing_tasks_are_reported_and_the_worst_case_is_computed() -> None:
    complete = [se.TaskPair(task="t1", cluster="c", stratum="s", baseline=0.5, candidate=0.75)]
    analysis = se.paired_with_missing(
        complete, missing_candidate=["t2"], missing_baseline=[], weighting=se.Weighting.TASK_COUNT
    )
    assert analysis.missing == 1
    # worst case: the missing task fails for the candidate and succeeds for the baseline
    assert analysis.worst_case == pytest.approx((0.25 + -1.0) / 2)
    assert analysis.estimate == pytest.approx(0.25)


def test_results_are_reproducible_from_the_seed() -> None:
    tasks = [
        se.TaskPair(
            task=f"t{i}", cluster=f"c{i % 5}", stratum="s", baseline=0.4, candidate=0.4 + 0.01 * i
        )
        for i in range(20)
    ]
    first = se.analyse(tasks, se.AnalysisPlan(seed=11, iterations=300))
    second = se.analyse(tasks, se.AnalysisPlan(seed=11, iterations=300))
    assert first == second
    assert first.seed == 11


def test_a_small_simulation_runs_and_records_its_inputs() -> None:
    from agent_core.stats import simulation as sim

    report = sim.run(sim.Scenario(name="smoke", effect=0.0), runs=40, seed=5, iterations=100)
    assert report.runs == 40
    assert report.seed == 5
    assert 0.0 <= report.adopt_rate <= 1.0
    assert report.seconds >= 0.0
