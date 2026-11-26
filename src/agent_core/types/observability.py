"""Structured log and audit record types (WP-11; E 7.7, C2 4.5, C2.X4, C2.X25).

Fixed metadata only: IDs, engine, kind, state, a fixed reason code, times and
allowed labels. There is no free-text, message or dictionary field, so a
prompt, response, tool input or output, file content, credential or exception
message cannot be put into a record. ``state`` is checked against the entity's
own state enum; ``reason`` is a closed enum.

Observation logs (``LogRecord``) and audit records (``AuditRecord``) are
separate types written by separate writers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import StringConstraints, field_validator, model_validator

from agent_core.types.base import ImmutableRecord
from agent_core.types.ids import AttemptId, EventId, RunId, StepId, TaskId
from agent_core.types.records import AttemptState, RunStatus, StepStatus, TaskStatus
from agent_core.types.values import (
    Count,
    EngineVersion,
    PolicyVersion,
    TimestampMs,
    require_sorted_unique,
)

LOG_SCHEMA_VERSION: Literal[1] = 1
AUDIT_SCHEMA_VERSION: Literal[1] = 1


class LogKind(StrEnum):
    STATE_CHANGED = "STATE_CHANGED"
    OPERATION_FINISHED = "OPERATION_FINISHED"
    OPERATION_FAILED = "OPERATION_FAILED"


class LogEntity(StrEnum):
    TASK = "TASK"
    RUN = "RUN"
    STEP = "STEP"
    ATTEMPT = "ATTEMPT"


class LogLabel(StrEnum):
    """Data labels allowed in logs. Confidential and personal data never appear."""

    SYNTHETIC = "synthetic"
    PUBLIC = "public"
    INTERNAL = "internal"


class ReasonCode(StrEnum):
    """Fixed reason codes (the ``code`` of typed errors). Never a message."""

    OK = "OK"
    UNCLASSIFIED_ERROR = "UNCLASSIFIED_ERROR"
    RECORD_VALIDATION_FAILED = "RECORD_VALIDATION_FAILED"
    ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
    TRANSITION_PRECONDITION_FAILED = "TRANSITION_PRECONDITION_FAILED"
    STALE_STATE = "STALE_STATE"
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    EVIDENCE_REJECTED = "EVIDENCE_REJECTED"
    RUNTIME_REENTRY = "RUNTIME_REENTRY"
    RUNTIME_RELOAD_REQUIRED = "RUNTIME_RELOAD_REQUIRED"
    STATE_REBUILD_FAILED = "STATE_REBUILD_FAILED"
    STATE_DIVERGENCE = "STATE_DIVERGENCE"
    DUPLICATE_ID = "DUPLICATE_ID"
    JOURNAL_HEAD_MISMATCH = "JOURNAL_HEAD_MISMATCH"
    JOURNAL_CORRUPTION = "JOURNAL_CORRUPTION"
    JOURNAL_STORAGE_FAILURE = "JOURNAL_STORAGE_FAILURE"
    JOURNAL_WRITE_OUTCOME_UNKNOWN = "JOURNAL_WRITE_OUTCOME_UNKNOWN"
    MODEL_NOT_SENT = "MODEL_NOT_SENT"
    MODEL_FAILED_DEFINITE = "MODEL_FAILED_DEFINITE"
    MODEL_OUTCOME_UNKNOWN = "MODEL_OUTCOME_UNKNOWN"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MODEL_INVALID_REQUEST = "MODEL_INVALID_REQUEST"
    MODEL_UNSUPPORTED = "MODEL_UNSUPPORTED"
    FAKE_SCRIPT_MISS = "FAKE_SCRIPT_MISS"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    UNKNOWN_TOOL_VERSION = "UNKNOWN_TOOL_VERSION"
    TOOL_NOT_APPROVED = "TOOL_NOT_APPROVED"
    TOOL_KIND_NOT_ALLOWED = "TOOL_KIND_NOT_ALLOWED"
    TOOL_INPUT_INVALID = "TOOL_INPUT_INVALID"
    TOOL_OUTPUT_INVALID = "TOOL_OUTPUT_INVALID"
    REPLAY_UNAVAILABLE = "REPLAY_UNAVAILABLE"


_STATES: dict[LogEntity, type[StrEnum]] = {
    LogEntity.TASK: TaskStatus,
    LogEntity.RUN: RunStatus,
    LogEntity.STEP: StepStatus,
    LogEntity.ATTEMPT: AttemptState,
}
StateName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z][A-Z_]{1,40}$")]


class _Trace(ImmutableRecord):
    task_id: TaskId | None
    run_id: RunId | None
    step_id: StepId | None
    attempt_id: AttemptId | None
    event_id: EventId | None

    @model_validator(mode="after")
    def _chain(self) -> Self:
        if self.attempt_id is not None and self.step_id is None:
            raise ValueError("attempt_id requires step_id")
        if self.step_id is not None and self.run_id is None:
            raise ValueError("step_id requires run_id")
        if self.run_id is not None and self.task_id is None:
            raise ValueError("run_id requires task_id")
        return self


class LogRecord(_Trace):
    log_schema_version: Literal[1]
    kind: LogKind
    recorded_at_ms: TimestampMs
    # Measured with a monotonic clock; never negative.
    elapsed_ms: Count | None
    engine: EngineVersion
    entity: LogEntity | None
    state: StateName | None
    reason: ReasonCode
    labels: tuple[LogLabel, ...]

    @field_validator("labels")
    @classmethod
    def _canonical(cls, value: tuple[LogLabel, ...]) -> tuple[LogLabel, ...]:
        require_sorted_unique(value, "labels")
        return value

    @model_validator(mode="after")
    def _state_fits_entity(self) -> Self:
        if (self.entity is None) != (self.state is None):
            raise ValueError("entity and state come together")
        if self.entity is not None and self.state is not None:
            if self.state not in {s.value for s in _STATES[self.entity]}:
                raise ValueError("state does not belong to the entity")
            own = {
                LogEntity.TASK: self.task_id,
                LogEntity.RUN: self.run_id,
                LogEntity.STEP: self.step_id,
                LogEntity.ATTEMPT: self.attempt_id,
            }[self.entity]
            if own is None:
                raise ValueError("a state change names its entity's ID")
        if (self.kind is LogKind.STATE_CHANGED) != (self.entity is not None):
            raise ValueError("only STATE_CHANGED records carry an entity state")
        return self


class AuditAction(StrEnum):
    POLICY_DECISION = "POLICY_DECISION"
    APPROVAL_RECORDED = "APPROVAL_RECORDED"
    PERMIT_ISSUED = "PERMIT_ISSUED"
    PERMIT_CONSUMED = "PERMIT_CONSUMED"


class AuditDecision(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class AuditRecord(_Trace):
    """Minimal audit record (C2 9.2 subset). No body field exists. The full audit
    log (sequence, chain, signed checkpoints) is WP-09."""

    audit_schema_version: Literal[1] = 1
    action: AuditAction
    decision: AuditDecision
    reason: ReasonCode
    recorded_at_ms: TimestampMs
    policy_version: PolicyVersion
