"""Entity IDs: format, typed kinds, and Pydantic integration (E_INTEGRATION 2.2)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_core.types.errors import IdKindMismatchError, InvalidIdError
from agent_core.types.ids import (
    MAX_TIMESTAMP_MS,
    AttemptId,
    EntityId,
    EventId,
    IdKind,
    RunId,
    SiteId,
    SiteRole,
    TaskId,
    parse_entity_id,
)

SITE = SiteId("dev01")
TS = 1_780_000_000_000  # 2026-05-28T...Z, synthetic
RAND = 0x0123456789ABCDEF0123  # 80 bits


def test_compose_produces_the_documented_format() -> None:
    task_id = TaskId.compose(SITE, TS, RAND)
    expected_value = f"{(TS << 80) | RAND:032x}"
    assert str(task_id) == f"tsk_dev01_{expected_value}"
    assert task_id.kind is IdKind.TASK
    assert task_id.site == SITE
    assert task_id.timestamp_ms == TS
    assert task_id.random_bits == RAND


def test_parse_round_trips_and_returns_the_typed_class() -> None:
    original = RunId.compose(SiteId("eval7"), TS, RAND)
    parsed = parse_entity_id(str(original))
    assert type(parsed) is RunId
    assert parsed == original
    assert parsed.site.role is SiteRole.EVAL


@pytest.mark.parametrize(
    "value",
    [
        "",
        "tsk",
        "tsk_dev01",
        "tsk_dev01_" + "0" * 31,
        "tsk_dev01_" + "0" * 33,
        "tsk_dev01_" + "A" * 32,
        "tsk_dev01_" + "g" * 32,
        "TSK_dev01_" + "0" * 32,
        "tsk_prod1_" + "0" * 32,
        "tsk_dev_" + "0" * 32,
        "tsk_dev01_" + "0" * 32 + "_x",
        " tsk_dev01_" + "0" * 32,
        "tsk_dev01_" + "0" * 32 + "\n",
    ],
)
def test_malformed_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdError):
        TaskId(value)


def test_unknown_kind_prefix_is_rejected() -> None:
    with pytest.raises(InvalidIdError):
        parse_entity_id("zzz_dev01_" + "0" * 32)


def test_one_kind_cannot_be_used_as_another() -> None:
    task_id = TaskId.compose(SITE, TS, RAND)
    with pytest.raises(IdKindMismatchError):
        RunId(str(task_id))
    with pytest.raises(IdKindMismatchError):
        AttemptId(task_id)


def test_base_entity_id_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        EntityId("tsk_dev01_" + "0" * 32)


@pytest.mark.parametrize(
    ("timestamp_ms", "random_bits"),
    [(-1, 0), (MAX_TIMESTAMP_MS + 1, 0), (0, -1), (0, 1 << 80), (True, 0), (0, True)],
)
def test_compose_rejects_out_of_range_parts(timestamp_ms: int, random_bits: int) -> None:
    with pytest.raises(InvalidIdError):
        TaskId.compose(SITE, timestamp_ms, random_bits)


@pytest.mark.parametrize("value", ["dev", "eval", "prod1", "dev_1", "Dev1", "dev" + "a" * 13, ""])
def test_invalid_site_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdError):
        SiteId(value)


def test_site_role_distinguishes_development_and_evaluation() -> None:
    assert SiteId("dev01").role is SiteRole.DEV
    assert SiteId("eval01").role is SiteRole.EVAL


class _Holder(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    run_id: RunId


def test_pydantic_field_accepts_only_its_own_kind() -> None:
    run_id = RunId.compose(SITE, TS, RAND)
    assert _Holder(run_id=run_id).run_id is run_id
    assert type(_Holder.model_validate_json(f'{{"run_id": "{run_id}"}}').run_id) is RunId

    task_id = TaskId.compose(SITE, TS, RAND)
    with pytest.raises(ValidationError) as excinfo:
        _Holder.model_validate({"run_id": task_id})
    assert excinfo.value.errors()[0]["type"] == "id_kind_mismatch"
    with pytest.raises(ValidationError):
        _Holder.model_validate_json(f'{{"run_id": "{task_id}"}}')


@pytest.mark.parametrize("value", [123, None, b"run_dev01_" + b"0" * 22, ["x"]])
def test_pydantic_field_rejects_non_string_values(value: object) -> None:
    with pytest.raises(ValidationError):
        _Holder.model_validate({"run_id": value})


def test_ids_serialize_as_plain_strings() -> None:
    event_id = EventId.compose(SITE, TS, RAND)

    class _EventHolder(BaseModel):
        event_id: EventId

    assert _EventHolder(event_id=event_id).model_dump(mode="json") == {"event_id": str(event_id)}
