"""Fake Provider (WP-05): deterministic synthetic replies and fault injection.

Usage values are synthetic test data, not real token counts or real costs.
No test waits: timeouts are decided from synthetic elapsed times.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from agent_core.adapters.fake_model import (
    FakeFault,
    FakeFaultKind,
    FakeModelProvider,
    FakePrice,
    FakeReply,
    FakeScript,
    request_fingerprint,
)
from agent_core.ports.model import ModelPort
from agent_core.types.model import (
    GenerationParams,
    ModelCallOutcome,
    ModelResponse,
    ProfileRef,
    ProviderKind,
    StopReason,
    TextBlock,
    ToolUseBlock,
    UsageStatus,
)
from agent_core.types.model_errors import (
    FakeScriptMissError,
    ModelDefiniteFailureError,
    ModelError,
    ModelInvalidRequestError,
    ModelOutcomeUnknownError,
    ModelResponseInvalidError,
    ModelTimeoutError,
    ModelUnsupportedError,
)
from support import model_world as m

PRICE = FakePrice(input_micro_xtu_per_mtok=1_000_000, output_micro_xtu_per_mtok=4_000_000)
SECRET_TEXT = "synthetic-secret-" + "Kq7" * 5


def _reply(text: str = "synthetic answer", **overrides: Any) -> FakeReply:
    values: dict[str, Any] = {
        "kind": "reply",
        "content": (TextBlock(type="text", text=text),),
        "stop_reason": StopReason.END_TURN,
        "input_tokens": 120,
        "output_tokens": 30,
        "elapsed_ms": 10,
    }
    values.update(overrides)
    return FakeReply(**values)


def _fault(kind: FakeFaultKind, elapsed_ms: int = 10) -> FakeFault:
    return FakeFault(kind="fault", fault=kind, elapsed_ms=elapsed_ms)


def _provider(script: FakeScript, **overrides: Any) -> FakeModelProvider:
    return FakeModelProvider(
        profile=overrides.get("profile", m.profile()), script=script, price=PRICE
    )


def _script(request: Any, *outcomes: Any) -> FakeScript:
    return FakeScript.of({request_fingerprint(request): outcomes})


# ---------------------------------------------------------------- normal replies


def test_reply_is_a_typed_response_with_synthetic_final_usage() -> None:
    request = m.request()
    provider = _provider(_script(request, _reply()))
    assert isinstance(provider, ModelPort)
    response = provider.complete(request)
    assert isinstance(response, ModelResponse)
    assert response.model_request_id == request.model_request_id
    assert response.content == (TextBlock(type="text", text="synthetic answer"),)
    usage = response.usage
    assert usage.status is UsageStatus.FINAL
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (120, 30, 150)
    assert usage.synthetic
    # Integer arithmetic only: ceil((120 * 1_000_000 + 30 * 4_000_000) / 1_000_000) = 240.
    assert usage.cost is not None
    assert usage.cost.micro_units == 240
    assert response.final_usage


def test_tool_request_is_returned_as_data_and_not_executed() -> None:
    request = m.request()
    tool = ToolUseBlock(type="tool_use", id="call_1", name="read_file", arguments='{"path":"x"}')
    provider = _provider(_script(request, _reply(content=(tool,), stop_reason=StopReason.TOOL_USE)))
    response = provider.complete(request)
    assert response.stop_reason is StopReason.TOOL_USE
    assert response.content == (tool,)
    assert provider.calls == 1  # nothing else happened: no tool registry, no execution path


# ---------------------------------------------------------------- determinism


def test_same_script_and_inputs_give_identical_results() -> None:
    first_request, second_request = m.request(m.user("one")), m.request(m.user("two"))
    script = FakeScript.of(
        {
            request_fingerprint(first_request): (_reply("a1"), _reply("a2")),
            request_fingerprint(second_request): (_reply("b1"),),
        }
    )
    runs = []
    for _ in range(2):
        provider = _provider(script)
        runs.append(
            [
                provider.complete(first_request),
                provider.complete(second_request),
                provider.complete(first_request),
            ]
        )
    assert runs[0] == runs[1]
    assert [r.content[0].text for r in runs[0]] == ["a1", "b1", "a2"]  # type: ignore[union-attr]


def test_fingerprint_ignores_ids_but_not_content() -> None:
    a, b = m.request(m.user("same")), m.request(m.user("same"))
    assert a.model_request_id != b.model_request_id
    assert request_fingerprint(a) == request_fingerprint(b)
    assert request_fingerprint(m.request(m.user("other"))) != request_fingerprint(a)


def test_unregistered_input_is_an_explicit_error_not_a_guess() -> None:
    provider = _provider(_script(m.request(m.user("registered")), _reply()))
    with pytest.raises(FakeScriptMissError) as excinfo:
        provider.complete(m.request(m.user(SECRET_TEXT)))
    assert excinfo.value.outcome is ModelCallOutcome.NOT_SENT
    assert SECRET_TEXT not in str(excinfo.value)


def test_exhausted_sequence_is_an_explicit_error() -> None:
    request = m.request()
    provider = _provider(_script(request, _reply()))
    provider.complete(request)
    with pytest.raises(FakeScriptMissError):
        provider.complete(request)


# ---------------------------------------------------------------- fault injection


@pytest.mark.parametrize(
    ("fault", "error", "outcome"),
    [
        (
            FakeFaultKind.FAILED_BEFORE_SEND,
            ModelDefiniteFailureError,
            ModelCallOutcome.FAILED_DEFINITE,
        ),
        (
            FakeFaultKind.PROVIDER_REJECTED,
            ModelDefiniteFailureError,
            ModelCallOutcome.FAILED_DEFINITE,
        ),
        (FakeFaultKind.STREAM_INTERRUPTED, ModelOutcomeUnknownError, ModelCallOutcome.UNKNOWN),
        (FakeFaultKind.OUTCOME_UNKNOWN, ModelOutcomeUnknownError, ModelCallOutcome.UNKNOWN),
        (FakeFaultKind.INVALID_RESPONSE, ModelResponseInvalidError, ModelCallOutcome.UNKNOWN),
    ],
)
def test_faults_become_typed_errors_with_a_clear_outcome(
    fault: FakeFaultKind, error: type[ModelError], outcome: ModelCallOutcome
) -> None:
    request = m.request()
    provider = _provider(_script(request, _fault(fault)))
    with pytest.raises(error) as excinfo:
        provider.complete(request)
    assert excinfo.value.outcome is outcome
    if outcome is ModelCallOutcome.UNKNOWN:
        # Unknown is never reported as a known zero.
        assert excinfo.value.usage.status in (UsageStatus.UNKNOWN, UsageStatus.PARTIAL)
        assert excinfo.value.usage.total_tokens is None
    else:
        assert excinfo.value.usage.status is UsageStatus.FINAL
        assert excinfo.value.usage.total_tokens == 0


def test_stream_interruption_reports_partial_usage() -> None:
    request = m.request()
    provider = _provider(_script(request, _fault(FakeFaultKind.STREAM_INTERRUPTED)))
    with pytest.raises(ModelOutcomeUnknownError) as excinfo:
        provider.complete(request)
    usage = excinfo.value.usage
    assert usage.status is UsageStatus.PARTIAL
    assert usage.input_tokens is not None
    assert usage.output_tokens is None


def test_timeout_is_decided_from_synthetic_elapsed_time() -> None:
    request = m.request()  # timeout_ms = 30_000
    slow = _reply(elapsed_ms=30_001)
    provider = _provider(_script(request, slow, _reply(elapsed_ms=30_000)))
    with pytest.raises(ModelTimeoutError) as excinfo:
        provider.complete(request)
    assert excinfo.value.outcome is ModelCallOutcome.UNKNOWN
    assert excinfo.value.usage.status is UsageStatus.UNKNOWN
    # The next call is a new, separate call: the provider did not retry by itself.
    assert provider.calls == 1
    assert provider.complete(request).usage.status is UsageStatus.FINAL
    assert provider.calls == 2


def test_errors_never_retry_inside_the_provider() -> None:
    request = m.request()
    provider = _provider(_script(request, _fault(FakeFaultKind.FAILED_BEFORE_SEND), _reply()))
    with pytest.raises(ModelDefiniteFailureError):
        provider.complete(request)
    assert provider.calls == 1
    assert provider.remaining(request) == 1


# ---------------------------------------------------------------- request checks


def test_only_fake_profiles_are_accepted() -> None:
    with pytest.raises(ModelUnsupportedError):
        FakeModelProvider(
            profile=m.profile(provider=ProviderKind.UNSUPPORTED),
            script=FakeScript.of({}),
            price=PRICE,
        )


def test_request_for_another_profile_is_rejected() -> None:
    request = m.request(profile=ProfileRef(model_id="other-model", profile_version="0.1.0"))
    provider = _provider(_script(request, _reply()))
    with pytest.raises(ModelInvalidRequestError) as excinfo:
        provider.complete(request)
    assert excinfo.value.outcome is ModelCallOutcome.NOT_SENT
    assert provider.calls == 0


def test_request_over_the_profile_limit_is_rejected() -> None:
    request = m.request(
        params=GenerationParams(max_output_tokens=1_001, temperature_milli=0, timeout_ms=1_000)
    )
    provider = _provider(_script(request, _reply()))
    with pytest.raises(ModelInvalidRequestError):
        provider.complete(request)


def test_reply_over_the_requested_output_is_invalid() -> None:
    request = m.request()  # max_output_tokens = 200
    provider = _provider(_script(request, _reply(output_tokens=201)))
    with pytest.raises(ModelResponseInvalidError):
        provider.complete(request)


def test_no_network_is_needed() -> None:
    # The autouse guard refuses every Python socket; the provider still works.
    with pytest.raises(RuntimeError):
        socket.socket()
    request = m.request()
    assert _provider(_script(request, _reply())).complete(request).final_usage
