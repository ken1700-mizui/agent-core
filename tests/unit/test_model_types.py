"""Common model types (WP-05, E 7.4 and 8, B1 2.4 and 6.2)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.types.errors import RecordValidationError
from agent_core.types.ids import ModelRequestId, RunId
from agent_core.types.model import (
    CostStatus,
    Message,
    ModelRequest,
    ModelResponse,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageStatus,
    context_hash,
    rendering_hash,
    request_hash,
)
from agent_core.types.serialization import dump_json, load_json
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import model_world as m


def _usage(**overrides: Any) -> Usage:
    values: dict[str, Any] = {
        "status": UsageStatus.FINAL,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cost": Budget(currency=Currency.XTU, micro_units=30),
        "cost_status": CostStatus.FINAL,
        "synthetic": True,
    }
    values.update(overrides)
    return Usage(**values)


def _tool_use(tool_id: str = "call_1") -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=tool_id, name="read_file", arguments='{"path":"a.txt"}')


def _response(**overrides: Any) -> ModelResponse:
    values: dict[str, Any] = {
        "model_request_id": b.new_id(ModelRequestId),
        "content": (TextBlock(type="text", text="synthetic answer"),),
        "stop_reason": StopReason.END_TURN,
        "usage": _usage(),
        "provider_request_id": "fake-1",
        "final_usage": True,
    }
    values.update(overrides)
    return ModelResponse(**values)


# ---------------------------------------------------------------- valid records and round trips


def test_valid_request_and_response_round_trip() -> None:
    for record in (m.request(), _response(), m.profile()):
        data = dump_json(record)
        assert load_json(type(record), data) == record
        assert dump_json(load_json(type(record), data)) == data


def test_records_are_frozen() -> None:
    request = m.request()
    with pytest.raises(ValidationError):
        request.messages = ()  # type: ignore[misc]  # runtime check of frozen


# ---------------------------------------------------------------- hashes are distinct types


def test_the_three_hashes_have_distinct_formats() -> None:
    assert context_hash(b"x").startswith("ctxhash-v1:sha256:")
    assert rendering_hash(b"x").startswith("renderhash-v1:sha256:")
    assert request_hash(b"x").startswith("reqhash-v1:sha256:")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_hash", rendering_hash(b"x")),
        ("rendering_hash", request_hash(b"x")),
        ("request_hash", context_hash(b"x")),
        ("request_hash", b.digest("plain")),
    ],
)
def test_hashes_cannot_be_swapped(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        m.request(**{field: value})


# ---------------------------------------------------------------- rejection


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_request_id": b.new_id(RunId)},
        {"messages": ()},
        {"messages": [m.user("list, not tuple")]},
        {"unknown": 1},
        {"params": {"max_output_tokens": 0, "temperature_milli": 0, "timeout_ms": 1}},
    ],
)
def test_invalid_requests_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        m.request(**overrides)


@pytest.mark.parametrize(
    "params",
    [
        {"max_output_tokens": -1, "temperature_milli": 0, "timeout_ms": 1_000},
        {"max_output_tokens": 10, "temperature_milli": 2_001, "timeout_ms": 1_000},
        {"max_output_tokens": 10, "temperature_milli": 0, "timeout_ms": 0},
        {"max_output_tokens": 10, "temperature_milli": 0.5, "timeout_ms": 1_000},
    ],
)
def test_invalid_generation_params_are_rejected(params: dict[str, Any]) -> None:
    data = json.loads(dump_json(m.request()))
    data["params"] = params
    with pytest.raises(RecordValidationError):
        load_json(ModelRequest, json.dumps(data))


def test_message_roles_and_blocks_must_match() -> None:
    with pytest.raises(ValidationError):
        Message(role=Role.USER, content=(_tool_use(),))
    with pytest.raises(ValidationError):
        Message(
            role=Role.ASSISTANT,
            content=(
                ToolResultBlock(
                    type="tool_result",
                    tool_use_id="call_1",
                    content=(TextBlock(type="text", text="r"),),
                    is_error=False,
                ),
            ),
        )
    with pytest.raises(ValidationError):
        Message(role=Role.USER, content=())


def test_tool_result_must_answer_an_earlier_tool_use() -> None:
    result = Message(
        role=Role.USER,
        content=(
            ToolResultBlock(
                type="tool_result",
                tool_use_id="call_9",
                content=(TextBlock(type="text", text="r"),),
                is_error=False,
            ),
        ),
    )
    with pytest.raises(ValidationError):
        m.request(m.user("q"), result)
    asked = Message(role=Role.ASSISTANT, content=(_tool_use("call_9"),))
    assert m.request(m.user("q"), asked, result).messages[-1] == result


@pytest.mark.parametrize(
    "arguments", ["[]", '{"a":1,"a":2}', "{", '{"a": NaN}', '{"b":1, "a":2}', "null"]
)
def test_tool_arguments_must_be_a_canonical_json_object(arguments: str) -> None:
    with pytest.raises(ValidationError):
        ToolUseBlock(type="tool_use", id="call_1", name="read_file", arguments=arguments)


@pytest.mark.parametrize("text", ["", "a\x00b"])
def test_text_must_be_non_empty_without_nul(text: str) -> None:
    with pytest.raises(ValidationError):
        TextBlock(type="text", text=text)


def test_unknown_content_type_is_rejected() -> None:
    data = json.loads(dump_json(m.request()))
    data["messages"][0]["content"][0] = {"type": "image", "data": "x"}
    with pytest.raises(RecordValidationError):
        load_json(ModelRequest, json.dumps(data))


def test_stop_reason_tool_use_needs_a_tool_use_block() -> None:
    with pytest.raises(ValidationError):
        _response(stop_reason=StopReason.TOOL_USE)
    with pytest.raises(ValidationError):
        _response(content=(_tool_use(),), stop_reason=StopReason.END_TURN)
    assert _response(content=(_tool_use(),), stop_reason=StopReason.TOOL_USE).content


# ---------------------------------------------------------------- usage


def test_unknown_usage_is_not_zero() -> None:
    unknown = _usage(
        status=UsageStatus.UNKNOWN,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost=None,
        cost_status=CostStatus.UNKNOWN,
    )
    assert unknown.input_tokens is None
    with pytest.raises(ValidationError):
        _usage(status=UsageStatus.UNKNOWN, input_tokens=0, output_tokens=0, total_tokens=0)


def test_token_usage_and_cost_are_independent() -> None:
    # WP05-R01: a provider may report a final bill while token usage is unknown,
    # and zero tokens do not imply a known zero cost.
    billed = _usage(
        status=UsageStatus.UNKNOWN, input_tokens=None, output_tokens=None, total_tokens=None
    )
    assert billed.cost is not None
    zero_tokens = _usage(
        input_tokens=0, output_tokens=0, total_tokens=0, cost=None, cost_status=CostStatus.UNKNOWN
    )
    assert zero_tokens.cost is None
    with pytest.raises(ValidationError):
        _usage(cost=None, cost_status=CostStatus.FINAL)
    with pytest.raises(ValidationError):
        _usage(cost_status=CostStatus.UNKNOWN)


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": None},
        {"total_tokens": 16},
        {"input_tokens": -1, "total_tokens": 4},
        {"output_tokens": None},
        {"synthetic": "yes"},
        {"input_tokens": 1.5},
    ],
)
def test_invalid_final_usage_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _usage(**overrides)


def test_final_usage_flag_matches_the_usage_status() -> None:
    with pytest.raises(ValidationError):
        _response(final_usage=False)
    partial = _usage(
        status=UsageStatus.PARTIAL, total_tokens=None, cost=None, cost_status=CostStatus.UNKNOWN
    )
    with pytest.raises(ValidationError):
        _response(usage=partial, final_usage=True)
    assert _response(usage=partial, final_usage=False).final_usage is False


def test_model_records_grant_nothing() -> None:
    response = _response(content=(_tool_use(),), stop_reason=StopReason.TOOL_USE)
    public = {name for name in dir(response) if not name.startswith("_")}
    assert not {n for n in public if n.startswith(("execute", "run", "dispatch", "authorize"))}


# ---------------------------------------------------------------- WP05-R02: tool ids and results


def _result(tool_id: str) -> ToolResultBlock:
    return ToolResultBlock(
        type="tool_result",
        tool_use_id=tool_id,
        content=(TextBlock(type="text", text="r"),),
        is_error=False,
    )


def test_response_rejects_duplicate_tool_use_ids() -> None:
    with pytest.raises(ValidationError):
        _response(
            content=(_tool_use("call_1"), _tool_use("call_1")), stop_reason=StopReason.TOOL_USE
        )


def test_request_rejects_two_results_for_one_tool_use() -> None:
    asked = Message(role=Role.ASSISTANT, content=(_tool_use("call_1"),))
    answered = Message(role=Role.USER, content=(_result("call_1"),))
    with pytest.raises(ValidationError):
        m.request(
            m.user("q"), asked, answered, Message(role=Role.USER, content=(_result("call_1"),))
        )
    with pytest.raises(ValidationError):
        m.request(
            m.user("q"),
            asked,
            Message(role=Role.USER, content=(_result("call_1"), _result("call_1"))),
        )


def test_multi_tool_conversation_is_accepted() -> None:
    asked = Message(role=Role.ASSISTANT, content=(_tool_use("call_1"), _tool_use("call_2")))
    answered = Message(role=Role.USER, content=(_result("call_2"), _result("call_1")))
    follow_up = Message(role=Role.ASSISTANT, content=(_tool_use("call_3"),))
    final = Message(role=Role.USER, content=(_result("call_3"),))
    request = m.request(m.user("q"), asked, answered, follow_up, final)
    assert len(request.messages) == 5


# ---------------------------------------------------------------- WP05-R03: strict JSON arguments


MARKER = "synthetic-secret-" + "Pq4" * 6


@pytest.mark.parametrize(
    "arguments",
    [
        '{"a":"\\ud800"}',
        "[" * 100 + "]" * 100,
        '{"a":' * 80 + "1" + "}" * 80,
        '{"a":1e400}',
    ],
    ids=["lone-surrogate", "deep-array", "deep-object", "overflow"],
)
def test_tool_arguments_use_the_strict_json_rules(arguments: str) -> None:
    with pytest.raises(ValidationError):
        ToolUseBlock(type="tool_use", id="call_1", name="read_file", arguments=arguments)


def test_invalid_arguments_do_not_leak_through_typed_errors() -> None:
    data = json.loads(dump_json(_response()))
    data["content"] = [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "read_file",
            "arguments": f'{{"{MARKER}":1,"{MARKER}":2}}',
        }
    ]
    data["stop_reason"] = "TOOL_USE"
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(ModelResponse, json.dumps(data))
    assert MARKER not in str(excinfo.value)
    assert MARKER not in repr(excinfo.value.issues)
