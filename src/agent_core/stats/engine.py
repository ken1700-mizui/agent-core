"""Statistics Engine (WP2-07; D 8.1-8.6). Standard library only; pure.

- Unit: tasks are paired between the two configurations; the cluster
  (repository or sequence) is the resampling unit (D 8.1). Repeats estimate a
  task's success probability and are never independent samples.
- Estimate: the stratum-weighted mean of the per-task differences (D 8.2, 8.3).
- Interval: percentile cluster bootstrap (primary when clusters are many) and a
  cluster sign-flip permutation test (exact up to 16 clusters, Monte Carlo
  above), both seeded. Multiplicity: Holm. Missing pairs: reported, with a
  worst-case sensitivity value. Cost and time: negated log of the ratio of
  geometric means (positive = better).
- Decision: the three judgements and the combined label of D 8.4.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist


class Weighting(StrEnum):
    TASK_COUNT = "TASK_COUNT"
    EQUAL_STRATA = "EQUAL_STRATA"


@dataclass(frozen=True)
class TaskPair:
    task: str
    cluster: str
    stratum: str
    baseline: float  # e.g. the task's success rate under the baseline
    candidate: float

    @property
    def difference(self) -> float:
        return self.candidate - self.baseline


def weighted_difference(tasks: Sequence[TaskPair], weighting: Weighting) -> float:
    if not tasks:
        raise ValueError("no paired task")
    if weighting is Weighting.TASK_COUNT:
        return sum(t.difference for t in tasks) / len(tasks)
    strata: dict[str, list[float]] = {}
    for t in tasks:
        strata.setdefault(t.stratum, []).append(t.difference)
    return sum(sum(v) / len(v) for v in strata.values()) / len(strata)


def _clusters(tasks: Sequence[TaskPair]) -> list[list[TaskPair]]:
    grouped: dict[str, list[TaskPair]] = {}
    for t in tasks:
        grouped.setdefault(t.cluster, []).append(t)
    return [grouped[k] for k in sorted(grouped)]


def cluster_bootstrap(
    tasks: Sequence[TaskPair], weighting: Weighting, *, iterations: int, rng: random.Random
) -> list[float]:
    clusters = _clusters(tasks)
    n = len(clusters)
    if weighting is Weighting.TASK_COUNT:
        sums = [sum(t.difference for t in c) for c in clusters]
        counts = [len(c) for c in clusters]
        draws = []
        for _ in range(iterations):
            picked = rng.choices(range(n), k=n)
            draws.append(sum(sums[i] for i in picked) / sum(counts[i] for i in picked))
        return draws
    out = []
    for _ in range(iterations):
        picked = rng.choices(range(n), k=n)
        out.append(weighted_difference([t for i in picked for t in clusters[i]], weighting))
    return out


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def cluster_permutation_p(
    tasks: Sequence[TaskPair],
    weighting: Weighting,
    *,
    rng: random.Random,
    iterations: int = 10_000,
    exact_up_to: int = 16,
) -> float:
    """Two-sided p-value of the sign-flip test: under no effect, each cluster's
    differences are as likely negated (D 8.2, few clusters)."""
    if weighting is not Weighting.TASK_COUNT:
        raise ValueError("the permutation test is defined for task-count weights")
    clusters = _clusters(tasks)
    sums = [sum(t.difference for t in c) for c in clusters]
    total = sum(len(c) for c in clusters)
    observed = abs(sum(sums) / total) - 1e-12
    if len(sums) <= exact_up_to:
        flips = itertools.product((1, -1), repeat=len(sums))
        hits = sum(
            1
            for s in flips
            if abs(sum(a * b for a, b in zip(s, sums, strict=True)) / total) >= observed
        )
        return hits / (1 << len(sums))
    hits = 0
    for _ in range(iterations):
        value = sum(x if rng.random() < 0.5 else -x for x in sums) / total
        hits += abs(value) >= observed
    return (hits + 1) / (iterations + 1)


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm-adjusted p-values, in the input order."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def holm_reject(p_values: Sequence[float], *, alpha: float) -> list[bool]:
    return [p <= alpha for p in holm(p_values)]


# ---------------------------------------------------------------- t distribution


def _beta_cf(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_cf(a, b, x) / a
    return 1.0 - front * _beta_cf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: int) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: int) -> float:
    if not 0.0 < p < 1.0 or df < 1:
        raise ValueError("p in (0, 1) and df >= 1")
    low, high = -1e4, 1e4
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# ---------------------------------------------------------------- metrics, missing data


def log_ratio_pair(
    task: str, cluster: str, stratum: str, *, baseline: Sequence[float], candidate: Sequence[float]
) -> TaskPair:
    """Cost or time: the negated log of the geometric mean (higher = better)."""
    if any(v <= 0 for v in (*baseline, *candidate)) or not baseline or not candidate:
        raise ValueError("cost and time values are positive")

    def score(values: Sequence[float]) -> float:
        return -sum(math.log(v) for v in values) / len(values)

    return TaskPair(task, cluster, stratum, score(baseline), score(candidate))


@dataclass(frozen=True)
class MissingAnalysis:
    estimate: float
    worst_case: float
    missing: int


def paired_with_missing(
    complete: Sequence[TaskPair],
    *,
    missing_candidate: Iterable[str],
    missing_baseline: Iterable[str],
    weighting: Weighting,
) -> MissingAnalysis:
    """D 8.6: the estimate from complete pairs, and the worst case in which every
    task missing on either side counts as the candidate's loss (difference -1)."""
    missing = len(set(missing_candidate) | set(missing_baseline))
    estimate = weighted_difference(complete, weighting)
    worst = (sum(t.difference for t in complete) - missing) / (len(complete) + missing)
    return MissingAnalysis(estimate, worst, missing)


# ---------------------------------------------------------------- decision


@dataclass(frozen=True)
class Interval:
    low: float
    estimate: float
    high: float


@dataclass(frozen=True)
class Verdict:
    direction: str
    non_inferiority: str
    practical: str
    label: str


def classify(interval: Interval, *, delta_ni: float, delta_imp: float) -> Verdict:
    low, est, high = interval.low, interval.estimate, interval.high
    direction = "BETTER" if low > 0 else "WORSE" if high < 0 else "UNDETERMINED"
    ni = "NI_MET" if low > -delta_ni else "NI_FAILED"
    if low >= delta_imp:
        practical = "PRACTICAL_CONFIRMED"
    elif low > 0 and est >= delta_imp:
        practical = "PRACTICAL_ESTIMATE"
    elif low > 0:
        practical = "EXISTS_ONLY"
    else:
        practical = "NOT_SHOWN"
    if high < -delta_ni:
        label = "PRACTICALLY_DEGRADED"
    elif direction == "WORSE" and ni == "NI_MET":
        label = "DEGRADED_WITHIN_MARGIN"
    elif practical == "PRACTICAL_CONFIRMED":
        label = "IMPROVED_CONFIRMED"
    elif practical == "PRACTICAL_ESTIMATE":
        label = "IMPROVED_ESTIMATE"
    elif practical == "EXISTS_ONLY":
        label = "IMPROVED_EXISTS"
    elif ni == "NI_MET" and direction == "UNDETERMINED":
        label = "NON_INFERIOR"
    else:
        label = "INCONCLUSIVE"
    return Verdict(direction, ni, practical, label)


# ---------------------------------------------------------------- one analysis


@dataclass(frozen=True)
class AnalysisPlan:
    seed: int
    iterations: int = 2_000
    level: float = 0.95
    weighting: Weighting = Weighting.TASK_COUNT


@dataclass(frozen=True)
class AnalysisOutput:
    estimate: float
    low: float
    high: float
    p_value: float
    clusters: int
    tasks: int
    seed: int
    iterations: int
    method: str


def analyse(tasks: Sequence[TaskPair], plan: AnalysisPlan) -> AnalysisOutput:
    rng = random.Random(plan.seed)
    estimate = weighted_difference(tasks, plan.weighting)
    draws = cluster_bootstrap(tasks, plan.weighting, iterations=plan.iterations, rng=rng)
    tail = (1.0 - plan.level) / 2.0
    p_value = (
        cluster_permutation_p(tasks, plan.weighting, rng=rng)
        if plan.weighting is Weighting.TASK_COUNT
        else float("nan")
    )
    return AnalysisOutput(
        estimate=estimate,
        low=percentile(draws, tail),
        high=percentile(draws, 1.0 - tail),
        p_value=p_value,
        clusters=len(_clusters(tasks)),
        tasks=len(tasks),
        seed=plan.seed,
        iterations=plan.iterations,
        method="cluster_bootstrap_percentile",
    )


def normal_quantile(p: float) -> float:
    return NormalDist().inv_cdf(p)
