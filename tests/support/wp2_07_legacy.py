"""The ORIGINAL WP2-07 simulation (commit 9b2663c), kept unchanged so that the
recorded failure can be reproduced (D19: never delete the failing record).

Not used by the product. ``legacy_datasets`` replays the original single
random stream (data generation interleaved with 500 bootstrap draws per run)
so the new method can be compared on exactly the datasets that failed.

The original module's docstring follows.

D 16.1 statistical simulation of the decision procedure (WP2-07).

Synthetic data with the D 8.1 structure: clusters, tasks in clusters, repeats
per task, two configurations on the same tasks. Per run: success
probabilities ``p = clip(0.5 + u_cluster + v_task)``, the candidate's
``p + effect + w_cluster`` (cluster-level heterogeneity of the effect),
binomial successes over the repeats; optional missing pairs (MCAR) and time
drift shared by both configurations within a block. The procedure under test
is the one of ``engine``: task-count weights, percentile cluster bootstrap,
the D 8.4 labels.

Measured per scenario: the rate of BETTER (L > 0) and of PRACTICAL_CONFIRMED
(L >= delta_imp), the coverage of the true effect by the 95% interval, the
mean interval width and the width of a naive interval that treats tasks as
independent, the planned power from an analytic normal approximation (an
independent calculation, not from this simulation), the seed, the runs and
the wall time. Nothing here decides pass or fail: the criteria of D 16.1 are
fixed in the tests and the analysis plan beforehand.
"""

from __future__ import annotations

import math
import random
import statistics
import time
from dataclasses import dataclass
from statistics import NormalDist

from agent_core.stats import engine as se


@dataclass(frozen=True)
class Scenario:
    name: str
    effect: float
    clusters: int = 40
    tasks_per_cluster: int = 4
    repeats: int = 3
    sigma_cluster: float = 0.10
    sigma_task: float = 0.10
    sigma_effect: float = 0.03  # cluster-level heterogeneity of the effect
    missing: float = 0.0
    drift_per_block: float = 0.0
    blocks: int = 4


@dataclass(frozen=True)
class SimReport:
    name: str
    runs: int
    seed: int
    iterations: int
    better_rate: float
    confirmed_rate: float
    coverage: float
    mean_width: float
    naive_width: float
    planned_power: float
    seconds: float

    @property
    def adopt_rate(self) -> float:
        return self.better_rate

    def monte_carlo_error(self, rate: float) -> float:
        return math.sqrt(max(rate * (1.0 - rate), 1e-12) / self.runs)


def _clip(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _one_run(s: Scenario, rng: random.Random) -> list[se.TaskPair]:
    tasks: list[se.TaskPair] = []
    for c in range(s.clusters):
        u = rng.gauss(0.0, s.sigma_cluster)
        w = rng.gauss(0.0, s.sigma_effect)
        for t in range(s.tasks_per_cluster):
            if s.missing and rng.random() < s.missing:
                continue
            block = (c * s.tasks_per_cluster + t) % s.blocks
            shift = s.drift_per_block * block
            p = _clip(0.45 + u + rng.gauss(0.0, s.sigma_task) + shift, 0.10, 0.80)
            q = _clip(p + s.effect + w, 0.0, 1.0)
            base = sum(rng.random() < p for _ in range(s.repeats)) / s.repeats
            cand = sum(rng.random() < q for _ in range(s.repeats)) / s.repeats
            tasks.append(se.TaskPair(f"t{c}_{t}", f"c{c}", "all", base, cand))
    return tasks


def planned_power(
    s: Scenario, *, level: float = 0.95, draws: int = 20_000, seed: int = 99
) -> float:
    """Analytic normal approximation: SE of the cluster mean difference from the
    variance components (binomial variance averaged over p, effect heterogeneity)."""
    rng = random.Random(seed)
    binomial = 0.0
    for _ in range(draws):
        p = _clip(0.45 + rng.gauss(0.0, s.sigma_cluster) + rng.gauss(0.0, s.sigma_task), 0.10, 0.80)
        q = _clip(p + s.effect, 0.0, 1.0)
        binomial += p * (1 - p) + q * (1 - q)
    binomial /= draws
    tasks = s.tasks_per_cluster * (1.0 - s.missing)
    per_cluster = s.sigma_effect**2 + binomial / (s.repeats * tasks)
    se_ = math.sqrt(per_cluster / s.clusters)
    z = se.normal_quantile(1.0 - (1.0 - level) / 2.0)
    return NormalDist().cdf(s.effect / se_ - z) if s.effect > 0 else 0.0


def run(
    s: Scenario,
    *,
    runs: int,
    seed: int,
    iterations: int = 500,
    delta_imp: float = 0.05,
    level: float = 0.95,
) -> SimReport:
    rng = random.Random(seed)
    started = time.perf_counter()
    better = confirmed = covered = 0
    widths: list[float] = []
    naive: list[float] = []
    tail = (1.0 - level) / 2.0
    z = se.normal_quantile(1.0 - tail)
    for _ in range(runs):
        tasks = _one_run(s, rng)
        draws = se.cluster_bootstrap(tasks, se.Weighting.TASK_COUNT, iterations=iterations, rng=rng)
        low, high = se.percentile(draws, tail), se.percentile(draws, 1.0 - tail)
        better += low > 0
        confirmed += low >= delta_imp
        covered += low <= s.effect <= high
        widths.append(high - low)
        diffs = [t.difference for t in tasks]
        naive.append(2 * z * statistics.pstdev(diffs) / math.sqrt(len(diffs)))
    return SimReport(
        name=s.name,
        runs=runs,
        seed=seed,
        iterations=iterations,
        better_rate=better / runs,
        confirmed_rate=confirmed / runs,
        coverage=covered / runs,
        mean_width=statistics.fmean(widths),
        naive_width=statistics.fmean(naive),
        planned_power=planned_power(s, level=level),
        seconds=time.perf_counter() - started,
    )


def legacy_datasets(
    s: Scenario, *, runs: int, seed: int, iterations: int = 500, level: float = 0.95
) -> list[tuple[list[se.TaskPair], float, float]]:
    """The exact datasets of ``run`` with the original interval of each run:
    the same stream, the bootstrap draws consumed in the same order."""
    rng = random.Random(seed)
    tail = (1.0 - level) / 2.0
    out = []
    for _ in range(runs):
        tasks = _one_run(s, rng)
        draws = se.cluster_bootstrap(tasks, se.Weighting.TASK_COUNT, iterations=iterations, rng=rng)
        out.append((tasks, se.percentile(draws, tail), se.percentile(draws, 1.0 - tail)))
    return out
