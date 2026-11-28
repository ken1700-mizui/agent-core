"""Evaluation records (WP2-01; D 3, D.V19; contract 2, 2.4, 2.5, 7.8).

Kept apart from the runtime types: the agent under evaluation never writes or
classifies these records (D.V4, D.V8); the evaluation side does.

- Specs (``TaskSpec``, ``ConfigSpec``, ``ExperimentSpec``) are identified by
  hashes of their canonical JSON. An ``ExperimentSpec`` is frozen before it runs.
- ``TrialRecord`` keeps the task outcome (D 7.1, unchanged) and safety apart.
  ``objective_success`` and ``safe_completion`` are derived, never stored.
- Cost: a trial's cost is split once by settlement (settled, or held per
  reservation). Validity and retry are attributes of the trial. The four
  reporting buckets of 2.4 are views of that split (``cost_summary``): only the
  settled and held totals add up; one XTU is never counted twice.
- No floats: rates are parts per million (integers).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel, ImmutableRecord
from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import AttemptId, ExperimentId, HomeId, ReservationId, RunId, TrialId
from agent_core.types.records import StepKind
from agent_core.types.serialization import dump_json
from agent_core.types.values import (
    Count,
    Digest,
    Identifier,
    SemVer,
    Seq,
    TimestampMs,
    require_sorted_unique,
    require_unique,
)

PPM = 1_000_000
Ppm = Annotated[int, Field(strict=True, ge=0, le=PPM)]
PositiveCount = Annotated[int, Field(strict=True, ge=1, le=(1 << 63) - 1)]
# Invariant references such as "C1.S1" or "D.V19".
InvariantRef = Annotated[str, Field(strict=True, pattern=r"^[A-E][0-9]?\.[A-Z][0-9]{1,3}$")]

_DOMAIN = b"agent-core/evaluation/v1\x00"


def _hash(kind: str, record: FrozenModel) -> str:
    data = _DOMAIN + kind.encode() + b"\x00" + dump_json(record)
    return "sha256:" + hashlib.sha256(data).hexdigest()


class CostAccountingError(AgentCoreError):
    """Cost records do not add up (double counting, unknown hold, over-settlement)."""

    code = "COST_ACCOUNTING"


# ---------------------------------------------------------------- enums


class TaskSource(StrEnum):
    """Where a task comes from (contract 8). Only DEV tasks live in this repository."""

    DEV = "DEV"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


class TaskOutcome(StrEnum):
    """D 7.1. Unchanged: no new values."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_ENV = "INVALID_ENV"


class FailureAttribution(StrEnum):
    """D 7.2."""

    ENV = "ENV"
    AGENT = "AGENT"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    PROVIDER_OUTAGE_BLOCK = "PROVIDER_OUTAGE_BLOCK"


class InvalidReason(StrEnum):
    """Fixed codes of the evaluation side (D.V18). A spec lists the allowed ones."""

    GRADER_CRASH = "GRADER_CRASH"
    GRADING_IMPOSSIBLE = "GRADING_IMPOSSIBLE"
    PROVIDER_OUTAGE_BLOCK = "PROVIDER_OUTAGE_BLOCK"
    RUNNER_FAILURE = "RUNNER_FAILURE"
    SANDBOX_HOST_FAILURE = "SANDBOX_HOST_FAILURE"
    SELF_DIAGNOSIS_FAILED = "SELF_DIAGNOSIS_FAILED"


class GradingStatus(StrEnum):
    GRADED = "GRADED"
    NOT_GRADABLE = "NOT_GRADABLE"


class LimitKind(StrEnum):
    BUDGET = "BUDGET"
    CALLS = "CALLS"
    CONTEXT = "CONTEXT"
    EVENTS = "EVENTS"
    STEPS = "STEPS"
    TIME = "TIME"


class PrimaryMetric(StrEnum):
    """Pre-registered main success measure (contract 2.4, rule 3)."""

    OBJECTIVE_SUCCESS_WITHOUT_VIOLATION = "OBJECTIVE_SUCCESS_WITHOUT_VIOLATION"
    SAFE_COMPLETION = "SAFE_COMPLETION"


class Validity(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


class Verdict(StrEnum):
    ADOPT = "ADOPT"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"


# ---------------------------------------------------------------- specs


class TaskSpec(FrozenModel):
    """A task of the catalog: references only (no task body, no hidden test)."""

    task_key: Identifier
    version: SemVer
    source: TaskSource
    goal_ref: Digest
    done_criteria: Annotated[tuple[Digest, ...], Field(min_length=1)]
    grader_ref: Digest
    repo_cluster: Identifier
    stratum: Identifier

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        require_sorted_unique(self.done_criteria, "done_criteria")
        return self

    def task_ref(self) -> Digest:
        return _hash("task", self)


class ConfigSpec(FrozenModel):
    """One configuration under comparison (D 4.1)."""

    config_key: Identifier
    model_profile_ref: Digest
    context_strategy_ref: Digest
    planning_strategy_ref: Digest
    verification_strategy_ref: Digest
    prompt_template_ref: Digest
    tool_manifest_ref: Digest
    sandbox_profile_hash: Digest | None

    def config_hash(self) -> Digest:
        return _hash("config", self)


class ClassificationRules(FrozenModel):
    """Pre-registered classification rules (contract 2.4). Part of ``spec_hash``."""

    version: SemVer
    allowed_invalid_reasons: Annotated[tuple[InvalidReason, ...], Field(min_length=1)]
    primary_metric: PrimaryMetric

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        require_sorted_unique(self.allowed_invalid_reasons, "allowed_invalid_reasons")
        return self

    def rules_hash(self) -> Digest:
        return _hash("rules", self)


class ExperimentBudget(FrozenModel):
    """Caps of one experiment across all its homes (contract 7.2, 7.8)."""

    experiment_cap_xtu: Count
    trial_cap_xtu: Count
    trial_event_cap: PositiveCount
    events_per_home: PositiveCount
    max_homes: PositiveCount
    max_retries: Count

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.trial_cap_xtu > self.experiment_cap_xtu:
            raise ValueError("trial_cap_xtu is above experiment_cap_xtu")
        if self.trial_event_cap > self.events_per_home:
            raise ValueError("trial_event_cap is above events_per_home")
        return self


class ExperimentSpec(FrozenModel):
    """Frozen (``spec_hash``) before the experiment runs; never changed afterwards."""

    experiment_id: ExperimentId
    task_refs: Annotated[tuple[Digest, ...], Field(min_length=1)]
    config_hashes: Annotated[tuple[Digest, ...], Field(min_length=1)]
    repeats: PositiveCount
    seed: Count
    eval_set_version: SemVer
    grader_version: SemVer
    analysis_plan_ref: Digest
    classification: ClassificationRules
    budget: ExperimentBudget

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_sorted_unique(self.task_refs, "task_refs")
        require_sorted_unique(self.config_hashes, "config_hashes")
        if self.required_homes > self.budget.max_homes:
            raise ValueError("max_homes is below the homes the planned trials need (7.8)")
        return self

    @property
    def planned_trials(self) -> int:
        return len(self.task_refs) * len(self.config_hashes) * self.repeats + (
            self.budget.max_retries
        )

    @property
    def required_homes(self) -> int:
        """From events, not XTU: zero-cost trials still use events (7.8)."""
        events = self.planned_trials * self.budget.trial_event_cap
        return -(-events // self.budget.events_per_home)

    def spec_hash(self) -> Digest:
        return _hash("experiment", self)


# ---------------------------------------------------------------- trial records


class SafetyViolation(FrozenModel):
    code: Identifier
    invariant_ref: InvariantRef
    evidence_ref: Digest


class BlockedAttempt(FrozenModel):
    """A normal authorization refusal: not a violation."""

    deny_reason: Identifier
    attempt_ref: AttemptId


class SafetyRecord(FrozenModel):
    violations: tuple[SafetyViolation, ...]
    blocked_attempts: tuple[BlockedAttempt, ...]


class JournalRef(FrozenModel):
    """The agent journal position of a trial, and its home (7.8)."""

    home_id: HomeId
    tenant_seq: Seq
    head_hash: Digest


class TokenUsage(FrozenModel):
    input: Count
    output: Count

    @property
    def total(self) -> int:
        return self.input + self.output


class Unresolved(FrozenModel):
    """OUTCOME_UNKNOWN attempts left at the end of the trial."""

    attempt_refs: tuple[AttemptId, ...]

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        require_sorted_unique(self.attempt_refs, "attempt_refs")
        return self

    @property
    def count(self) -> int:
        return len(self.attempt_refs)


class HeldCost(FrozenModel):
    """A hold kept by the ledger (unknown outcome): never counted as settled zero."""

    reservation_ref: ReservationId
    xtu: PositiveCount


class TrialCost(FrozenModel):
    """The trial's cost split once by settlement (contract 2.5). A copy of the
    ledger (the source of truth), checked against it when reconciled."""

    settled_xtu: Count
    held: tuple[HeldCost, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique([h.reservation_ref for h in self.held], "held reservations")
        return self

    @property
    def held_xtu(self) -> int:
        return sum(h.xtu for h in self.held)

    @property
    def upper_bound(self) -> int:
        return self.settled_xtu + self.held_xtu


_INVALID_ATTRIBUTIONS = frozenset(
    {FailureAttribution.ENV, FailureAttribution.PROVIDER_OUTAGE_BLOCK}
)


class TrialRecord(ImmutableRecord):
    """D.V19 with the approved extensions (O-3). Classified by the evaluation side."""

    trial_id: TrialId
    experiment_id: ExperimentId
    config_hash: Digest
    task_ref: Digest
    repeat_index: Count
    order_index: Count
    seed: Count
    run_id: RunId | None
    journal_ref: JournalRef | None
    started_at_ms: TimestampMs
    ended_at_ms: TimestampMs
    wall_ms: Count
    task_outcome: TaskOutcome
    safety: SafetyRecord
    failure_attribution: FailureAttribution | None
    invalid_reason: InvalidReason | None
    tokens: TokenUsage
    cost: TrialCost
    human_minutes: Count
    unresolved: Unresolved
    grading_status: GradingStatus
    limits_hit: tuple[LimitKind, ...]
    run_completed: bool
    retry_of: TrialId | None
    classification_rules_hash: Digest

    @model_validator(mode="after")
    def _rules(self) -> Self:
        require_sorted_unique(self.limits_hit, "limits_hit")
        invalid = self.task_outcome is TaskOutcome.INVALID_ENV
        if invalid != (self.invalid_reason is not None):
            raise ValueError("invalid_reason is given exactly when task_outcome is INVALID_ENV")
        if invalid and self.failure_attribution not in _INVALID_ATTRIBUTIONS:
            raise ValueError("an agent failure is never INVALID_ENV (D 7.2)")
        if self.task_outcome is TaskOutcome.TIMEOUT and LimitKind.TIME not in self.limits_hit:
            raise ValueError("TIMEOUT records the TIME limit")
        if (
            self.task_outcome is TaskOutcome.BUDGET_EXCEEDED
            and LimitKind.BUDGET not in self.limits_hit
        ):
            raise ValueError("BUDGET_EXCEEDED records the BUDGET limit")
        impossible = self.invalid_reason is InvalidReason.GRADING_IMPOSSIBLE
        if self.grading_status is GradingStatus.NOT_GRADABLE:
            agent_fault = (
                self.task_outcome is TaskOutcome.FAIL
                and self.failure_attribution is FailureAttribution.AGENT
            )
            if not (impossible or agent_fault):
                raise ValueError("NOT_GRADABLE is GRADING_IMPOSSIBLE or an agent FAIL")
        elif impossible:
            raise ValueError("GRADING_IMPOSSIBLE means the trial was not gradable")
        if (self.run_id is None) != (self.journal_ref is None):
            raise ValueError("run_id and journal_ref are given together")
        if self.ended_at_ms < self.started_at_ms:
            raise ValueError("ended_at_ms is before started_at_ms")
        return self

    @property
    def validity(self) -> Validity:
        invalid = self.task_outcome is TaskOutcome.INVALID_ENV
        return Validity.INVALID if invalid else Validity.VALID

    @property
    def objective_success(self) -> bool:
        return self.task_outcome is TaskOutcome.SUCCESS

    @property
    def safe_completion(self) -> bool:
        return (
            self.objective_success
            and not self.safety.violations
            and self.unresolved.count == 0
            and self.run_completed
        )


class CostReconciliation(ImmutableRecord):
    """A hold settled later (contract 2.5). Appended; the trial is never rewritten."""

    trial_id: TrialId
    reservation_ref: ReservationId
    settled_xtu: Count
    released_xtu: Count
    evidence_ref: Digest


class ScoreRecord(ImmutableRecord):
    """The grader's result for one trial (D 7)."""

    trial_id: TrialId
    experiment_id: ExperimentId
    grader_version: SemVer
    grading_status: GradingStatus
    task_outcome: TaskOutcome
    points: Count
    max_points: PositiveCount
    evidence_ref: Digest

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.points > self.max_points:
            raise ValueError("points above max_points")
        if self.task_outcome is TaskOutcome.SUCCESS and self.points != self.max_points:
            raise ValueError("SUCCESS has every point")
        if self.grading_status is GradingStatus.NOT_GRADABLE and self.points != 0:
            raise ValueError("a trial that was not graded has no points")
        return self


class AnalysisResult(ImmutableRecord):
    """One pre-registered analysis (D 8). Values in parts per million."""

    experiment_id: ExperimentId
    spec_hash: Digest
    metric: PrimaryMetric
    method: Identifier
    estimate_ppm: Ppm
    ci_low_ppm: Ppm
    ci_high_ppm: Ppm
    n_tasks: Count
    n_trials_valid: Count
    n_trials_invalid: Count
    violations: Count
    results_head: Digest

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if not self.ci_low_ppm <= self.estimate_ppm <= self.ci_high_ppm:
            raise ValueError("the estimate lies within its interval")
        return self


class Decision(ImmutableRecord):
    """The owner's decision on an analysis. ADOPT needs the Safety Gate."""

    experiment_id: ExperimentId
    analysis_ref: Digest
    verdict: Verdict
    safety_gate_passed: bool
    reasons: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _safety(self) -> Self:
        if self.verdict is Verdict.ADOPT and not self.safety_gate_passed:
            raise ValueError("ADOPT needs the safety gate (no violation)")
        return self


# ---------------------------------------------------------------- summaries


@dataclass(frozen=True)
class CostSummary:
    """Additive: ``settled_total`` and ``held_total`` (each XTU in one of them).
    Views (never added to the totals): the other fields."""

    settled_total: int
    held_total: int
    released_total: int
    settled_valid: int
    invalid_trial_xtu: int
    retry_xtu: int

    @property
    def upper_bound(self) -> int:
        return self.settled_total + self.held_total

    @property
    def held_unknown_xtu(self) -> int:
        return self.held_total


def cost_summary(
    trials: Iterable[TrialRecord], reconciliations: Iterable[CostReconciliation]
) -> CostSummary:
    settled: dict[TrialId, int] = {}
    holds: dict[tuple[TrialId, ReservationId], int] = {}
    by_trial: dict[TrialId, TrialRecord] = {}
    for t in trials:
        if t.trial_id in by_trial:
            raise CostAccountingError("a trial is counted once")
        by_trial[t.trial_id] = t
        settled[t.trial_id] = t.cost.settled_xtu
        for h in t.cost.held:
            holds[(t.trial_id, h.reservation_ref)] = h.xtu
    released = 0
    done: set[tuple[TrialId, ReservationId]] = set()
    for r in reconciliations:
        key = (r.trial_id, r.reservation_ref)
        if key in done:
            raise CostAccountingError("a hold is reconciled once")
        if key not in holds:
            raise CostAccountingError("no such hold to reconcile")
        if r.settled_xtu + r.released_xtu != holds[key]:
            raise CostAccountingError("a reconciliation accounts for the whole hold")
        done.add(key)
        del holds[key]
        settled[r.trial_id] += r.settled_xtu
        released += r.released_xtu
    held_by_trial: dict[TrialId, int] = {}
    for (trial_id, _), xtu in holds.items():
        held_by_trial[trial_id] = held_by_trial.get(trial_id, 0) + xtu

    def total(keep: Iterable[TrialId], *, with_held: bool) -> int:
        ids = list(keep)
        held_part = sum(held_by_trial.get(i, 0) for i in ids) if with_held else 0
        return sum(settled[i] for i in ids) + held_part

    valid = [i for i, t in by_trial.items() if t.validity is Validity.VALID]
    invalid = [i for i, t in by_trial.items() if t.validity is Validity.INVALID]
    retries = [i for i, t in by_trial.items() if t.retry_of is not None]
    return CostSummary(
        settled_total=sum(settled.values()),
        held_total=sum(holds.values()),
        released_total=released,
        settled_valid=total(valid, with_held=False),
        invalid_trial_xtu=total(invalid, with_held=True),
        retry_xtu=total(retries, with_held=True),
    )


@dataclass(frozen=True)
class SafetySummary:
    """Over every trial, INVALID_ENV included (contract 2.4, rule 2)."""

    trials: int
    violations: int
    trials_with_violations: int
    blocked_attempts: int


def safety_summary(trials: Iterable[TrialRecord]) -> SafetySummary:
    records = list(trials)
    return SafetySummary(
        trials=len(records),
        violations=sum(len(t.safety.violations) for t in records),
        trials_with_violations=sum(1 for t in records if t.safety.violations),
        blocked_attempts=sum(len(t.safety.blocked_attempts) for t in records),
    )


@dataclass(frozen=True)
class SuccessCounts:
    """Counts for the success rates (D 8.2 weights them per task; WP2-07)."""

    denominator: int
    primary_successes: int
    safe_completions: int
    unresolved_trials: int
    invalid: int


def success_counts(trials: Iterable[TrialRecord]) -> SuccessCounts:
    records = list(trials)
    valid = [t for t in records if t.validity is Validity.VALID]
    return SuccessCounts(
        denominator=len(valid),
        primary_successes=sum(1 for t in valid if t.objective_success and not t.safety.violations),
        safe_completions=sum(1 for t in valid if t.safe_completion),
        unresolved_trials=sum(1 for t in valid if t.unresolved.count > 0),
        invalid=len(records) - len(valid),
    )


# ---------------------------------------------------------------- port requests


class GradingRequest(FrozenModel):
    """What the grader gets: references only; hidden tests stay on its side."""

    trial_id: TrialId
    experiment_id: ExperimentId
    task_ref: Digest
    artifact_hash: Digest
    grader_version: SemVer


class PlanRequest(FrozenModel):
    task_ref: Digest
    context_hash: Digest


class PlanProposal(FrozenModel):
    """A plan is a proposal: the runtime still authorizes every step (WP2-06)."""

    plan_ref: Digest
    step_kinds: Annotated[tuple[StepKind, ...], Field(min_length=1)]
