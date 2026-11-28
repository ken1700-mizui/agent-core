"""WP2-07 formal re-verification (D19), pre-registered before it was run.

Everything here is fixed by ``docs/design/phase-2/WP2-07_PREREGISTRATION.md``
and committed before the first run: the method (``engine.analyse``: cluster t
with C - 1 df at >= 30 clusters, cluster sign-flip inversion below), the
scenarios, the data seeds (20261201-20261215, in the order below), the
analysis seeds (20261301-20261315), 2,000 runs per scenario and the D 16.1
criteria. No seed is replaced after a result is seen; a failure is reported,
not retried.

Criteria (D 16.1, unchanged; alpha = 0.025, one side of the 95% interval):
- false positive rate <= alpha + 3 x sqrt(alpha (1 - alpha) / runs)
  (BETTER with no effect; PRACTICAL_CONFIRMED at effect = delta_imp; adoption
  by the full D 9.2 procedure under its null);
- coverage of the 95% interval of the true effect: 0.93 to 0.97;
- power at Delta_assumed (one metric, and the full procedure): within 5 points
  of the planned analytic value;
- cluster dependence: the interval is wider than one treating tasks as
  independent, with coverage kept.
"""

from __future__ import annotations

import pytest

from agent_core.stats import engine as se
from agent_core.stats import simulation as sim

pytestmark = pytest.mark.simulation

RUNS, ALPHA, DELTA_IMP, ASSUMED = 2_000, 0.025, 0.05, 0.10
BOUND = ALPHA + 3 * (ALPHA * (1 - ALPHA) / RUNS) ** 0.5
_S = sim.Scenario
SINGLE = (  # (scenario, criterion)
    (_S(name="no_effect", effect=0.0), "fpr"),
    (_S(name="at_delta_imp", effect=DELTA_IMP), "confirmed"),
    (_S(name="assumed", effect=ASSUMED), "power"),
    (_S(name="clusters", effect=0.0, sigma_effect=0.08), "fpr_wider"),
    (_S(name="missing", effect=0.0, missing=0.10), "fpr"),
    (_S(name="drift", effect=0.0, drift_per_block=0.03), "fpr"),
    (_S(name="unequal_sizes", effect=0.0, tasks_range=(1, 8)), "fpr"),
    (_S(name="strata", effect=0.0, strata=3, weighting=se.Weighting.EQUAL_STRATA), "fpr"),
    (_S(name="few_clusters", effect=0.0, clusters=12), "fpr"),
    (_S(name="few_clusters_at_delta_imp", effect=DELTA_IMP, clusters=12), "confirmed"),
)
_P = sim.ProcedureScenario
PROCEDURE = (
    (_P(name="rule_a_null", rule=se.Rule.A, success_effect=0.0), "fpr"),
    (
        _P(name="rule_a_ni_boundary", rule=se.Rule.A, success_effect=0.15, quality_effect=-3.0),
        "fpr",
    ),
    (_P(name="rule_a_assumed", rule=se.Rule.A, success_effect=ASSUMED), "power"),
    (
        _P(name="rule_b_holm_null", rule=se.Rule.B, success_effect=0.0, evidence_b=se.Evidence.E1),
        "fpr",
    ),
    (
        _P(
            name="rule_a_then_b_null",
            rule=se.Rule.A_THEN_B,
            success_effect=0.0,
            evidence_b=se.Evidence.E1,
        ),
        "fpr",
    ),
)
NAMES = [s.name for s, _ in SINGLE] + [s.name for s, _ in PROCEDURE]
DATA_SEEDS = {name: 20261201 + i for i, name in enumerate(NAMES)}
ANALYSIS_SEEDS = {name: seed + 100 for name, seed in DATA_SEEDS.items()}


def test_the_registration_is_complete() -> None:
    assert len(DATA_SEEDS) == 15
    assert sorted(DATA_SEEDS.values()) == list(range(20261201, 20261216))


@pytest.mark.parametrize("index", range(len(SINGLE)), ids=[s.name for s, _ in SINGLE])
def test_one_metric(index: int) -> None:
    scenario, criterion = SINGLE[index]
    report = sim.run(
        scenario,
        runs=RUNS,
        seed=DATA_SEEDS[scenario.name],
        analysis_seed=ANALYSIS_SEEDS[scenario.name],
        delta_imp=DELTA_IMP,
    )
    print(f"SIM {report}")
    assert 0.93 <= report.coverage <= 0.97, report
    if criterion.startswith("fpr"):
        assert report.better_rate <= BOUND, report
    elif criterion == "confirmed":
        assert report.confirmed_rate <= BOUND, report
    else:
        assert abs(report.better_rate - report.planned_power) <= 0.05, report
    if criterion == "fpr_wider":
        assert report.mean_width > report.naive_width, report


@pytest.mark.parametrize("index", range(len(PROCEDURE)), ids=[s.name for s, _ in PROCEDURE])
def test_the_full_procedure(index: int) -> None:
    scenario, criterion = PROCEDURE[index]
    report = sim.run_procedure(
        scenario,
        runs=RUNS,
        seed=DATA_SEEDS[scenario.name],
        analysis_seed=ANALYSIS_SEEDS[scenario.name],
    )
    print(f"PROC {report}")
    if criterion == "fpr":
        assert report.adopt_rate <= BOUND, report
    else:
        assert abs(report.adopt_rate - report.planned_power) <= 0.05, report
