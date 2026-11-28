"""Journal v4 provenance records (WP2-01, A.I11; contract 3.2.4).

A v4 runtime payload may carry a list of provenance records next to its
transitions. Each record names the entity of one transition in the same event
and the stage of that transition. The records never change the transitions or
the state's meaning; they say which configuration, task, evaluation version,
model profile, context, prompt, tool and sandbox produced a step or attempt,
and, at a live result, how long it took (monotonic clock).

Values are recorded only at the transition where they become final. Stages
whose result carries no measured value (abort before dispatch, unknown outcome,
reconciliation) have records with no values: a record can never hold a usage,
result or time that does not exist. Usage and cost stay in the ledger.

Only identifiers and digests: no prompt, file or model output body, no secret.

``*Fields`` classes are what a caller supplies; the runtime binds them to the
entity it creates or changes (``bind``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import AttemptId, ExperimentId, RunId, StepId, TrialId
from agent_core.types.values import Count, Digest, Identifier, SemVer, require_sorted_unique


class CallKind(StrEnum):
    """What an Attempt calls: a model (``MODEL_CALL`` steps) or a tool (others)."""

    MODEL = "MODEL"
    TOOL = "TOOL"


class OutcomeKind(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED_DEFINITE = "FAILED_DEFINITE"
    ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    # Settled later from external records (ReconciliationEvidence).
    RECONCILED_COMPLETED = "RECONCILED_COMPLETED"
    RECONCILED_FAILED = "RECONCILED_FAILED"


# Results reported live by the executor: the time was measured.
LIVE_RESULTS = frozenset({OutcomeKind.COMPLETED, OutcomeKind.FAILED_DEFINITE})
# Results that carry no value at all: nothing ran, or nothing is known yet.
EMPTY_RESULTS = frozenset({OutcomeKind.ABORTED_BEFORE_DISPATCH, OutcomeKind.OUTCOME_UNKNOWN})
RECONCILED_RESULTS = frozenset({OutcomeKind.RECONCILED_COMPLETED, OutcomeKind.RECONCILED_FAILED})


class ToolRef(FrozenModel):
    """``tool_id@version@manifest_hash`` of the tool that ran (C1 2.3)."""

    tool_id: Identifier  # the registry's ToolId
    version: SemVer
    manifest_hash: Digest


# ---------------------------------------------------------------- fields (caller input)


class RunProvenanceFields(FrozenModel):
    """Marks an evaluation Run: only Runs created with this record are evaluation Runs."""

    experiment_id: ExperimentId
    trial_id: TrialId


class StepProvenanceFields(FrozenModel):
    """Values fixed when the Step is created (contract 3.2.1, stage 1)."""

    config_hash: Digest
    task_ref: Digest
    eval_set_version: SemVer
    grader_version: SemVer
    experiment_id: ExperimentId | None
    trial_id: TrialId | None
    plan_ref: Digest | None
    strategy_refs: tuple[Digest, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.experiment_id is None) != (self.trial_id is None):
            raise ValueError("experiment_id and trial_id are given together")
        require_sorted_unique(self.strategy_refs, "strategy_refs")
        return self


class AttemptProvenanceFields(FrozenModel):
    """Values fixed with the action hash (``CONTENT_FIXED``, stage 2)."""

    call: CallKind
    model_profile_ref: Digest | None
    context_hash: Digest | None
    prompt_template_ref: Digest | None
    tool_ref: ToolRef | None
    sandbox_profile_hash: Digest | None
    image_digest: Digest | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        model = (self.model_profile_ref, self.context_hash, self.prompt_template_ref)
        tool = (self.tool_ref, self.sandbox_profile_hash, self.image_digest)
        if self.call is CallKind.MODEL:
            if any(v is None for v in model):
                raise ValueError("a model call records its profile, context and prompt")
            if any(v is not None for v in tool):
                raise ValueError("a model call records no tool or sandbox")
        else:
            if self.tool_ref is None:
                raise ValueError("a tool call records its tool")
            if any(v is not None for v in model):
                raise ValueError("a tool call records no model profile, context or prompt")
            if (self.sandbox_profile_hash is None) != (self.image_digest is None):
                raise ValueError("a sandbox profile and its image digest are recorded together")
        return self


class OutcomeProvenanceFields(FrozenModel):
    """Values of a result transition (stage 3). See the module docstring."""

    result: OutcomeKind
    wall_ms: Count | None
    exit_ref: Digest | None
    verification_ref: Digest | None

    @model_validator(mode="after")
    def _only_existing_values(self) -> Self:
        if self.result in LIVE_RESULTS:
            if self.wall_ms is None:
                raise ValueError("a live result records its measured wall_ms")
        elif self.result in EMPTY_RESULTS:
            if (self.wall_ms, self.exit_ref, self.verification_ref) != (None, None, None):
                raise ValueError("no time, exit or verification exists for this result")
        elif self.wall_ms is not None or self.verification_ref is not None:
            raise ValueError("a reconciled result has no measured time or verification")
        return self

    @classmethod
    def marker(cls, result: OutcomeKind) -> OutcomeProvenanceFields:
        """The value-free record of an abort, unknown or reconciled result."""
        if result in LIVE_RESULTS:
            raise ValueError("a live result needs its measured values")
        return cls(result=result, wall_ms=None, exit_ref=None, verification_ref=None)


# ---------------------------------------------------------------- records (in the payload)


class RunProvenance(RunProvenanceFields):
    stage: Literal["run_created"]
    run_id: RunId

    @classmethod
    def bind(cls, fields: RunProvenanceFields, run_id: RunId) -> RunProvenance:
        return cls.model_validate({"stage": "run_created", "run_id": run_id, **dict(fields)})


class StepProvenance(StepProvenanceFields):
    stage: Literal["step_created"]
    step_id: StepId

    @classmethod
    def bind(cls, fields: StepProvenanceFields, step_id: StepId) -> StepProvenance:
        return cls.model_validate({"stage": "step_created", "step_id": step_id, **dict(fields)})


class AttemptProvenance(AttemptProvenanceFields):
    stage: Literal["content_fixed"]
    attempt_id: AttemptId

    @classmethod
    def bind(cls, fields: AttemptProvenanceFields, attempt_id: AttemptId) -> AttemptProvenance:
        return cls.model_validate(
            {"stage": "content_fixed", "attempt_id": attempt_id, **dict(fields)}
        )


class OutcomeProvenance(OutcomeProvenanceFields):
    stage: Literal["outcome"]
    attempt_id: AttemptId

    @classmethod
    def bind(cls, fields: OutcomeProvenanceFields, attempt_id: AttemptId) -> OutcomeProvenance:
        return cls.model_validate({"stage": "outcome", "attempt_id": attempt_id, **dict(fields)})


Provenance = Annotated[
    RunProvenance | StepProvenance | AttemptProvenance | OutcomeProvenance,
    Field(discriminator="stage"),
]

# Kept for readers: the names of the stages, in transition order.
PROVENANCE_STAGES: tuple[Identifier, ...] = (
    "run_created",
    "step_created",
    "content_fixed",
    "outcome",
)
