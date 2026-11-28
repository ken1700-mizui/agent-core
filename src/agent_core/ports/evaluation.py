"""Evaluation ports (WP2-01; D 3, D 7; contract 1, 5).

Used by the evaluation side only (controller, grader, analysis). The agent
path never imports this module (import contract): the agent under evaluation
cannot write or classify evaluation records (D.V4, D.V8).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.evaluation import (
    AnalysisResult,
    CostReconciliation,
    CostSummary,
    Decision,
    ExperimentSpec,
    GradingRequest,
    PlanProposal,
    PlanRequest,
    ScoreRecord,
    TaskSpec,
    TrialRecord,
)
from agent_core.types.ids import ExperimentId


@runtime_checkable
class EvaluationStorePort(Protocol):
    """Experiment Registry and Results Store (append only)."""

    def register_task(self, task: TaskSpec) -> str: ...

    def freeze(self, spec: ExperimentSpec) -> str: ...

    def spec(self, experiment_id: ExperimentId) -> ExperimentSpec: ...

    def append_trial(self, record: TrialRecord) -> None: ...

    def append_reconciliation(self, record: CostReconciliation) -> None: ...

    def append_score(self, record: ScoreRecord) -> None: ...

    def append_analysis(self, record: AnalysisResult) -> None: ...

    def append_decision(self, record: Decision) -> None: ...

    def trials(self, experiment_id: ExperimentId) -> tuple[TrialRecord, ...]: ...

    def cost_summary(self, experiment_id: ExperimentId) -> CostSummary: ...


@runtime_checkable
class GraderPort(Protocol):
    """Grades one trial in its own grading sandbox, after the agent's run (WP2-10)."""

    def grade(self, request: GradingRequest) -> ScoreRecord: ...


@runtime_checkable
class PlannerPort(Protocol):
    """Proposes a plan; never authorizes anything (WP2-06)."""

    def plan(self, request: PlanRequest) -> PlanProposal: ...
