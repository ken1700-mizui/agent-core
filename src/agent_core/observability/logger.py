"""Structured observation logger (WP-11; E 7.7, C2.X4).

- Records are built only from typed arguments into ``LogRecord`` (fixed
  metadata). There is no method that accepts free text or a dictionary.
- A failure is logged by its fixed reason code (``reason_for``); the exception
  message is never read.
- Human time (``recorded_at_ms``) comes from the wall clock; durations come
  from the monotonic clock and are never negative.
- Each record is at most ``max_record_bytes``. A sink failure raises
  ``LogWriteError``: an observation-log failure, never a Journal error.
"""

from __future__ import annotations

from agent_core.ports.clock import Clock
from agent_core.ports.log_sink import LogSink, MonotonicClock
from agent_core.types.errors import LogRecordTooLargeError, LogWriteError
from agent_core.types.ids import AttemptId, EventId, RunId, StepId, TaskId
from agent_core.types.observability import (
    LOG_SCHEMA_VERSION,
    LogEntity,
    LogKind,
    LogLabel,
    LogRecord,
    ReasonCode,
)
from agent_core.types.serialization import dump_json
from agent_core.types.values import EngineVersion

DEFAULT_MAX_RECORD_BYTES = 2_048
_NS_PER_MS = 1_000_000


def reason_for(error: BaseException) -> ReasonCode:
    """The fixed reason code of a typed error; anything else is UNCLASSIFIED_ERROR."""
    code = getattr(type(error), "code", None)
    if isinstance(code, str) and code in ReasonCode.__members__:
        return ReasonCode(code)
    return ReasonCode.UNCLASSIFIED_ERROR


class Timer:
    def __init__(self, monotonic: MonotonicClock) -> None:
        self._monotonic = monotonic
        self._start = monotonic.monotonic_ns()

    def elapsed_ms(self) -> int:
        # A misbehaving clock must not produce a negative duration.
        return max(0, (self._monotonic.monotonic_ns() - self._start) // _NS_PER_MS)


class StructuredLogger:
    def __init__(
        self,
        *,
        sink: LogSink,
        clock: Clock,
        monotonic: MonotonicClock,
        engine: EngineVersion,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        labels: tuple[LogLabel, ...] = (LogLabel.SYNTHETIC,),
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._monotonic = monotonic
        self._engine = engine
        self._max_record_bytes = max_record_bytes
        self._labels = labels

    def start_timer(self) -> Timer:
        return Timer(self._monotonic)

    def state_changed(
        self,
        *,
        entity: LogEntity,
        state: str,
        task_id: TaskId,
        run_id: RunId | None = None,
        step_id: StepId | None = None,
        attempt_id: AttemptId | None = None,
        event_id: EventId | None = None,
    ) -> LogRecord:
        return self._write(
            kind=LogKind.STATE_CHANGED,
            entity=entity,
            state=state,
            reason=ReasonCode.OK,
            elapsed_ms=None,
            ids=(task_id, run_id, step_id, attempt_id, event_id),
        )

    def operation_finished(
        self,
        *,
        reason: ReasonCode,
        elapsed_ms: int,
        task_id: TaskId,
        run_id: RunId | None = None,
        step_id: StepId | None = None,
        attempt_id: AttemptId | None = None,
        event_id: EventId | None = None,
    ) -> LogRecord:
        return self._write(
            kind=LogKind.OPERATION_FINISHED,
            entity=None,
            state=None,
            reason=reason,
            elapsed_ms=elapsed_ms,
            ids=(task_id, run_id, step_id, attempt_id, event_id),
        )

    def operation_failed(
        self,
        error: BaseException,
        *,
        task_id: TaskId,
        run_id: RunId | None = None,
        step_id: StepId | None = None,
        attempt_id: AttemptId | None = None,
        event_id: EventId | None = None,
        elapsed_ms: int | None = None,
    ) -> LogRecord:
        """Log a failure by its reason code only; ``str(error)`` is never used."""
        return self._write(
            kind=LogKind.OPERATION_FAILED,
            entity=None,
            state=None,
            reason=reason_for(error),
            elapsed_ms=elapsed_ms,
            ids=(task_id, run_id, step_id, attempt_id, event_id),
        )

    def _write(
        self,
        *,
        kind: LogKind,
        entity: LogEntity | None,
        state: str | None,
        reason: ReasonCode,
        elapsed_ms: int | None,
        ids: tuple[TaskId | None, RunId | None, StepId | None, AttemptId | None, EventId | None],
    ) -> LogRecord:
        task_id, run_id, step_id, attempt_id, event_id = ids
        record = LogRecord(
            log_schema_version=LOG_SCHEMA_VERSION,
            kind=kind,
            recorded_at_ms=self._clock.now_ms(),
            elapsed_ms=elapsed_ms,
            engine=self._engine,
            task_id=task_id,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            event_id=event_id,
            entity=entity,
            state=state,
            reason=reason,
            labels=self._labels,
        )
        line = dump_json(record) + b"\n"
        if len(line) > self._max_record_bytes:
            raise LogRecordTooLargeError("log record exceeds the size limit")
        try:
            self._sink.write(line)
        except LogWriteError:
            raise
        except Exception as exc:
            raise LogWriteError("log sink failed") from exc
        return record
