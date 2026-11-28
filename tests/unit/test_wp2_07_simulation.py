"""WP2-07: the D 16.1 simulation (``simulation`` category; minutes).

Criteria fixed before running (D 16.1; values PROVISIONAL in D):
- false positive rate (adoption = BETTER, i.e. L > 0, with no effect; and
  PRACTICAL_CONFIRMED with the effect exactly delta_imp): at most
  alpha + 3 x Monte Carlo error, alpha = 0.025 (one side of a 95% interval);
- coverage of the 95% interval: 0.93 to 0.97;
- power at the assumed effect: within 5 points of the planned (analytic) value;
- cluster dependence: the cluster interval is wider than one treating tasks as
  independent, and keeps its coverage.
Scenarios: no effect, effect = delta_imp, effect = Delta_assumed, clusters
(stronger cluster heterogeneity), missing pairs (10%), time drift. 2,000 runs
each, 500 bootstrap draws, fixed seeds. Results are recorded in
``docs/design/phase-2/WP2-07_STATISTICS.md``; the criteria are not changed
after seeing them.
"""

from __future__ import annotations

import pytest

from agent_core.stats import simulation as sim

pytestmark = pytest.mark.simulation

RUNS, ITERATIONS, ALPHA, DELTA_IMP, ASSUMED = 2_000, 500, 0.025, 0.05, 0.10
SCENARIOS = {
    "no_effect": (sim.Scenario(name="no_effect", effect=0.0), 20261128),
    "at_delta_imp": (sim.Scenario(name="at_delta_imp", effect=DELTA_IMP), 20261129),
    "assumed": (sim.Scenario(name="assumed", effect=ASSUMED), 20261130),
    "clusters": (sim.Scenario(name="clusters", effect=0.0, sigma_effect=0.08), 20261131),
    "missing": (sim.Scenario(name="missing", effect=0.0, missing=0.10), 20261132),
    "drift": (sim.Scenario(name="drift", effect=0.0, drift_per_block=0.03), 20261133),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_d_16_1(name: str) -> None:
    scenario, seed = SCENARIOS[name]
    report = sim.run(scenario, runs=RUNS, seed=seed, iterations=ITERATIONS, delta_imp=DELTA_IMP)
    print(f"SIM {report}")  # recorded in the WP2-07 document
    assert 0.93 <= report.coverage <= 0.97, report
    if name == "at_delta_imp":
        assert report.confirmed_rate <= ALPHA + 3 * report.monte_carlo_error(ALPHA), report
    elif name == "assumed":
        assert abs(report.better_rate - report.planned_power) <= 0.05, report
    else:
        assert report.better_rate <= ALPHA + 3 * report.monte_carlo_error(ALPHA), report
    if name == "clusters":
        assert report.mean_width > report.naive_width, report
