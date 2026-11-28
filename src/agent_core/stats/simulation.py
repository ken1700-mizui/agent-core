"""D 16.1 statistical simulation of the decision procedure (WP2-07, fixed by D19).

Synthetic data with the D 8.1 structure: clusters, tasks in clusters, repeats
per task, two configurations on the same tasks. Success probabilities
``p = clip(0.45 + u_cluster + v_task + drift)``, the candidate's
``p + effect + w_cluster`` (cluster-level heterogeneity of the effect),
binomial successes over the repeats; optional unequal cluster sizes, strata
with different base rates, missing pairs (MCAR) and time drift. This is the
data model of the original simulation (``tests/support/wp2_07_legacy.py``).

Two changes from the original (D19):
- the analysis is the formal one: ``engine.analyse`` and ``engine.verdict_of``
  for one metric, ``engine.decide`` for the D 9.2 procedure; nothing here
  recomputes an interval or a decision;
- the data and the analysis use separate generators: run ``r`` of seed ``s``
  draws its data from ``sha256("data", s, r, metric)`` and its analysis seed
  from ``sha256("analysis", a, r)``, so changing the method or the analysis
  seed never changes the data.

The planned power is an analytic normal approximation (an independent
calculation). Nothing here decides pass or fail: the criteria are fixed in the
tests and in the pre-registration (``docs/design/phase-2/WP2-07_PREREGISTRATION.md``).
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
import time
from dataclasses import dataclass
from statistics import NormalDist

from agent_core.stats import engine as se

NI_SUCCESS = 0.03  # D 9.4 candidates (PROVISIONAL)
NI_QUALITY = 3.0
NI_LOG_RATIO = math.log(1.10)
IMPROVE_LOG_RATIO = -math.log(0.85)


def derive(*parts: object) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in ("wp2-07", *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


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
    tasks_range: tuple[int, int] | None = None  # unequal cluster sizes
    strata: int = 1  # strata with sizes 1:2:...:H and base rates shifted
    stratum_shift: float = 0.10
    weighting: se.Weighting = se.Weighting.TASK_COUNT


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
    analysis_seed: int = 0
    method: str = ""
    degenerate: int = 0
    variance_ratio: float | None = None  # mean SE^2 / variance of the estimates
    mean_estimate: float = 0.0
    data_digest: str | None = None

    @property
    def adopt_rate(self) -> float:
        return self.better_rate

    def monte_carlo_error(self, rate: float) -> float:
        return math.sqrt(max(rate * (1.0 - rate), 1e-12) / self.runs)


def _clip(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def generate(s: Scenario, *, seed: int, run: int) -> list[se.TaskPair]:
    rng = random.Random(derive("data", seed, run, "success"))
    tasks: list[se.TaskPair] = []
    for c in range(s.clusters):
        u = rng.gauss(0.0, s.sigma_cluster)
        w = rng.gauss(0.0, s.sigma_effect)
        size = s.tasks_per_cluster if s.tasks_range is None else rng.randint(*s.tasks_range)
        for t in range(size):
            stratum = (
                0 if s.strata == 1 else rng.choices(range(s.strata), range(1, s.strata + 1))[0]
            )
            if s.missing and rng.random() < s.missing:
                continue
            block = (c * s.tasks_per_cluster + t) % s.blocks
            shift = s.drift_per_block * block + s.stratum_shift * (stratum - (s.strata - 1) / 2)
            p = _clip(0.45 + u + rng.gauss(0.0, s.sigma_task) + shift, 0.10, 0.80)
            q = _clip(p + s.effect + w, 0.0, 1.0)
            base = sum(rng.random() < p for _ in range(s.repeats)) / s.repeats
            cand = sum(rng.random() < q for _ in range(s.repeats)) / s.repeats
            tasks.append(se.TaskPair(f"t{c}_{t}", f"c{c:03d}", f"h{stratum}", base, cand))
    return tasks


def _mean_tasks(s: Scenario) -> float:
    size = s.tasks_per_cluster if s.tasks_range is None else sum(s.tasks_range) / 2
    return size * (1.0 - s.missing)


def success_se(s: Scenario, *, draws: int = 20_000, seed: int = 99) -> float:
    """Analytic SE of the cluster mean difference from the variance components
    (binomial variance averaged over p, effect heterogeneity)."""
    rng = random.Random(seed)
    binomial = 0.0
    for _ in range(draws):
        p = _clip(0.45 + rng.gauss(0.0, s.sigma_cluster) + rng.gauss(0.0, s.sigma_task), 0.10, 0.80)
        q = _clip(p + s.effect, 0.0, 1.0)
        binomial += p * (1 - p) + q * (1 - q)
    binomial /= draws
    per_cluster = s.sigma_effect**2 + binomial / (s.repeats * _mean_tasks(s))
    return math.sqrt(per_cluster / s.clusters)


def _critical(clusters: int, alpha_one_sided: float) -> float:
    return se.t_quantile(1.0 - alpha_one_sided, clusters - 1)


def planned_power(
    s: Scenario, *, level: float = 0.95, draws: int = 20_000, seed: int = 99
) -> float:
    """P(L > 0) by the normal approximation with the registered critical value
    (Student t, C - 1 df)."""
    if s.effect <= 0:
        return 0.0
    crit = _critical(s.clusters, (1.0 - level) / 2.0)
    return NormalDist().cdf(s.effect / success_se(s, draws=draws, seed=seed) - crit)


def _digest(datasets: list[object]) -> str:
    return hashlib.sha256(repr(datasets).encode()).hexdigest()


def run(
    s: Scenario,
    *,
    runs: int,
    seed: int,
    analysis_seed: int | None = None,
    iterations: int = 500,
    delta_imp: float = 0.05,
    delta_ni: float = NI_SUCCESS,
    level: float = 0.95,
    record_data: bool = False,
) -> SimReport:
    a_seed = seed if analysis_seed is None else analysis_seed
    started = time.perf_counter()
    better = confirmed = covered = degenerate = 0
    widths: list[float] = []
    naive: list[float] = []
    estimates: list[float] = []
    variances: list[float] = []
    methods: set[str] = set()
    seen: list[object] = []
    for r in range(runs):
        tasks = generate(s, seed=seed, run=r)
        if record_data:
            seen.append(tasks)
        plan = se.AnalysisPlan(
            seed=derive("analysis", a_seed, r),
            iterations=iterations,
            level=level,
            weighting=s.weighting,
            sensitivity=False,
        )
        out = se.analyse(tasks, plan)
        verdict = se.verdict_of(out, delta_ni=delta_ni, delta_imp=delta_imp)
        methods.add(out.method)
        degenerate += out.degenerate
        better += verdict.direction == "BETTER"
        confirmed += verdict.practical == "PRACTICAL_CONFIRMED"
        covered += out.low <= s.effect <= out.high
        widths.append(out.high - out.low)
        naive.append(se.naive_task_interval(tasks, level=level))
        estimates.append(out.estimate)
        if out.standard_error is not None:
            variances.append(out.standard_error**2)
    spread = statistics.pvariance(estimates) if runs > 1 else 0.0
    ratio = statistics.fmean(variances) / spread if variances and spread > 0 else None
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
        analysis_seed=a_seed,
        method="+".join(sorted(methods)),
        degenerate=degenerate,
        variance_ratio=ratio,
        mean_estimate=statistics.fmean(estimates),
        data_digest=_digest(seen) if record_data else None,
    )


# ---------------------------------------------------------------- the D 9.2 procedure


@dataclass(frozen=True)
class ProcedureScenario:
    """Four metrics per task: success (primary and maintained), quality (points),
    cost and time (negated log ratio, positive = better). Effects are on those
    scales; metrics are generated independently given the structure."""

    name: str
    rule: se.Rule
    success_effect: float
    quality_effect: float = 0.0
    cost_effect: float = 0.0
    time_effect: float = 0.0
    evidence_a: se.Evidence = se.Evidence.E2
    evidence_b: se.Evidence = se.Evidence.E2
    rule_b: tuple[str, ...] = ("cost", "time")
    clusters: int = 40
    tasks_per_cluster: int = 4
    repeats: int = 3
    sigma_quality: float = 5.0  # cluster and task components of the level
    sigma_quality_noise: float = 8.0
    sigma_quality_effect: float = 1.0
    sigma_log: float = 0.20
    sigma_log_noise: float = 0.30
    sigma_log_effect: float = 0.03

    @property
    def structure(self) -> Scenario:
        return Scenario(
            name=self.name,
            effect=self.success_effect,
            clusters=self.clusters,
            tasks_per_cluster=self.tasks_per_cluster,
            repeats=self.repeats,
        )

    def registration(self) -> se.Registration:
        return se.Registration(
            rule=self.rule,
            maintained=(
                se.Maintained("success", NI_SUCCESS),
                se.Maintained("quality", NI_QUALITY),
                se.Maintained("cost", NI_LOG_RATIO),
                se.Maintained("time", NI_LOG_RATIO),
            ),
            primary="success",
            delta_imp_primary=0.05,
            evidence_a=self.evidence_a,
            rule_b=self.rule_b,
            delta_imp_b=IMPROVE_LOG_RATIO,
            evidence_b=self.evidence_b,
        )


def _continuous(
    ps: ProcedureScenario,
    rng: random.Random,
    *,
    effect: float,
    level: float,
    noise: float,
    sigma: float,
    sigma_effect: float,
    log_scale: bool,
) -> list[se.TaskPair]:
    """Quality: the candidate is higher by the effect. Cost and time: values
    are logs and the candidate is lower by the effect (positive = better)."""
    sign = -1.0 if log_scale else 1.0
    tasks: list[se.TaskPair] = []
    for c in range(ps.clusters):
        u = rng.gauss(0.0, sigma)
        w = rng.gauss(0.0, sigma_effect)
        for t in range(ps.tasks_per_cluster):
            v = rng.gauss(0.0, sigma)
            base = [level + u + v + rng.gauss(0.0, noise) for _ in range(ps.repeats)]
            cand = [
                level + u + v + sign * (effect + w) + rng.gauss(0.0, noise)
                for _ in range(ps.repeats)
            ]
            task, cluster = f"t{c}_{t}", f"c{c:03d}"
            if log_scale:
                tasks.append(
                    se.log_ratio_pair(
                        task,
                        cluster,
                        "h0",
                        baseline=[math.exp(x) for x in base],
                        candidate=[math.exp(x) for x in cand],
                    )
                )
            else:
                tasks.append(
                    se.TaskPair(task, cluster, "h0", statistics.fmean(base), statistics.fmean(cand))
                )
    return tasks


def generate_metrics(ps: ProcedureScenario, *, seed: int, run: int) -> dict[str, list[se.TaskPair]]:
    def stream(metric: str) -> random.Random:
        return random.Random(derive("data", seed, run, metric))

    return {
        "success": generate(ps.structure, seed=seed, run=run),
        "quality": _continuous(
            ps,
            stream("quality"),
            effect=ps.quality_effect,
            level=70.0,
            noise=ps.sigma_quality_noise,
            sigma=ps.sigma_quality,
            sigma_effect=ps.sigma_quality_effect,
            log_scale=False,
        ),
        "cost": _continuous(
            ps,
            stream("cost"),
            effect=ps.cost_effect,
            level=0.0,
            noise=ps.sigma_log_noise,
            sigma=ps.sigma_log,
            sigma_effect=ps.sigma_log_effect,
            log_scale=True,
        ),
        "time": _continuous(
            ps,
            stream("time"),
            effect=ps.time_effect,
            level=0.0,
            noise=ps.sigma_log_noise,
            sigma=ps.sigma_log,
            sigma_effect=ps.sigma_log_effect,
            log_scale=True,
        ),
    }


def _continuous_se(ps: ProcedureScenario, *, noise: float, sigma_effect: float) -> float:
    per_cluster = sigma_effect**2 + 2 * noise**2 / (ps.repeats * ps.tasks_per_cluster)
    return math.sqrt(per_cluster / ps.clusters)


def planned_procedure_power(ps: ProcedureScenario, *, alpha: float = 0.025) -> float:
    """Rule A only: P(evidence on success) x P(NI) of quality, cost and time,
    by the normal approximation (metrics independent by construction; success
    NI is implied by L > 0). Other rules: 0.0 (no power criterion)."""
    if ps.rule is not se.Rule.A:
        return 0.0
    crit = _critical(ps.clusters, alpha)
    phi = NormalDist().cdf
    s_se = success_se(ps.structure)
    if ps.evidence_a is se.Evidence.E1:
        p_a = phi(ps.success_effect / s_se - crit)
    elif ps.evidence_a is se.Evidence.E2:
        p_a = phi((ps.success_effect - max(0.05, crit * s_se)) / s_se)
    else:
        p_a = phi((ps.success_effect - 0.05) / s_se - crit)
    q_se = _continuous_se(ps, noise=ps.sigma_quality_noise, sigma_effect=ps.sigma_quality_effect)
    l_se = _continuous_se(ps, noise=ps.sigma_log_noise, sigma_effect=ps.sigma_log_effect)
    p_ni = (
        phi((ps.quality_effect + NI_QUALITY) / q_se - crit)
        * phi((ps.cost_effect + NI_LOG_RATIO) / l_se - crit)
        * phi((ps.time_effect + NI_LOG_RATIO) / l_se - crit)
    )
    return p_a * p_ni


@dataclass(frozen=True)
class ProcedureReport:
    name: str
    runs: int
    seed: int
    analysis_seed: int
    adopt_rate: float
    stage1_fail_rate: float
    planned_power: float
    seconds: float

    def monte_carlo_error(self, rate: float) -> float:
        return math.sqrt(max(rate * (1.0 - rate), 1e-12) / self.runs)


def run_procedure(
    ps: ProcedureScenario, *, runs: int, seed: int, analysis_seed: int | None = None
) -> ProcedureReport:
    a_seed = seed if analysis_seed is None else analysis_seed
    started = time.perf_counter()
    registration = ps.registration()
    adopted = stage1_failed = 0
    for r in range(runs):
        data = generate_metrics(ps, seed=seed, run=r)
        plan = se.AnalysisPlan(seed=derive("analysis", a_seed, r), sensitivity=False)
        decision = se.decide(registration, dict(data), plan, safety_violations=0)
        adopted += decision.adopted
        stage1_failed += not all(met for _, _, met in decision.stage1)
    return ProcedureReport(
        name=ps.name,
        runs=runs,
        seed=seed,
        analysis_seed=a_seed,
        adopt_rate=adopted / runs,
        stage1_fail_rate=stage1_failed / runs,
        planned_power=planned_procedure_power(ps),
        seconds=time.perf_counter() - started,
    )
