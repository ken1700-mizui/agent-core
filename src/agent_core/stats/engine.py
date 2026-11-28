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
import statistics
from collections.abc import Iterable, Mapping, Sequence
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


# ---------------------------------------------------------------- cluster scores (D19)


class InsufficientClustersError(ValueError):
    """An interval needs at least two clusters."""


class Method(StrEnum):
    CLUSTER_T = "CLUSTER_T"  # clusters >= the registered threshold (D 8.2)
    CLUSTER_SIGN_FLIP = "CLUSTER_SIGN_FLIP"  # fewer clusters (D 8.2: permutation)


@dataclass(frozen=True)
class ClusterScores:
    """Per-cluster linearisation of the weighted mean difference.

    With stratum weights a_h (1 for task-count weights, 1/H for equal strata)
    and n_h tasks in stratum h, task i contributes w_i = a_h / n_h. For each
    cluster c: ``numerators`` P_c = sum w_i d_i, ``weights`` Q_c = sum w_i
    (they sum to 1), ``residuals`` e_c = sum w_i (d_i - mean_h). The estimate is
    sum P_c = sum_h a_h mean_h, the D 8.2 weighted mean.
    """

    clusters: tuple[str, ...]
    numerators: tuple[float, ...]
    weights: tuple[float, ...]
    residuals: tuple[float, ...]
    estimate: float

    @property
    def degenerate(self) -> bool:
        """No variation between clusters: no interval quantifies uncertainty."""
        return all(abs(e) <= 1e-12 for e in self.residuals)


def cluster_scores(tasks: Sequence[TaskPair], weighting: Weighting) -> ClusterScores:
    if not tasks:
        raise ValueError("no paired task")

    def stratum(t: TaskPair) -> str:
        return "*" if weighting is Weighting.TASK_COUNT else t.stratum

    counts: dict[str, int] = {}
    sums: dict[str, float] = {}
    for t in tasks:
        h = stratum(t)
        counts[h] = counts.get(h, 0) + 1
        sums[h] = sums.get(h, 0.0) + t.difference
    share = 1.0 / len(counts)
    means = {h: sums[h] / counts[h] for h in counts}
    p: dict[str, float] = {}
    q: dict[str, float] = {}
    e: dict[str, float] = {}
    for t in tasks:
        h = stratum(t)
        w = share / counts[h]
        p[t.cluster] = p.get(t.cluster, 0.0) + w * t.difference
        q[t.cluster] = q.get(t.cluster, 0.0) + w
        e[t.cluster] = e.get(t.cluster, 0.0) + w * (t.difference - means[h])
    order = tuple(sorted(p))
    return ClusterScores(
        clusters=order,
        numerators=tuple(p[c] for c in order),
        weights=tuple(q[c] for c in order),
        residuals=tuple(e[c] for c in order),
        estimate=sum(share * m for m in means.values()),
    )


@dataclass(frozen=True)
class IntervalResult:
    method: Method
    low: float
    estimate: float
    high: float
    degenerate: bool
    df: int | None = None
    standard_error: float | None = None
    permutations: int | None = None
    exact: bool | None = None
    p_zero: float = 1.0  # two-sided p-value of H0: effect = 0 (1.0 when degenerate)

    def p_greater(self, delta0: float) -> float:
        """One-sided p-value of H0: effect <= delta0 (cluster t only)."""
        if self.df is None or self.standard_error is None or self.degenerate:
            raise ValueError("a p-value needs a non-degenerate cluster t interval")
        return 1.0 - t_cdf((self.estimate - delta0) / self.standard_error, self.df)


def _need_two(scores: ClusterScores) -> int:
    clusters = len(scores.clusters)
    if clusters < 2:
        raise InsufficientClustersError("an interval needs at least two clusters")
    return clusters


def cluster_t_interval(scores: ClusterScores, *, level: float) -> IntervalResult:
    """Cluster-robust (CR1) standard error with C/(C-1) and Student t with C-1
    degrees of freedom: the variance is estimated between clusters only."""
    clusters = _need_two(scores)
    variance = clusters / (clusters - 1) * sum(e * e for e in scores.residuals)
    error = math.sqrt(variance)
    est = scores.estimate
    if scores.degenerate:
        return IntervalResult(Method.CLUSTER_T, est, est, est, True, clusters - 1, 0.0)
    half = t_quantile(1.0 - (1.0 - level) / 2.0, clusters - 1) * error
    upper_tail = 1.0 - t_cdf(est / error, clusters - 1)
    return IntervalResult(
        Method.CLUSTER_T,
        est - half,
        est,
        est + half,
        False,
        clusters - 1,
        error,
        p_zero=min(1.0, 2.0 * min(upper_tail, 1.0 - upper_tail)),
    )


def cluster_sign_flip_interval(
    scores: ClusterScores,
    *,
    level: float,
    rng: random.Random,
    iterations: int = 9_999,
    exact_up_to: int = 16,
) -> IntervalResult:
    """Inversion of the cluster sign-flip test (D 8.2, few clusters).

    Under H0 (effect = d0) each cluster's contribution P_c - d0 Q_c is as likely
    negated. Flipping the set F moves the statistic below the observed one iff
    the subset mean r_F = sum_F P_c / sum_F Q_c <= d0, so the one-sided p-value
    is (1 + #{r_F <= d0}) / M and the bounds are order statistics of the subset
    means (Hartigan's construction; exact for all 2**C sign patterns, Monte
    Carlo with ``iterations`` random non-empty patterns above ``exact_up_to``).
    """
    clusters = _need_two(scores)
    p, q = scores.numerators, scores.weights
    if clusters <= exact_up_to:
        sp, sq = [0.0], [0.0]
        for a, b in zip(p, q, strict=True):
            sp += [x + a for x in sp]
            sq += [y + b for y in sq]
        ratios = [a / b for a, b in zip(sp[1:], sq[1:], strict=True)]
        patterns, exact = 1 << clusters, True
    else:
        ratios = []
        for _ in range(iterations):
            picked: list[int] = []
            while not picked:
                picked = [i for i in range(clusters) if rng.random() < 0.5]
            ratios.append(sum(p[i] for i in picked) / sum(q[i] for i in picked))
        patterns, exact = iterations + 1, False
    ratios.sort()
    tail = (1.0 - level) / 2.0
    allowed = math.floor(tail * patterns + 1e-9) - 1
    if allowed < 0:
        low, high = -math.inf, math.inf
    else:
        low, high = ratios[allowed], ratios[len(ratios) - 1 - allowed]
    below = (1 + sum(r <= 0.0 for r in ratios)) / patterns
    above = (1 + sum(r >= 0.0 for r in ratios)) / patterns
    return IntervalResult(
        Method.CLUSTER_SIGN_FLIP,
        low,
        scores.estimate,
        high,
        scores.degenerate,
        permutations=patterns,
        exact=exact,
        p_zero=1.0 if scores.degenerate else min(1.0, 2.0 * min(below, above)),
    )


def naive_task_interval(tasks: Sequence[TaskPair], *, level: float) -> float:
    """Width of a normal interval that wrongly treats tasks as independent
    (a diagnostic of D 16.1 only; never used to decide)."""
    diffs = [t.difference for t in tasks]
    z = normal_quantile(1.0 - (1.0 - level) / 2.0)
    return 2 * z * statistics.pstdev(diffs) / math.sqrt(len(diffs))


# ---------------------------------------------------------------- one analysis


@dataclass(frozen=True)
class AnalysisPlan:
    seed: int
    iterations: int = 2_000
    level: float = 0.95
    weighting: Weighting = Weighting.TASK_COUNT
    min_clusters_for_t: int = 30  # D 8.2 candidate (PROVISIONAL)
    sensitivity: bool = True  # D 8.2: also report the other method; never decides


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
    degenerate: bool = False
    df: int | None = None
    standard_error: float | None = None
    exact: bool | None = None
    sensitivity: tuple[tuple[str, float, float], ...] = ()


def _primary(scores: ClusterScores, plan: AnalysisPlan, rng: random.Random) -> IntervalResult:
    if len(scores.clusters) >= plan.min_clusters_for_t:
        return cluster_t_interval(scores, level=plan.level)
    return cluster_sign_flip_interval(scores, level=plan.level, rng=rng, iterations=plan.iterations)


def analyse(tasks: Sequence[TaskPair], plan: AnalysisPlan) -> AnalysisOutput:
    """The registered analysis (D19): cluster t when there are at least
    ``min_clusters_for_t`` clusters, otherwise the cluster sign-flip inversion.
    Sensitivity analyses run after the primary one on the same generator, so
    they never change it."""
    rng = random.Random(plan.seed)
    scores = cluster_scores(tasks, plan.weighting)
    primary = _primary(scores, plan, rng)
    extra: list[tuple[str, float, float]] = []
    if plan.sensitivity:
        if primary.method is Method.CLUSTER_T:
            other = cluster_sign_flip_interval(
                scores, level=plan.level, rng=rng, iterations=plan.iterations
            )
        else:
            other = cluster_t_interval(scores, level=plan.level)
        extra.append((other.method.value, other.low, other.high))
        draws = cluster_bootstrap(tasks, plan.weighting, iterations=plan.iterations, rng=rng)
        tail = (1.0 - plan.level) / 2.0
        extra.append(
            ("cluster_bootstrap_percentile", percentile(draws, tail), percentile(draws, 1 - tail))
        )
        if plan.weighting is Weighting.TASK_COUNT and len(scores.clusters) >= 2:
            p_perm = cluster_permutation_p(tasks, plan.weighting, rng=rng)
            extra.append(("cluster_sign_flip_p_two_sided", p_perm, p_perm))
    return AnalysisOutput(
        estimate=primary.estimate,
        low=primary.low,
        high=primary.high,
        p_value=primary.p_zero,
        clusters=len(scores.clusters),
        tasks=len(tasks),
        seed=plan.seed,
        iterations=plan.iterations,
        method=primary.method.value,
        degenerate=primary.degenerate,
        df=primary.df,
        standard_error=primary.standard_error,
        exact=primary.exact,
        sensitivity=tuple(extra),
    )


def verdict_of(output: AnalysisOutput, *, delta_ni: float, delta_imp: float) -> Verdict:
    """D 8.4 on the registered interval. A degenerate interval (no variation
    between clusters) decides nothing: fail closed (NI_FAILED, INCONCLUSIVE)."""
    if output.degenerate:
        return Verdict("UNDETERMINED", "NI_FAILED", "NOT_SHOWN", "INCONCLUSIVE")
    return classify(
        Interval(output.low, output.estimate, output.high), delta_ni=delta_ni, delta_imp=delta_imp
    )


# ---------------------------------------------------------------- missing data, zeros (D 8.6, 12.3)


@dataclass(frozen=True)
class MissingTask:
    task: str
    cluster: str
    stratum: str


def worst_case_pairs(
    complete: Sequence[TaskPair], missing: Sequence[MissingTask]
) -> list[TaskPair]:
    """D 8.6 worst case for a success rate: the candidate fails (0) and the
    baseline succeeds (1) on every missing task."""
    return [
        *complete,
        *(TaskPair(m.task, m.cluster, m.stratum, baseline=1.0, candidate=0.0) for m in missing),
    ]


@dataclass(frozen=True)
class MissingSensitivity:
    complete: AnalysisOutput
    worst: AnalysisOutput
    complete_label: str
    worst_label: str
    missing: int

    @property
    def robust(self) -> bool:
        return self.complete_label == self.worst_label


def missing_sensitivity(
    complete: Sequence[TaskPair],
    missing: Sequence[MissingTask],
    plan: AnalysisPlan,
    *,
    delta_ni: float,
    delta_imp: float,
) -> MissingSensitivity:
    first = analyse(complete, plan)
    worst = analyse(worst_case_pairs(complete, missing), plan)
    return MissingSensitivity(
        complete=first,
        worst=worst,
        complete_label=verdict_of(first, delta_ni=delta_ni, delta_imp=delta_imp).label,
        worst_label=verdict_of(worst, delta_ni=delta_ni, delta_imp=delta_imp).label,
        missing=len(missing),
    )


@dataclass(frozen=True)
class CostRecord:
    task: str
    cluster: str
    stratum: str
    baseline: Sequence[float]
    candidate: Sequence[float]


@dataclass(frozen=True)
class CostTimeComparison:
    log_pairs: tuple[TaskPair, ...]  # negated log of geometric means (higher = better)
    absolute_pairs: tuple[TaskPair, ...]  # negated means (higher = cheaper, faster)
    excluded_zero: int  # tasks left out of the log analysis (D 12.3)


def cost_time_comparison(records: Sequence[CostRecord]) -> CostTimeComparison:
    """D 12.3: a zero cost or time is left out of the log analysis and counted;
    the absolute difference is always given."""
    logs: list[TaskPair] = []
    absolute: list[TaskPair] = []
    excluded = 0
    for r in records:
        values = (*r.baseline, *r.candidate)
        if not r.baseline or not r.candidate:
            raise ValueError("cost and time need values on both sides")
        if any(v < 0 for v in values):
            raise ValueError("cost and time are never negative")
        absolute.append(
            TaskPair(
                r.task,
                r.cluster,
                r.stratum,
                baseline=-statistics.fmean(r.baseline),
                candidate=-statistics.fmean(r.candidate),
            )
        )
        if any(v == 0 for v in values):
            excluded += 1
            continue
        logs.append(
            log_ratio_pair(r.task, r.cluster, r.stratum, baseline=r.baseline, candidate=r.candidate)
        )
    return CostTimeComparison(tuple(logs), tuple(absolute), excluded)


# ---------------------------------------------------------------- the D 9.2 procedure


class Evidence(StrEnum):
    E1 = "E1"  # EXISTS_ONLY or better
    E2 = "E2"  # PRACTICAL_ESTIMATE or better
    E3 = "E3"  # PRACTICAL_CONFIRMED


_EVIDENCE = {
    Evidence.E1: {"EXISTS_ONLY", "PRACTICAL_ESTIMATE", "PRACTICAL_CONFIRMED"},
    Evidence.E2: {"PRACTICAL_ESTIMATE", "PRACTICAL_CONFIRMED"},
    Evidence.E3: {"PRACTICAL_CONFIRMED"},
}


class Rule(StrEnum):
    A = "A"
    B = "B"
    A_THEN_B = "A_THEN_B"  # fixed sequence: B is tested only after A succeeds


@dataclass(frozen=True)
class Maintained:
    name: str
    delta_ni: float
    allow_degraded_within_margin: bool = True  # D 9.2 default


@dataclass(frozen=True)
class Registration:
    rule: Rule
    maintained: tuple[Maintained, ...]
    primary: str = "success"
    delta_imp_primary: float = 0.05
    evidence_a: Evidence = Evidence.E2
    rule_b: tuple[str, ...] = ()
    delta_imp_b: float = 0.0
    evidence_b: Evidence = Evidence.E2
    alpha_one_sided: float = 0.025  # one side of the two-sided 95% interval


@dataclass(frozen=True)
class Decision:
    adopted: bool
    stage1: tuple[tuple[str, str, bool], ...]
    rule_a: bool | None
    rule_b: tuple[str, ...]
    reasons: tuple[str, ...]


def _at_level(plan: AnalysisPlan, alpha_one_sided: float) -> AnalysisPlan:
    return AnalysisPlan(
        seed=plan.seed,
        iterations=plan.iterations,
        level=1.0 - 2.0 * alpha_one_sided,
        weighting=plan.weighting,
        min_clusters_for_t=plan.min_clusters_for_t,
        sensitivity=plan.sensitivity,
    )


def _meets(
    tasks: Sequence[TaskPair], plan: AnalysisPlan, alpha: float, delta_imp: float, e: Evidence
) -> bool:
    out = analyse(tasks, _at_level(plan, alpha))
    return verdict_of(out, delta_ni=math.inf, delta_imp=delta_imp).practical in _EVIDENCE[e]


def decide(
    registration: Registration,
    data: Mapping[str, Sequence[TaskPair]],
    plan: AnalysisPlan,
    *,
    safety_violations: int,
) -> Decision:
    """D 9.2: stage 1, every maintained metric non-inferior (intersection-union,
    each at the registered alpha); stage 2, the registered rule at its evidence
    level (rule B over several metrics with Holm; A then B as a fixed
    sequence). A safety violation blocks adoption (Safety Gate)."""
    alpha = registration.alpha_one_sided
    reasons: list[str] = []
    if safety_violations != 0:
        reasons.append("SAFETY_VIOLATION")
    stage1: list[tuple[str, str, bool]] = []
    for m in registration.maintained:
        out = analyse(data[m.name], _at_level(plan, alpha))
        verdict = verdict_of(out, delta_ni=m.delta_ni, delta_imp=math.inf)
        met = verdict.non_inferiority == "NI_MET" and (
            m.allow_degraded_within_margin or verdict.label != "DEGRADED_WITHIN_MARGIN"
        )
        stage1.append((m.name, verdict.label, met))
        if not met:
            reasons.append(f"NI_NOT_MET:{m.name}")
    rule_a: bool | None = None
    rule_b: tuple[str, ...] = ()
    if registration.rule in (Rule.A, Rule.A_THEN_B):
        rule_a = _meets(
            data[registration.primary],
            plan,
            alpha,
            registration.delta_imp_primary,
            registration.evidence_a,
        )
    run_b = registration.rule is Rule.B or (registration.rule is Rule.A_THEN_B and rule_a)
    if run_b:
        remaining = list(registration.rule_b)
        passed: list[str] = []
        while remaining:  # Holm: alpha / (number still untested) at each step
            step = alpha / len(remaining)
            newly = [
                name
                for name in remaining
                if _meets(data[name], plan, step, registration.delta_imp_b, registration.evidence_b)
            ]
            if not newly:
                break
            passed += newly
            remaining = [name for name in remaining if name not in newly]
        rule_b = tuple(passed)
    improved = bool(rule_b) if registration.rule is Rule.B else bool(rule_a)
    if not improved:
        reasons.append("IMPROVEMENT_NOT_SHOWN")
    adopted = not reasons
    return Decision(adopted, tuple(stage1), rule_a, rule_b, tuple(reasons))


def normal_quantile(p: float) -> float:
    return NormalDist().inv_cdf(p)
