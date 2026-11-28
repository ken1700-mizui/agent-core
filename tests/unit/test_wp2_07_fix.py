"""WP2-07 fix (D19): cluster t interval, few-cluster sign-flip inversion, the
D 9.2 decision procedure, and the simulation through the formal analysis.

Expected values come from outside the code under test: Student-t tables, a
one-sample t on cluster means, hand-worked residuals, a delete-one-cluster
jackknife, a brute-force enumeration of sign flips on the raw differences,
and hand-worked decision boundaries.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from typing import Any

import pytest

from agent_core.stats import engine as se
from agent_core.stats import simulation as sim


def _pair(task: str, cluster: str, diff: float, stratum: str = "s") -> se.TaskPair:
    return se.TaskPair(task=task, cluster=cluster, stratum=stratum, baseline=0.0, candidate=diff)


def _one_per_cluster(diffs: list[float]) -> list[se.TaskPair]:
    return [_pair(f"t{i}", f"c{i:03d}", d) for i, d in enumerate(diffs)]


# ---------------------------------------------------------------- t distribution


@pytest.mark.parametrize(
    ("p", "df", "table"),
    [
        (0.975, 5, 2.570582),
        (0.975, 9, 2.262157),
        (0.975, 11, 2.200985),
        (0.975, 39, 2.022691),
        (0.995, 10, 3.169273),
        (0.95, 29, 1.699127),
    ],
)
def test_more_t_quantiles_match_the_table(p: float, df: int, table: float) -> None:
    assert abs(se.t_quantile(p, df) - table) < 1e-5


def test_the_t_cdf_matches_a_known_value() -> None:
    # two-sided p of t = 2 with 10 df is 0.073388
    assert se.t_cdf(2.0, 10) == pytest.approx(1 - 0.073388 / 2, abs=1e-6)


# ---------------------------------------------------------------- cluster t interval


def test_with_equal_clusters_the_cluster_t_is_the_one_sample_t_of_cluster_means() -> None:
    rng = random.Random(4)
    tasks = [_pair(f"t{c}_{k}", f"c{c}", rng.gauss(0.1, 0.3)) for c in range(10) for k in range(3)]
    scores = se.cluster_scores(tasks, se.Weighting.TASK_COUNT)
    interval = se.cluster_t_interval(scores, level=0.95)
    means = [
        statistics.fmean(t.difference for t in tasks if t.cluster == f"c{c}") for c in range(10)
    ]
    half = 2.262157 * statistics.stdev(means) / math.sqrt(10)  # t table, 9 df
    assert interval.estimate == pytest.approx(statistics.fmean(means))
    assert interval.low == pytest.approx(statistics.fmean(means) - half, abs=1e-6)
    assert interval.high == pytest.approx(statistics.fmean(means) + half, abs=1e-6)
    assert interval.df == 9


def test_unequal_cluster_sizes_by_hand() -> None:
    # c1: [1, 0], c2: [0], c3: [1, 1, 1]; N = 6, mean 2/3
    tasks = [
        _pair("a", "c1", 1.0),
        _pair("b", "c1", 0.0),
        _pair("c", "c2", 0.0),
        _pair("d", "c3", 1.0),
        _pair("e", "c3", 1.0),
        _pair("f", "c3", 1.0),
    ]
    interval = se.cluster_t_interval(se.cluster_scores(tasks, se.Weighting.TASK_COUNT), level=0.95)
    # residuals S_c - m n_c: -1/3, -2/3, 1; sum of squares 14/9; var = 3/2 * (14/9) / 36
    assert interval.estimate == pytest.approx(2 / 3)
    assert interval.standard_error == pytest.approx(math.sqrt(14 / 216))


def test_the_cluster_variance_agrees_with_a_jackknife() -> None:
    rng = random.Random(9)
    tasks = []
    for c in range(40):
        u = rng.gauss(0, 0.1)
        for k in range(rng.randint(1, 7)):
            stratum = "a" if k % 3 else "b"
            tasks.append(_pair(f"t{c}_{k}", f"c{c:02d}", u + rng.gauss(0.05, 0.2), stratum))
    for weighting in se.Weighting:
        se_t = se.cluster_t_interval(se.cluster_scores(tasks, weighting), level=0.95).standard_error
        clusters = sorted({t.cluster for t in tasks})
        leave = [
            se.weighted_difference([t for t in tasks if t.cluster != c], weighting)
            for c in clusters
        ]
        mean = statistics.fmean(leave)
        jack = (len(clusters) - 1) / len(clusters) * sum((x - mean) ** 2 for x in leave)
        assert se_t == pytest.approx(math.sqrt(jack), rel=0.10), weighting


def test_equal_strata_weights_by_hand() -> None:
    tasks = [
        _pair("t1", "r1", 0.25, "small"),
        _pair("t2", "r1", 0.0, "small"),
        _pair("t3", "r2", 0.5, "large"),
    ]
    scores = se.cluster_scores(tasks, se.Weighting.EQUAL_STRATA)
    assert scores.estimate == pytest.approx(0.3125)  # (0.125 + 0.5) / 2
    assert sum(scores.weights) == pytest.approx(1.0)
    assert sum(scores.residuals) == pytest.approx(0.0, abs=1e-12)


def test_fewer_than_two_clusters_is_refused() -> None:
    with pytest.raises(se.InsufficientClustersError):
        se.cluster_t_interval(
            se.cluster_scores(
                [_pair("a", "c", 0.1), _pair("b", "c", 0.2)], se.Weighting.TASK_COUNT
            ),
            level=0.95,
        )


def test_zero_variance_is_degenerate_and_never_decides() -> None:
    tasks = [
        _pair(f"t{c}_{k}", f"c{c:02d}", 0.2 if k else 0.0) for c in range(30) for k in range(2)
    ]
    out = se.analyse(tasks, se.AnalysisPlan(seed=1, sensitivity=False))
    assert out.degenerate
    verdict = se.verdict_of(out, delta_ni=0.03, delta_imp=0.05)
    assert verdict.label == "INCONCLUSIVE"
    assert verdict.non_inferiority == "NI_FAILED"


# ---------------------------------------------------------------- few clusters: sign-flip inversion


def _brute_p(tasks: list[se.TaskPair], delta0: float, *, upper: bool) -> float:
    """Enumerate all sign flips of (d - delta0) by cluster on the raw tasks."""
    clusters = sorted({t.cluster for t in tasks})
    observed = se.weighted_difference(tasks, se.Weighting.TASK_COUNT) - delta0
    hits = 0
    for signs in itertools.product((1, -1), repeat=len(clusters)):
        flip = {c for c, s in zip(clusters, signs, strict=True) if s < 0}
        moved = [
            _pair(
                t.task, t.cluster, 2 * delta0 - t.difference if t.cluster in flip else t.difference
            )
            for t in tasks
        ]
        stat = se.weighted_difference(moved, se.Weighting.TASK_COUNT) - delta0
        hits += (stat <= observed + 1e-12) if upper else (stat >= observed - 1e-12)
    return hits / (1 << len(clusters))


def test_the_sign_flip_interval_inverts_an_enumerated_test() -> None:
    rng = random.Random(12)
    tasks = [
        _pair(f"t{c}_{k}", f"c{c}", rng.gauss(0.1, 0.25))
        for c in range(8)
        for k in range(rng.randint(1, 4))
    ]
    out = se.cluster_sign_flip_interval(
        se.cluster_scores(tasks, se.Weighting.TASK_COUNT), level=0.95, rng=random.Random(0)
    )
    assert out.exact
    eps = 1e-6
    assert _brute_p(tasks, out.low - eps, upper=False) <= 0.025
    assert _brute_p(tasks, out.low + eps, upper=False) > 0.025
    assert _brute_p(tasks, out.high + eps, upper=True) <= 0.025
    assert _brute_p(tasks, out.high - eps, upper=True) > 0.025


def test_too_few_clusters_give_an_unbounded_interval() -> None:
    tasks = _one_per_cluster([0.3, 0.4, 0.5, 0.35, 0.45])  # 2**5 * 0.025 < 1
    out = se.analyse(tasks, se.AnalysisPlan(seed=1, sensitivity=False))
    assert out.method == se.Method.CLUSTER_SIGN_FLIP
    assert out.low == -math.inf
    assert out.high == math.inf
    assert se.verdict_of(out, delta_ni=0.03, delta_imp=0.05).label == "INCONCLUSIVE"


@pytest.mark.parametrize(("clusters", "method"), [(29, "CLUSTER_SIGN_FLIP"), (30, "CLUSTER_T")])
def test_the_method_follows_the_registered_cluster_threshold(clusters: int, method: str) -> None:
    rng = random.Random(clusters)
    out = se.analyse(
        _one_per_cluster([rng.gauss(0, 1) for _ in range(clusters)]),
        se.AnalysisPlan(seed=3, iterations=999, sensitivity=False),
    )
    assert out.method == method


def test_the_sensitivity_analyses_do_not_change_the_primary_result() -> None:
    rng = random.Random(5)
    for clusters in (12, 25, 40):
        tasks = _one_per_cluster([rng.gauss(0.05, 0.3) for _ in range(clusters)])
        with_sens = se.analyse(tasks, se.AnalysisPlan(seed=8, iterations=999, sensitivity=True))
        without = se.analyse(tasks, se.AnalysisPlan(seed=8, iterations=999, sensitivity=False))
        assert (with_sens.low, with_sens.estimate, with_sens.high) == (
            without.low,
            without.estimate,
            without.high,
        )
        assert {name for name, _, _ in with_sens.sensitivity} >= {"cluster_bootstrap_percentile"}
        assert without.sensitivity == ()


# ---------------------------------------------------------------- boundaries of D 8.4


@pytest.mark.parametrize(
    ("low", "estimate", "high", "field", "expected"),
    [
        (0.0, 0.02, 0.04, "direction", "UNDETERMINED"),  # L > 0 is strict
        (-0.04, -0.02, 0.0, "direction", "UNDETERMINED"),  # U < 0 is strict
        (-0.03, 0.0, 0.03, "non_inferiority", "NI_FAILED"),  # L > -delta_ni is strict
        (0.05, 0.08, 0.11, "practical", "PRACTICAL_CONFIRMED"),  # L >= delta_imp
        (0.01, 0.05, 0.09, "practical", "PRACTICAL_ESTIMATE"),  # estimate >= delta_imp
        (-0.07, -0.05, -0.03, "label", "INCONCLUSIVE"),  # U = -delta_ni is not below it
    ],
)
def test_the_decision_boundaries(
    low: float, estimate: float, high: float, field: str, expected: str
) -> None:
    verdict = se.classify(se.Interval(low, estimate, high), delta_ni=0.03, delta_imp=0.05)
    assert getattr(verdict, field) == expected


# ---------------------------------------------------------------- missing, cost and time zeros


def test_the_missing_worst_case_reruns_the_same_analysis() -> None:
    rng = random.Random(2)
    complete = [_pair(f"t{c}", f"c{c:02d}", 0.1 + rng.gauss(0, 0.05)) for c in range(40)]
    missing = [se.MissingTask(task=f"m{c}", cluster=f"c{c:02d}", stratum="s") for c in range(4)]
    result = se.missing_sensitivity(
        complete, missing, se.AnalysisPlan(seed=1, sensitivity=False), delta_ni=0.03, delta_imp=0.05
    )
    assert result.missing == 4
    # worst case: the candidate fails (0) and the baseline succeeds (1) on each missing task
    worst_tasks = se.worst_case_pairs(complete, missing)
    assert len(worst_tasks) == 44
    assert sum(t.difference == -1.0 for t in worst_tasks) == 4
    assert result.worst.estimate == pytest.approx((sum(t.difference for t in complete) - 4) / 44)
    assert result.robust == (result.complete_label == result.worst_label)


def test_zero_costs_are_left_out_of_the_log_ratio_and_counted() -> None:
    comparison = se.cost_time_comparison(
        [
            se.CostRecord("t1", "c1", "s", baseline=[10.0, 10.0], candidate=[5.0, 5.0]),
            se.CostRecord("t2", "c2", "s", baseline=[0.0, 4.0], candidate=[2.0, 2.0]),
            se.CostRecord("t3", "c3", "s", baseline=[0.0, 0.0], candidate=[0.0, 0.0]),
        ]
    )
    assert [p.task for p in comparison.log_pairs] == ["t1"]
    assert comparison.excluded_zero == 2
    # absolute difference, sign flipped so that cheaper is positive: 10 - 5, 2 - 2, 0 - 0
    assert [p.difference for p in comparison.absolute_pairs] == [5.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="negative"):
        se.cost_time_comparison([se.CostRecord("t", "c", "s", baseline=[-1.0], candidate=[1.0])])


# ---------------------------------------------------------------- the D 9.2 procedure


def _metric(t_stat: float, *, spread: float = 0.1, clusters: int = 30) -> list[se.TaskPair]:
    """Diffs mu +- spread alternating: SE = spread / sqrt(clusters - 1), so t = mu / SE."""
    mu = t_stat * spread / math.sqrt(clusters - 1)
    return _one_per_cluster([mu + (spread if i % 2 else -spread) for i in range(clusters)])


def _registration(**kw: Any) -> se.Registration:
    values: dict[str, Any] = {
        "rule": se.Rule.B,
        "maintained": (se.Maintained("success", 0.03),),
        "rule_b": ("cost", "time"),
        "delta_imp_b": 0.0,
        "evidence_b": se.Evidence.E1,
    }
    values.update(kw)
    return se.Registration(**values)


def _decide(reg: se.Registration, **metrics: list[se.TaskPair]) -> se.Decision:
    data = {"success": _metric(0.0, spread=0.01), **metrics}
    return se.decide(reg, data, se.AnalysisPlan(seed=1, sensitivity=False), safety_violations=0)


def test_holm_needs_the_smaller_level_first() -> None:
    # one-sided t critical, 29 df: 2.045 at 0.025, 2.364 at 0.0125
    alone = _decide(_registration(), cost=_metric(2.2), time=_metric(0.5))
    assert not alone.adopted  # 2.2 passes alone at 0.025 but not at 0.0125 first
    both = _decide(_registration(), cost=_metric(2.2), time=_metric(3.0))
    assert both.adopted
    assert set(both.rule_b) == {"cost", "time"}  # time at 0.0125, then cost at 0.025


def test_non_inferiority_of_every_maintained_metric_is_required() -> None:
    reg = _registration(maintained=(se.Maintained("success", 0.03), se.Maintained("quality", 0.03)))
    failed = _decide(reg, cost=_metric(4.0), time=_metric(4.0), quality=_metric(-8.0))
    assert not failed.adopted
    assert ("quality", False) in {(name, met) for name, _, met in failed.stage1}


def test_degraded_within_margin_follows_the_registration() -> None:
    # estimate -0.01 with SE 0.0019 (t = -5.4): WORSE but L > -0.03
    within = _metric(-0.01 / (0.01 / math.sqrt(29)), spread=0.01)
    allowed = _registration(
        maintained=(se.Maintained("success", 0.03), se.Maintained("quality", 0.03))
    )
    forbidden = _registration(
        maintained=(
            se.Maintained("success", 0.03),
            se.Maintained("quality", 0.03, allow_degraded_within_margin=False),
        )
    )
    assert _decide(allowed, cost=_metric(4.0), time=_metric(0.0), quality=within).adopted
    assert not _decide(forbidden, cost=_metric(4.0), time=_metric(0.0), quality=within).adopted


def test_the_fixed_sequence_tests_b_only_after_a() -> None:
    reg = _registration(rule=se.Rule.A_THEN_B, evidence_a=se.Evidence.E1, delta_imp_primary=0.0)
    data = {"success": _metric(0.0, spread=0.01), "cost": _metric(5.0), "time": _metric(5.0)}
    plan = se.AnalysisPlan(seed=1, sensitivity=False)
    decision = se.decide(reg, data, plan, safety_violations=0)
    assert not decision.adopted
    assert decision.rule_a is False
    assert decision.rule_b == ()  # not tested: A failed first
    data["success"] = _metric(4.0, spread=0.01)
    assert se.decide(reg, data, plan, safety_violations=0).adopted


def test_a_safety_violation_blocks_adoption() -> None:
    reg = _registration(rule=se.Rule.A, evidence_a=se.Evidence.E1, delta_imp_primary=0.0)
    data = {"success": _metric(6.0)}
    plan = se.AnalysisPlan(seed=1, sensitivity=False)
    assert se.decide(reg, data, plan, safety_violations=0).adopted
    assert not se.decide(reg, data, plan, safety_violations=1).adopted


# ---------------------------------------------------------------- the simulation path


def test_the_simulation_uses_the_formal_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[se.AnalysisPlan] = []
    real = se.analyse

    def spy(tasks: Any, plan: se.AnalysisPlan) -> se.AnalysisOutput:
        seen.append(plan)
        return real(tasks, plan)

    monkeypatch.setattr(se, "analyse", spy)
    sim.run(sim.Scenario(name="s", effect=0.0), runs=3, seed=7)
    assert len(seen) == 3
    assert all(not plan.sensitivity for plan in seen)


def test_the_data_do_not_depend_on_the_analysis() -> None:
    s = sim.Scenario(name="s", effect=0.05, missing=0.1)
    first = [sim.generate(s, seed=11, run=r) for r in range(3)]
    again = [sim.generate(s, seed=11, run=r) for r in range(3)]
    assert first == again
    assert sim.generate(s, seed=12, run=0) != first[0]
    reports = [sim.run(s, runs=3, seed=11, analysis_seed=a, record_data=True) for a in (1, 2)]
    assert reports[0].data_digest == reports[1].data_digest
    procedure = sim.ProcedureScenario(name="p", rule=se.Rule.A, success_effect=0.0)
    metrics = sim.generate_metrics(procedure, seed=11, run=0)
    assert metrics["success"] == sim.generate(procedure.structure, seed=11, run=0)


def test_a_small_procedure_simulation_runs() -> None:
    report = sim.run_procedure(
        sim.ProcedureScenario(name="p", rule=se.Rule.A, success_effect=0.0), runs=5, seed=3
    )
    assert report.runs == 5
    assert 0.0 <= report.adopt_rate <= 1.0
    assert 0.0 < report.planned_power <= 1.0 or report.planned_power == 0.0
