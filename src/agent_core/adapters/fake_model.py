"""Fake Provider (WP-05): a deterministic, synthetic ModelPort for tests and Phase 1.

- A ``FakeScript`` maps a request fingerprint to a sequence of outcomes
  (``FakeReply`` or ``FakeFault``). Each ``complete`` call consumes the next
  outcome for that fingerprint. The same script, configuration and sequence of
  inputs always give the same results.
- An input with no scripted outcome (or an exhausted sequence) raises
  ``FakeScriptMissError``. The provider never guesses a reply.
- Faults are injected deterministically; timeouts are decided from synthetic
  elapsed times, so nothing waits. There is no retry and no fallback inside.
- Usage numbers and the XTU cost are synthetic test data (``synthetic=True``),
  computed with integer arithmetic from a synthetic price; they are not real
  token counts or real costs.
- No network, no credentials, no tool execution. Only ``FAKE`` profiles are
  accepted. The name "Fake" or a synthetic label is not a safety guarantee:
  safety comes from the runtime's authorization (WP-09), not from this adapter,
  and nothing wires this adapter into an execution path by itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from agent_core.types.base import FrozenModel
from agent_core.types.model import (
    CostStatus,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderKind,
    ResponseBlock,
    StopReason,
    TokenCount,
    Usage,
    UsageStatus,
)
from agent_core.types.model_errors import (
    FakeScriptMissError,
    ModelDefiniteFailureError,
    ModelInvalidRequestError,
    ModelNotSentError,
    ModelOutcomeUnknownError,
    ModelResponseInvalidError,
    ModelTimeoutError,
    ModelUnsupportedError,
)
from agent_core.types.values import Count, Currency

FINGERPRINT_PREFIX = "fakefp-v1:sha256:"
_PER_MILLION = 1_000_000


class FakePrice(FrozenModel):
    """Synthetic price in micro-XTU per million tokens (D 10, test-virtual-v1)."""

    input_micro_xtu_per_mtok: Count
    output_micro_xtu_per_mtok: Count


class FakeReply(FrozenModel):
    kind: Literal["reply"]
    content: Annotated[tuple[ResponseBlock, ...], Field(min_length=1)]
    stop_reason: StopReason
    input_tokens: TokenCount
    output_tokens: TokenCount
    elapsed_ms: Count


class FakeFaultKind(StrEnum):
    FAILED_BEFORE_SEND = "FAILED_BEFORE_SEND"  # nothing sent (connection refused): NOT_SENT
    PROVIDER_REJECTED = "PROVIDER_REJECTED"  # sent, refused unprocessed: FAILED_DEFINITE
    STREAM_INTERRUPTED = "STREAM_INTERRUPTED"  # broken after content was sent
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"  # e.g. connection lost waiting for the reply
    INVALID_RESPONSE = "INVALID_RESPONSE"  # the provider answers with malformed data


class FakeFault(FrozenModel):
    kind: Literal["fault"]
    fault: FakeFaultKind
    elapsed_ms: Count
    # Input tokens known when a stream breaks (synthetic). Output is then unknown.
    partial_input_tokens: TokenCount = 0


FakeOutcome = Annotated[FakeReply | FakeFault, Field(discriminator="kind")]


class _Outcomes(FrozenModel):
    outcomes: tuple[FakeOutcome, ...]


class FakeScript:
    """Immutable mapping from request fingerprint to a sequence of outcomes."""

    def __init__(self, entries: Mapping[str, tuple[FakeReply | FakeFault, ...]]) -> None:
        self._entries = MappingProxyType(dict(entries))

    @classmethod
    def of(cls, entries: Mapping[Any, Any]) -> FakeScript:
        checked: dict[str, tuple[FakeReply | FakeFault, ...]] = {}
        for fingerprint, outcomes in entries.items():
            if not (isinstance(fingerprint, str) and fingerprint.startswith(FINGERPRINT_PREFIX)):
                raise ValueError("script keys must be request fingerprints")
            checked[fingerprint] = _Outcomes(outcomes=tuple(outcomes)).outcomes
        return cls(checked)

    def get(self, fingerprint: str) -> tuple[FakeReply | FakeFault, ...] | None:
        return self._entries.get(fingerprint)


def request_fingerprint(request: ModelRequest) -> str:
    """Identity of the logical input: profile, messages, tools and parameters.

    IDs and the three hashes are excluded, so the same input matches whatever
    Attempt it belongs to.
    """
    data = request.model_dump(mode="json", include={"profile", "messages", "tools", "params"})
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return FINGERPRINT_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _SyntheticProviderError(Exception):
    """Stands in for a provider SDK's own exception type. Never leaves the adapter."""


class FakeModelProvider:
    """ModelPort implementation that replays a ``FakeScript``."""

    def __init__(self, *, profile: ModelProfile, script: FakeScript, price: FakePrice) -> None:
        if profile.provider is not ProviderKind.FAKE:
            raise ModelUnsupportedError("only FAKE profiles are supported in Phase 1")
        self._profile = profile
        self._script = script
        self._price = price
        self._cursor: dict[str, int] = {}
        self.calls = 0

    def remaining(self, request: ModelRequest) -> int:
        fingerprint = request_fingerprint(request)
        outcomes = self._script.get(fingerprint) or ()
        return len(outcomes) - self._cursor.get(fingerprint, 0)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if type(request) is not ModelRequest:
            raise ModelInvalidRequestError("expected a ModelRequest")
        if request.profile != self._profile.ref:
            raise ModelInvalidRequestError("request names another model profile")
        if request.params.max_output_tokens > self._profile.max_output:
            raise ModelInvalidRequestError("max_output_tokens exceeds the profile")
        fingerprint = request_fingerprint(request)
        outcomes = self._script.get(fingerprint)
        index = self._cursor.get(fingerprint, 0)
        if outcomes is None or index >= len(outcomes):
            raise FakeScriptMissError("no scripted outcome for this input")
        self._cursor[fingerprint] = index + 1
        self.calls += 1
        outcome = outcomes[index]
        if outcome.elapsed_ms > request.params.timeout_ms:
            raise ModelTimeoutError("synthetic timeout", usage=Usage.unknown(synthetic=True))
        if isinstance(outcome, FakeReply):
            return self._response(request, outcome)
        try:
            self._raise_fault(outcome)
        except _SyntheticProviderError:
            # Provider-specific errors are converted to the typed contract.
            raise ModelDefiniteFailureError("provider refused the request") from None
        # Every fault raises above; reaching here would be a bug, so fail as unknown.
        raise ModelOutcomeUnknownError("fault was not raised", usage=Usage.unknown(synthetic=True))

    def _raise_fault(self, fault: FakeFault) -> None:
        if fault.fault is FakeFaultKind.FAILED_BEFORE_SEND:
            raise ModelNotSentError("synthetic failure before sending")
        if fault.fault is FakeFaultKind.PROVIDER_REJECTED:
            raise _SyntheticProviderError("synthetic provider rejection")
        if fault.fault is FakeFaultKind.STREAM_INTERRUPTED:
            partial = Usage(
                status=UsageStatus.PARTIAL,
                input_tokens=fault.partial_input_tokens,
                output_tokens=None,
                total_tokens=None,
                cost=None,
                cost_status=CostStatus.UNKNOWN,
                synthetic=True,
            )
            raise ModelOutcomeUnknownError("synthetic stream interruption", usage=partial)
        if fault.fault is FakeFaultKind.OUTCOME_UNKNOWN:
            raise ModelOutcomeUnknownError(
                "synthetic lost reply", usage=Usage.unknown(synthetic=True)
            )
        # INVALID_RESPONSE: malformed provider data goes through real validation.
        malformed = {"content": [{"type": "text", "text": "x"}], "stop_reason": "TOOL_USE"}
        self._validate_or_unknown(malformed)

    def _response(self, request: ModelRequest, reply: FakeReply) -> ModelResponse:
        if reply.output_tokens > request.params.max_output_tokens:
            raise ModelResponseInvalidError(
                "reply exceeds max_output_tokens", usage=Usage.unknown(synthetic=True)
            )
        cost_numerator = (
            reply.input_tokens * self._price.input_micro_xtu_per_mtok
            + reply.output_tokens * self._price.output_micro_xtu_per_mtok
        )
        usage = {
            "status": UsageStatus.FINAL.value,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "total_tokens": reply.input_tokens + reply.output_tokens,
            "cost": {
                "currency": Currency.XTU.value,
                "micro_units": -(-cost_numerator // _PER_MILLION),
            },
            "cost_status": CostStatus.FINAL.value,
            "synthetic": True,
        }
        return self._validate_or_unknown(
            {
                "model_request_id": str(request.model_request_id),
                "content": [b.model_dump(mode="json") for b in reply.content],
                "stop_reason": reply.stop_reason.value,
                "usage": usage,
                "provider_request_id": f"fake-{self.calls}",
                "final_usage": True,
            }
        )

    @staticmethod
    def _validate_or_unknown(data: dict[str, Any]) -> ModelResponse:
        """Validate provider data strictly, as an adapter for a real API would."""
        try:
            return ModelResponse.model_validate_json(json.dumps(data))
        except ValidationError:
            raise ModelResponseInvalidError(
                "provider response failed validation", usage=Usage.unknown(synthetic=True)
            ) from None
