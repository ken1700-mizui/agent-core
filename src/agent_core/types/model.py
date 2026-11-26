"""Common model types (WP-05; E 7.4 and 8, B1 2.4 and 6.2, ADR-0003).

Provider-independent ``Message``, ``ContentBlock``, ``ModelRequest``,
``ModelResponse``, ``Usage`` and ``ModelProfile``. Phase 1 supports only text,
tool requests and tool results; other content types are rejected.

- The three hashes of B1 6.2 are distinct types with distinct prefixes, so one
  cannot be passed where another is expected. Computing them from a context
  is WP-14; here they are only typed values.
- Usage is integer only. Unknown usage is ``UNKNOWN`` with no numbers, never a
  zero. Fake usage is marked ``synthetic``: it is not a real measurement.
- A model response is data. A tool request in it grants nothing: executing a
  tool needs its own Attempt, authorization and permit (E 7.4, C1).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_core.types.base import FrozenModel, ImmutableRecord
from agent_core.types.ids import AttemptId, ModelRequestId
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.values import Budget, Count, Digest, Identifier, SemVer

MAX_TEXT_CHARS = 1_000_000
MAX_ARGUMENT_CHARS = 100_000
MAX_TOKENS = 10_000_000

ContextHash = Annotated[
    str, StringConstraints(strict=True, pattern=r"^ctxhash-v1:sha256:[0-9a-f]{64}$")
]
RenderingHash = Annotated[
    str, StringConstraints(strict=True, pattern=r"^renderhash-v1:sha256:[0-9a-f]{64}$")
]
RequestHash = Annotated[
    str, StringConstraints(strict=True, pattern=r"^reqhash-v1:sha256:[0-9a-f]{64}$")
]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=MAX_TOKENS)]
ToolUseId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
ModelId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
ProviderRequestId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]


def _prefixed(prefix: str, data: bytes) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(data).hexdigest()}"


def context_hash(normalized_ir: bytes) -> str:
    """``ctxhash-v1`` over the normalized IR bytes (the normalization is WP-14)."""
    return _prefixed("ctxhash-v1", normalized_ir)


def rendering_hash(normalized_rendering: bytes) -> str:
    """``renderhash-v1`` over the normalized rendered context (WP-14)."""
    return _prefixed("renderhash-v1", normalized_rendering)


def request_hash(request_record: bytes) -> str:
    """``reqhash-v1`` over the normalized request record (WP-14)."""
    return _prefixed("reqhash-v1", request_record)


# ---------------------------------------------------------------- content


class Role(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class TextBlock(FrozenModel):
    type: Literal["text"]
    text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_TEXT_CHARS)]

    @field_validator("text")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("text must not contain NUL")
        return value


class ToolUseBlock(FrozenModel):
    """A tool request from the model. Data only: it is never executed from here."""

    type: Literal["tool_use"]
    id: ToolUseId
    name: Identifier
    # Canonical JSON object text (keys sorted, compact): one spelling per value.
    arguments: Annotated[str, StringConstraints(strict=True, max_length=MAX_ARGUMENT_CHARS)]

    @field_validator("arguments")
    @classmethod
    def _canonical_object(cls, value: str) -> str:
        # The same strict rules as load_json (WP02-R01): duplicate keys,
        # non-finite numbers, invalid strings and excessive nesting are rejected.
        # The error names only the rule, never the input.
        try:
            parsed = parse_strict_json(value)
        except StrictJsonError as exc:
            raise ValueError(f"tool arguments rejected: {exc.issue_type}") from None
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        canonical = json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        if canonical != value:
            raise ValueError("tool arguments must be canonical JSON")
        return value


class ToolResultBlock(FrozenModel):
    type: Literal["tool_result"]
    tool_use_id: ToolUseId
    content: Annotated[tuple[TextBlock, ...], Field(min_length=1)]
    is_error: bool


ContentBlock = Annotated[TextBlock | ToolUseBlock | ToolResultBlock, Field(discriminator="type")]
# A response carries text and tool requests only (tool results come from the runtime).
ResponseBlock = Annotated[TextBlock | ToolUseBlock, Field(discriminator="type")]


class Message(FrozenModel):
    role: Role
    content: Annotated[tuple[ContentBlock, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _blocks_match_role(self) -> Self:
        for block in self.content:
            if isinstance(block, ToolUseBlock) and self.role is not Role.ASSISTANT:
                raise ValueError("only the assistant requests tools")
            if isinstance(block, ToolResultBlock) and self.role is not Role.USER:
                raise ValueError("tool results come in user messages")
        return self


# ---------------------------------------------------------------- profile


class ProviderKind(StrEnum):
    """Phase 1 supports only FAKE. Real providers need owner approval (ADR-0003)."""

    FAKE = "FAKE"
    # A placeholder used to test that anything but FAKE is rejected.
    UNSUPPORTED = "UNSUPPORTED"


class Deployment(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class WindowCounts(StrEnum):
    INPUT_ONLY = "INPUT_ONLY"
    INPUT_PLUS_OUTPUT = "INPUT_PLUS_OUTPUT"


class ReasoningMode(StrEnum):
    NONE = "NONE"
    INSIDE_OUTPUT = "INSIDE_OUTPUT"
    SEPARATE = "SEPARATE"


class TokenizerMethod(StrEnum):
    EXACT_LOCAL = "EXACT_LOCAL"
    EXACT_REMOTE = "EXACT_REMOTE"
    ESTIMATE = "ESTIMATE"


class TokenAccounting(FrozenModel):
    window_counts: WindowCounts
    reasoning_mode: ReasoningMode


class CacheCapability(FrozenModel):
    prefix_cache: bool
    min_cacheable_tokens: TokenCount
    max_breakpoints: Count


class ModelProfile(FrozenModel):
    """B1 2.4 (subset used in Phase 1). Synthetic values only until Phase 3B."""

    provider: ProviderKind
    model_id: ModelId
    deployment: Deployment
    context_window: Annotated[int, Field(strict=True, ge=1, le=MAX_TOKENS)]
    max_output: Annotated[int, Field(strict=True, ge=1, le=MAX_TOKENS)]
    token_accounting: TokenAccounting
    tokenizer_method: TokenizerMethod
    cache_capability: CacheCapability
    billing_scheme_ref: Digest
    profile_version: SemVer

    @model_validator(mode="after")
    def _output_fits(self) -> Self:
        if self.max_output > self.context_window:
            raise ValueError("max_output cannot exceed context_window")
        return self

    @property
    def ref(self) -> ProfileRef:
        return ProfileRef(model_id=self.model_id, profile_version=self.profile_version)


class ProfileRef(FrozenModel):
    """``profile@version`` in E 8."""

    model_id: ModelId
    profile_version: SemVer


# ---------------------------------------------------------------- request


class ToolSpec(FrozenModel):
    name: Identifier
    input_schema_ref: Digest


class GenerationParams(FrozenModel):
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=MAX_TOKENS)]
    # Temperature in thousandths (0..2000): an integer, so there is no float.
    temperature_milli: Annotated[int, Field(strict=True, ge=0, le=2_000)]
    timeout_ms: Annotated[int, Field(strict=True, ge=1, le=600_000)]


class ModelRequest(ImmutableRecord):
    """E 8. One request belongs to one Attempt; a new call needs a new Attempt."""

    model_request_id: ModelRequestId
    attempt_id: AttemptId
    profile: ProfileRef
    messages: Annotated[tuple[Message, ...], Field(min_length=1)]
    tools: tuple[ToolSpec, ...]
    params: GenerationParams
    context_hash: ContextHash
    rendering_hash: RenderingHash
    request_hash: RequestHash

    @model_validator(mode="after")
    def _conversation(self) -> Self:
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("tool names must be unique")
        asked: set[str] = set()
        answered: set[str] = set()
        for message in self.messages:
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    if block.id in asked:
                        raise ValueError("tool_use ids must be unique")
                    asked.add(block.id)
                elif isinstance(block, ToolResultBlock):
                    if block.tool_use_id not in asked:
                        raise ValueError("a tool result must answer an earlier tool_use")
                    if block.tool_use_id in answered:
                        raise ValueError("a tool_use is answered at most once")
                    answered.add(block.tool_use_id)
        return self


# ---------------------------------------------------------------- usage and response


class UsageStatus(StrEnum):
    FINAL = "FINAL"  # complete and final (E 4.2 condition 1)
    PARTIAL = "PARTIAL"  # some numbers known, not final
    UNKNOWN = "UNKNOWN"  # nothing known; never treated as zero


class CostStatus(StrEnum):
    """Status of the bill, independent of the token usage (WP05-R01)."""

    FINAL = "FINAL"
    UNKNOWN = "UNKNOWN"


class Usage(FrozenModel):
    """Token usage and cost, each with its own status.

    Zero tokens do not imply a known zero cost, and an unknown token usage does
    not exclude a final bill reported separately by a provider.
    """

    status: UsageStatus
    input_tokens: TokenCount | None
    output_tokens: TokenCount | None
    total_tokens: TokenCount | None
    # Integer minimum units; Phase 1 allows only XTU (Currency).
    cost: Budget | None
    cost_status: CostStatus
    # True for Fake Provider values: synthetic test data, not a real measurement.
    synthetic: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        numbers = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.status is UsageStatus.FINAL:
            if any(n is None for n in numbers):
                raise ValueError("final usage needs input, output and total tokens")
            if self.total_tokens != (self.input_tokens or 0) + (self.output_tokens or 0):
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        elif self.status is UsageStatus.UNKNOWN:
            if any(n is not None for n in numbers):
                raise ValueError("unknown usage carries no token numbers (it is not zero)")
        elif self.total_tokens is not None:
            raise ValueError("partial usage has no total")
        if (self.cost is None) != (self.cost_status is CostStatus.UNKNOWN):
            raise ValueError("a cost is present exactly when the cost status is FINAL")
        return self

    @classmethod
    def unknown(cls, *, synthetic: bool) -> Usage:
        return cls(
            status=UsageStatus.UNKNOWN,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            cost=None,
            cost_status=CostStatus.UNKNOWN,
            synthetic=synthetic,
        )


class StopReason(StrEnum):
    END_TURN = "END_TURN"
    MAX_TOKENS = "MAX_TOKENS"
    STOP_SEQUENCE = "STOP_SEQUENCE"
    TOOL_USE = "TOOL_USE"


class ModelResponse(ImmutableRecord):
    """E 8. Data only; nothing here is executed or authorized."""

    model_request_id: ModelRequestId
    content: Annotated[tuple[ResponseBlock, ...], Field(min_length=1)]
    stop_reason: StopReason
    usage: Usage
    provider_request_id: ProviderRequestId | None
    final_usage: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        tool_ids = [b.id for b in self.content if isinstance(b, ToolUseBlock)]
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("tool_use ids must be unique within a response")
        uses_tool = bool(tool_ids)
        if uses_tool != (self.stop_reason is StopReason.TOOL_USE):
            raise ValueError("stop_reason TOOL_USE exactly when a tool is requested")
        if self.final_usage != (self.usage.status is UsageStatus.FINAL):
            raise ValueError("final_usage must match the usage status")
        return self


class ModelCallOutcome(StrEnum):
    """What a failed call means for the request (E 3.1)."""

    NOT_SENT = "NOT_SENT"  # nothing left this process
    FAILED_DEFINITE = "FAILED_DEFINITE"  # certainly not processed by the provider
    UNKNOWN = "UNKNOWN"  # may have been processed (and billed)
