"""Structured logging (WP-11; E 7.7, C2 4.5, C2.X4, C2.X25).

Records hold fixed metadata only: IDs, engine, kind, state, reason code, times
and allowed labels. There is no free-text or dictionary field anywhere.
"""

from __future__ import annotations

import json
import os
import socket
import traceback
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.adapters.jsonl_log import JsonLinesLogSink
from agent_core.adapters.system import SystemMonotonicClock
from agent_core.observability.audit import AuditWriter
from agent_core.observability.logger import StructuredLogger, reason_for
from agent_core.ports.log_sink import LogSink
from agent_core.types.errors import (
    AuditRecursionError,
    AuditWriteError,
    IllegalTransitionError,
    JournalError,
    LogCapacityError,
    LogFileChangedError,
    LogPartialWriteError,
    LogRecordTooLargeError,
    LogSinkStoppedError,
    LogWriteError,
)
from agent_core.types.ids import AttemptId, EventId, RunId, StepId, TaskId
from agent_core.types.observability import (
    AuditAction,
    AuditDecision,
    AuditRecord,
    LogEntity,
    LogLabel,
    LogRecord,
    ReasonCode,
)
from support import builders as b

MARKER = "synthetic-secret-" + "Hv8" * 6


class MemorySink:
    def __init__(self, capacity_bytes: int = 1_000_000, fail: bool = False) -> None:
        self.lines: list[bytes] = []
        self.capacity_bytes = capacity_bytes
        self.fail = fail

    def write(self, line: bytes) -> None:
        if self.fail:
            raise LogWriteError("synthetic sink failure")
        if sum(map(len, self.lines)) + len(line) > self.capacity_bytes:
            raise LogCapacityError("synthetic capacity reached")
        self.lines.append(line)


class FakeMonotonic:
    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def monotonic_ns(self) -> int:
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]


class FixedClock:
    def __init__(self, now: int = b.TS) -> None:
        self.now = now

    def now_ms(self) -> int:
        return self.now


def _ids() -> dict[str, Any]:
    return {
        "task_id": b.new_id(TaskId),
        "run_id": b.new_id(RunId),
        "step_id": b.new_id(StepId),
        "attempt_id": b.new_id(AttemptId),
    }


def _logger(sink: Any = None, monotonic: Any = None) -> tuple[StructuredLogger, Any]:
    the_sink = sink if sink is not None else MemorySink()
    logger = StructuredLogger(
        sink=the_sink,
        clock=FixedClock(),
        monotonic=monotonic if monotonic is not None else FakeMonotonic(0),
        engine=b.ENGINE,
    )
    return logger, the_sink


# ---------------------------------------------------------------- records


def test_state_change_is_a_fixed_metadata_record() -> None:
    logger, sink = _logger()
    ids = _ids()
    logger.state_changed(entity=LogEntity.ATTEMPT, state="PERMIT_CONSUMED", **ids)
    (line,) = sink.lines
    data = json.loads(line)
    assert data["kind"] == "STATE_CHANGED"
    assert data["state"] == "PERMIT_CONSUMED"
    assert data["reason"] == "OK"
    assert data["attempt_id"] == str(ids["attempt_id"])
    assert set(data) == set(LogRecord.model_fields)
    assert line.endswith(b"\n")
    assert line == json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def test_trace_from_task_to_attempt_is_required() -> None:
    ids = _ids()
    logger, _ = _logger()
    for missing in ("task_id", "run_id", "step_id"):
        broken = {**ids, missing: None}
        with pytest.raises(ValidationError):
            logger.state_changed(entity=LogEntity.ATTEMPT, state="PROPOSED", **broken)


def test_id_kinds_cannot_be_swapped() -> None:
    ids = _ids()
    logger, _ = _logger()
    with pytest.raises(ValidationError):
        logger.state_changed(
            entity=LogEntity.RUN, state="RUNNING", task_id=ids["task_id"], run_id=ids["step_id"]
        )


@pytest.mark.parametrize(
    ("entity", "state"),
    [(LogEntity.RUN, "PERMIT_CONSUMED"), (LogEntity.ATTEMPT, "RUNNING"), (LogEntity.TASK, MARKER)],
)
def test_state_must_belong_to_the_entity(entity: LogEntity, state: str) -> None:
    logger, sink = _logger()
    with pytest.raises(ValidationError):
        logger.state_changed(entity=entity, state=state, **_ids())
    assert sink.lines == []


def test_unknown_fields_and_free_text_are_impossible() -> None:
    base = json.loads(_one_line())
    for extra in ({"message": MARKER}, {"details": {"k": MARKER}}, {"prompt": MARKER}):
        with pytest.raises(ValidationError):
            LogRecord.model_validate_json(json.dumps({**base, **extra}))
    with pytest.raises(ValidationError):
        LogRecord.model_validate_json(json.dumps({**base, "reason": MARKER}))
    with pytest.raises(ValidationError):
        LogRecord.model_validate_json(json.dumps({**base, "labels": ["confidential"]}))


def _one_line() -> bytes:
    logger, sink = _logger()
    logger.state_changed(
        entity=LogEntity.RUN, state="RUNNING", **{**_ids(), "step_id": None, "attempt_id": None}
    )
    line: bytes = sink.lines[0]
    return line


def test_only_allowed_labels() -> None:
    assert {label.value for label in LogLabel} == {"synthetic", "public", "internal"}


# ---------------------------------------------------------------- information protection


def test_exception_messages_never_reach_the_log() -> None:
    logger, sink = _logger()
    error = IllegalTransitionError(f"illegal with {MARKER}")
    logger.operation_failed(error, **_ids())
    other = ValueError(f"unexpected {MARKER}")
    logger.operation_failed(other, **_ids())
    text = b"".join(sink.lines).decode()
    assert MARKER not in text
    reasons = [json.loads(line)["reason"] for line in sink.lines]
    assert reasons == ["ILLEGAL_TRANSITION", "UNCLASSIFIED_ERROR"]


def test_reason_codes_are_fixed() -> None:
    assert reason_for(IllegalTransitionError("x")) is ReasonCode.ILLEGAL_TRANSITION
    assert reason_for(KeyError(MARKER)) is ReasonCode.UNCLASSIFIED_ERROR


def test_records_cannot_carry_synthetic_secrets_anywhere() -> None:
    logger, sink = _logger()
    ids = _ids()
    logger.state_changed(entity=LogEntity.STEP, state="IN_PROGRESS", **{**ids, "attempt_id": None})
    logger.operation_finished(reason=ReasonCode.OK, elapsed_ms=5, **ids)
    for line in sink.lines:
        LogRecord.model_validate_json(line)
        for name, info in LogRecord.model_fields.items():
            assert info.annotation is not str, name  # no plain free-text field exists


# ---------------------------------------------------------------- sinks and limits


def test_sink_failure_is_a_log_error_not_a_journal_error() -> None:
    logger, _ = _logger(sink=MemorySink(fail=True))
    with pytest.raises(LogWriteError) as excinfo:
        logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))
    assert not isinstance(excinfo.value, JournalError)
    assert not excinfo.value.fatal


def test_record_size_limit() -> None:
    sink = MemorySink()
    logger = StructuredLogger(
        sink=sink,
        clock=FixedClock(),
        monotonic=FakeMonotonic(0),
        engine=b.ENGINE,
        max_record_bytes=64,
    )
    with pytest.raises(LogRecordTooLargeError):
        logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))
    assert sink.lines == []


def test_storage_cap_is_enforced(tmp_path: Path) -> None:
    sink = JsonLinesLogSink(tmp_path / "observability.jsonl", max_total_bytes=700)
    logger, _ = _logger(sink=sink)

    def fill() -> None:
        for _ in range(10):
            logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))

    with pytest.raises(LogCapacityError):
        fill()
    size = (tmp_path / "observability.jsonl").stat().st_size
    assert size <= 700
    lines = (tmp_path / "observability.jsonl").read_bytes().splitlines()
    assert all(LogRecord.model_validate_json(line) for line in lines)


def test_jsonl_sink_is_local_append_only_and_refuses_links(tmp_path: Path) -> None:
    path = tmp_path / "observability.jsonl"
    sink = JsonLinesLogSink(path, max_total_bytes=10_000)
    assert isinstance(sink, LogSink)
    logger, _ = _logger(sink=sink)
    logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)
    with pytest.raises(LogWriteError):
        JsonLinesLogSink(link, max_total_bytes=10_000)
    os.link(path, tmp_path / "hard.jsonl")
    with pytest.raises(LogWriteError):
        JsonLinesLogSink(path, max_total_bytes=10_000)


def test_jsonl_sink_rejects_malformed_lines(tmp_path: Path) -> None:
    sink = JsonLinesLogSink(tmp_path / "o.jsonl", max_total_bytes=10_000)
    for line in (b"no newline", b"two\nlines\n", b""):
        with pytest.raises(LogWriteError):
            sink.write(line)


# ---------------------------------------------------------------- time


def test_elapsed_time_uses_the_monotonic_clock_deterministically() -> None:
    logger, sink = _logger(monotonic=FakeMonotonic(1_000_000_000, 1_250_000_000))
    timer = logger.start_timer()
    logger.operation_finished(reason=ReasonCode.OK, elapsed_ms=timer.elapsed_ms(), **_ids())
    assert json.loads(sink.lines[0])["elapsed_ms"] == 250


def test_elapsed_time_is_never_negative() -> None:
    logger, _ = _logger(monotonic=FakeMonotonic(5_000_000_000, 1_000_000_000))
    timer = logger.start_timer()
    assert timer.elapsed_ms() == 0
    with pytest.raises(ValidationError):
        logger.operation_finished(reason=ReasonCode.OK, elapsed_ms=-1, **_ids())


def test_wall_clock_going_back_does_not_affect_elapsed_time() -> None:
    clock = FixedClock(b.TS)
    sink = MemorySink()
    logger = StructuredLogger(
        sink=sink, clock=clock, monotonic=FakeMonotonic(0, 2_000_000), engine=b.ENGINE
    )
    timer = logger.start_timer()
    clock.now = b.TS - 60_000
    logger.operation_finished(reason=ReasonCode.OK, elapsed_ms=timer.elapsed_ms(), **_ids())
    data = json.loads(sink.lines[0])
    assert (data["elapsed_ms"], data["recorded_at_ms"]) == (2, b.TS - 60_000)


def test_system_monotonic_clock_is_non_decreasing() -> None:
    clock = SystemMonotonicClock()
    first, second = clock.monotonic_ns(), clock.monotonic_ns()
    assert second >= first >= 0


# ---------------------------------------------------------------- audit is separate


def _audit_record() -> AuditRecord:
    return AuditRecord(
        action=AuditAction.PERMIT_CONSUMED,
        decision=AuditDecision.ALLOWED,
        reason=ReasonCode.OK,
        recorded_at_ms=b.TS,
        policy_version="policy-1",
        event_id=b.new_id(EventId),
        **_ids(),
    )


def test_audit_write_failure_stops_the_operation() -> None:
    writer = AuditWriter(sink=MemorySink(fail=True))
    with pytest.raises(AuditWriteError) as excinfo:
        writer.write(_audit_record())
    assert excinfo.value.fatal


def test_audit_writer_never_recurses() -> None:
    holder: dict[str, AuditWriter] = {}

    class RecursingSink(MemorySink):
        def write(self, line: bytes) -> None:
            holder["writer"].write(_audit_record())  # e.g. a hook that audits its own write

    writer = AuditWriter(sink=RecursingSink())
    holder["writer"] = writer
    with pytest.raises(AuditRecursionError):
        writer.write(_audit_record())


def test_audit_record_has_no_body_field() -> None:
    record = _audit_record()
    assert "message" not in AuditRecord.model_fields
    writer = AuditWriter(sink=MemorySink())
    writer.write(record)


def test_no_network_is_used(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        socket.socket()
    sink = JsonLinesLogSink(tmp_path / "o.jsonl", max_total_bytes=10_000)
    logger, _ = _logger(sink=sink)
    logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))


# ---------------------------------------------------------- WP11-R01: capacity and single writer


def _write_one(sink: Any) -> None:
    logger, _ = _logger(sink=sink)
    logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))


def test_second_sink_on_the_same_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    first = JsonLinesLogSink(path, max_total_bytes=10_000)
    try:
        with pytest.raises(LogWriteError):
            JsonLinesLogSink(path, max_total_bytes=10_000)
        _write_one(first)
    finally:
        first.close()
    second = JsonLinesLogSink(path, max_total_bytes=10_000)
    second.close()


def test_external_size_change_stops_the_sink(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    sink = JsonLinesLogSink(path, max_total_bytes=10_000)
    _write_one(sink)
    with path.open("ab") as other:
        other.write(b"x" * 10)
    with pytest.raises(LogFileChangedError):
        _write_one(sink)
    with pytest.raises(LogSinkStoppedError):
        _write_one(sink)


def test_existing_file_over_the_cap_is_refused_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    path.write_bytes(b"x" * 100)
    path.chmod(0o600)
    with pytest.raises(LogCapacityError):
        JsonLinesLogSink(path, max_total_bytes=50)
    assert path.read_bytes() == b"x" * 100


# ---------------------------------------------------------- WP11-R02: partial writes and files


def test_partial_write_stops_the_sink_and_keeps_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "o.jsonl"
    sink = JsonLinesLogSink(path, max_total_bytes=10_000)
    real_write = os.write

    def short_write(fd: int, data: bytes) -> int:
        return real_write(fd, data[:5])

    monkeypatch.setattr("agent_core.adapters.jsonl_log.os.write", short_write)
    with pytest.raises(LogPartialWriteError):
        _write_one(sink)
    monkeypatch.undo()
    with pytest.raises(LogSinkStoppedError):
        _write_one(sink)
    assert len(path.read_bytes()) == 5  # the partial tail stays; nothing is truncated


def test_os_error_during_write_stops_the_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = JsonLinesLogSink(tmp_path / "o.jsonl", max_total_bytes=10_000)

    def failing_write(_fd: int, _data: bytes) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("agent_core.adapters.jsonl_log.os.write", failing_write)
    with pytest.raises(LogWriteError):
        _write_one(sink)
    monkeypatch.undo()
    with pytest.raises(LogSinkStoppedError):
        _write_one(sink)


def test_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(LogWriteError):
        JsonLinesLogSink(fifo, max_total_bytes=10_000)


def test_group_or_world_accessible_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    path.write_bytes(b"")
    path.chmod(0o644)
    with pytest.raises(LogWriteError):
        JsonLinesLogSink(path, max_total_bytes=10_000)


def test_replaced_file_stops_the_sink(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    sink = JsonLinesLogSink(path, max_total_bytes=10_000)
    path.unlink()
    path.write_bytes(b"")
    with pytest.raises(LogFileChangedError):
        _write_one(sink)


# ---------------------------------------------------------------- WP11-R03: audit limits and errors


def test_audit_record_size_limit() -> None:
    writer = AuditWriter(sink=MemorySink(), max_record_bytes=64)
    with pytest.raises(AuditWriteError):
        writer.write(_audit_record())


@pytest.mark.parametrize("value", [0, -1, 10_000_000, True])
def test_invalid_writer_settings_are_refused(value: Any) -> None:
    with pytest.raises(AuditWriteError):
        AuditWriter(sink=MemorySink(), max_record_bytes=value)
    with pytest.raises(LogWriteError):
        StructuredLogger(
            sink=MemorySink(),
            clock=FixedClock(),
            monotonic=FakeMonotonic(0),
            engine=b.ENGINE,
            max_record_bytes=value,
        )


class _LeakySink:
    def write(self, line: bytes) -> None:
        raise OSError(f"disk failure near {MARKER}")


def test_sink_exception_text_does_not_leak_from_the_audit_writer() -> None:
    writer = AuditWriter(sink=_LeakySink())
    with pytest.raises(AuditWriteError) as excinfo:
        writer.write(_audit_record())
    rendered = "".join(traceback.format_exception(excinfo.value))
    assert MARKER not in rendered
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_sink_exception_text_does_not_leak_from_the_logger() -> None:
    logger, _ = _logger(sink=_LeakySink())
    with pytest.raises(LogWriteError) as excinfo:
        logger.state_changed(entity=LogEntity.TASK, state="ACTIVE", task_id=b.new_id(TaskId))
    assert MARKER not in "".join(traceback.format_exception(excinfo.value))
