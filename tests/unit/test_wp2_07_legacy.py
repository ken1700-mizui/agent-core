"""WP2-07 (D19): the original failure is kept and reproduced, and the fixed
method is applied to exactly the datasets that failed (``simulation``).

- ``test_the_original_failure_is_reproduced``: the original implementation
  (``tests/support/wp2_07_legacy.py``, commit 9b2663c) on the original seeds
  gives the recorded values, including the failure (0.0365 > 0.0355).
- ``test_the_fixed_method_on_the_original_datasets``: the same 2,000 datasets
  per scenario, analysed by ``engine.analyse`` (analysis seed derived from the
  original seed). The D 16.1 criteria apply unchanged (pre-registered in
  ``docs/design/phase-2/WP2-07_PREREGISTRATION.md``). Paired with the original
  intervals, so method bias is separated from Monte Carlo error.
"""

from __future__ import annotations

import dataclasses
import math
import statistics

import pytest

from agent_core.stats import engine as se
from agent_core.stats import simulation as sim
from support import wp2_07_legacy as legacy

pytestmark = pytest.mark.simulation

RUNS, ITERATIONS, ALPHA, DELTA_IMP = 2_000, 500, 0.025, 0.05
ORIGINAL = {  # name: (scenario, seed, recorded better, confirmed, coverage), commit 9b2663c
    "no_effect": (legacy.Scenario(name="no_effect", effect=0.0), 20261128, 0.03, 0.0, 0.937),
    "at_delta_imp": (
        legacy.Scenario(name="at_delta_imp", effect=DELTA_IMP),
        20261129,
        0.3895,
        0.0365,
        0.9355,
    ),
    "assumed": (legacy.Scenario(name="assumed", effect=0.10), 20261130, 0.8985, 0.392, 0.9475),
    "clusters": (
        legacy.Scenario(name="clusters", effect=0.0, sigma_effect=0.08),
        20261131,
        0.032,
        0.0005,
        0.9345,
    ),
    "missing": (
        legacy.Scenario(name="missing", effect=0.0, missing=0.10),
        20261132,
        0.0295,
        0.0,
        0.9435,
    ),
    "drift": (
        legacy.Scenario(name="drift", effect=0.0, drift_per_block=0.03),
        20261133,
        0.029,
        0.0,
        0.935,
    ),
}


def _bound(runs: int) -> float:
    return ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / runs)


@pytest.mark.parametrize("name", sorted(ORIGINAL))
def test_the_original_failure_is_reproduced(name: str) -> None:
    scenario, seed, better, confirmed, coverage = ORIGINAL[name]
    report = legacy.run(scenario, runs=RUNS, seed=seed, iterations=ITERATIONS, delta_imp=DELTA_IMP)
    print(f"LEGACY {report}")
    assert (report.better_rate, report.confirmed_rate, report.coverage) == (
        better,
        confirmed,
        coverage,
    )
    if name == "at_delta_imp":
        assert report.confirmed_rate > _bound(RUNS)  # the recorded failure, unchanged


@pytest.mark.parametrize("name", sorted(ORIGINAL))
def test_the_fixed_method_on_the_original_datasets(name: str) -> None:
    scenario, seed, *_ = ORIGINAL[name]
    datasets = legacy.legacy_datasets(scenario, runs=RUNS, seed=seed, iterations=ITERATIONS)
    better = confirmed = covered = old_covered = only_old = only_new = 0
    widths: list[float] = []
    naive: list[float] = []
    for r, (tasks, old_low, old_high) in enumerate(datasets):
        plan = se.AnalysisPlan(seed=sim.derive("analysis", seed, r), sensitivity=False)
        out = se.analyse(tasks, plan)
        verdict = se.verdict_of(out, delta_ni=sim.NI_SUCCESS, delta_imp=DELTA_IMP)
        better += verdict.direction == "BETTER"
        confirmed += verdict.practical == "PRACTICAL_CONFIRMED"
        new_in = out.low <= scenario.effect <= out.high
        old_in = old_low <= scenario.effect <= old_high
        covered += new_in
        old_covered += old_in
        widths.append(out.high - out.low)
        naive.append(se.naive_task_interval(tasks, level=0.95))
        only_old += old_in and not new_in
        only_new += new_in and not old_in
    rates = (better / RUNS, confirmed / RUNS, covered / RUNS)
    planned = sim.planned_power(sim.Scenario(**dataclasses.asdict(scenario)))
    print(
        f"PAIRED {name} seed={seed} new(better, confirmed, coverage)={rates}"
        f" old_coverage={old_covered / RUNS} covered_only_by_old={only_old}"
        f" covered_only_by_new={only_new} planned={planned}"
        f" width={statistics.fmean(widths)} naive={statistics.fmean(naive)}"
    )
    assert 0.93 <= rates[2] <= 0.97
    if name == "at_delta_imp":
        assert rates[1] <= _bound(RUNS)
    elif name == "assumed":
        assert abs(rates[0] - planned) <= 0.05
    else:
        assert rates[0] <= _bound(RUNS)
    if name == "clusters":
        assert statistics.fmean(widths) > statistics.fmean(naive)
