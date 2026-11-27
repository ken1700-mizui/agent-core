"""ID generation with injected Clock and RandomSource, and the duplicate retry contract."""

from __future__ import annotations

import pytest

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import MAX_DUPLICATE_RETRIES, IdGenerator
from agent_core.types.errors import (
    ClockOutOfRangeError,
    DuplicateIdError,
    IdCollisionRetryExhaustedError,
    RandomSourceError,
)
from agent_core.types.ids import (
    MAX_TIMESTAMP_MS,
    MIN_GENERATION_TIMESTAMP_MS,
    EventId,
    SiteId,
    TaskId,
)
from support.fakes import InMemoryIdRegistry, ScriptedClock, ScriptedRandomSource

SITE = SiteId("dev01")
TS = 1_780_000_000_000


def _random(*values: int) -> ScriptedRandomSource:
    return ScriptedRandomSource(v.to_bytes(10, "big") for v in values)


def test_generation_is_deterministic_with_injected_sources() -> None:
    random = _random(0x0123456789ABCDEF0123)
    generator = IdGenerator(SITE, ScriptedClock(TS), random)
    task_id = generator.new(TaskId)
    assert task_id == TaskId.compose(SITE, TS, 0x0123456789ABCDEF0123)
    assert random.requested == [10]


def test_design_retry_limit_is_three() -> None:
    assert MAX_DUPLICATE_RETRIES == 3


@pytest.mark.parametrize("chunk", [b"", b"\x00" * 9, b"\x00" * 11], ids=["empty", "short", "long"])
def test_wrong_length_random_output_is_a_typed_fatal_error(chunk: bytes) -> None:
    generator = IdGenerator(SITE, ScriptedClock(TS), ScriptedRandomSource([chunk]))
    with pytest.raises(RandomSourceError) as excinfo:
        generator.new(TaskId)
    assert excinfo.value.fatal


def test_failing_random_source_is_wrapped_not_swallowed() -> None:
    class _Broken:
        def token_bytes(self, n: int) -> bytes:
            raise OSError("synthetic entropy failure")

    generator = IdGenerator(SITE, ScriptedClock(TS), _Broken())
    with pytest.raises(RandomSourceError) as excinfo:
        generator.new(TaskId)
    assert isinstance(excinfo.value.__cause__, OSError)


@pytest.mark.parametrize(
    "now_ms",
    [MIN_GENERATION_TIMESTAMP_MS - 1, MAX_TIMESTAMP_MS + 1, -5, True],
)
def test_clock_outside_the_valid_range_is_rejected(now_ms: int) -> None:
    generator = IdGenerator(SITE, ScriptedClock(now_ms), _random(1))
    with pytest.raises(ClockOutOfRangeError) as excinfo:
        generator.new(TaskId)
    assert excinfo.value.fatal


def test_clock_going_backwards_is_allowed_and_id_order_is_not_execution_order() -> None:
    generator = IdGenerator(SITE, ScriptedClock(TS, TS - 5_000), _random(7, 8))
    first = generator.new(EventId)
    second = generator.new(EventId)
    assert second.timestamp_ms == TS - 5_000
    # The later ID sorts first: ordering must come from run_seq/tenant_seq, never IDs.
    assert str(second) < str(first)
    assert first != second


def test_duplicate_is_retried_with_a_new_random_part_and_same_timestamp() -> None:
    registry = InMemoryIdRegistry()
    taken = TaskId.compose(SITE, TS, 1)
    registry.register(taken)
    seen: list[TaskId] = []

    def write(task_id: TaskId) -> str:
        seen.append(task_id)
        registry.register(task_id)
        return "stored"

    generator = IdGenerator(SITE, ScriptedClock(TS), _random(1, 2))
    assert generator.issue_unique(TaskId, write) == "stored"
    assert [s.random_bits for s in seen] == [1, 2]
    assert {s.timestamp_ms for s in seen} == {TS}


def test_retry_limit_stops_with_a_typed_fatal_error() -> None:
    registry = InMemoryIdRegistry()
    registry.register(TaskId.compose(SITE, TS, 9))
    calls: list[TaskId] = []

    def write(task_id: TaskId) -> None:
        calls.append(task_id)
        registry.register(task_id)

    scripted = _random(*([9] * (MAX_DUPLICATE_RETRIES + 1)))
    generator = IdGenerator(SITE, ScriptedClock(TS), scripted)
    with pytest.raises(IdCollisionRetryExhaustedError) as excinfo:
        generator.issue_unique(TaskId, write)
    assert len(calls) == MAX_DUPLICATE_RETRIES + 1
    assert excinfo.value.fatal
    assert isinstance(excinfo.value.__cause__, DuplicateIdError)


def test_other_write_errors_are_not_retried() -> None:
    calls = 0

    def write(_task_id: TaskId) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic storage failure")

    generator = IdGenerator(SITE, ScriptedClock(TS), _random(1, 2))
    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        generator.issue_unique(TaskId, write)
    assert calls == 1


def test_in_memory_registry_detects_a_deliberate_duplicate() -> None:
    registry = InMemoryIdRegistry()
    task_id = TaskId.compose(SITE, TS, 3)
    registry.register(task_id)
    with pytest.raises(DuplicateIdError) as excinfo:
        registry.register(TaskId(str(task_id)))
    assert excinfo.value.entity_id == task_id


def test_os_sources_generate_distinct_ids_in_this_process() -> None:
    # In-memory check only; it does not prove uniqueness across restarts (WP-03).
    generator = IdGenerator(SITE, SystemClock(), OsRandomSource())
    registry = InMemoryIdRegistry()
    for _ in range(2_000):
        registry.register(generator.new(EventId))
