"""Synthetic model requests, responses and profiles for WP-05 tests."""

from __future__ import annotations

from typing import Any

from agent_core.types.ids import AttemptId, ModelRequestId
from agent_core.types.model import (
    CacheCapability,
    Deployment,
    GenerationParams,
    Message,
    ModelProfile,
    ModelRequest,
    ProfileRef,
    ProviderKind,
    ReasoningMode,
    Role,
    TextBlock,
    TokenAccounting,
    TokenizerMethod,
    ToolSpec,
    WindowCounts,
    context_hash,
    rendering_hash,
    request_hash,
)
from support import builders as b


def profile(**overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "provider": ProviderKind.FAKE,
        "model_id": "scripted-model-small",
        "deployment": Deployment.LOCAL,
        "context_window": 8_000,
        "max_output": 1_000,
        "token_accounting": TokenAccounting(
            window_counts=WindowCounts.INPUT_PLUS_OUTPUT, reasoning_mode=ReasoningMode.NONE
        ),
        "tokenizer_method": TokenizerMethod.ESTIMATE,
        "cache_capability": CacheCapability(
            prefix_cache=False, min_cacheable_tokens=0, max_breakpoints=0
        ),
        "billing_scheme_ref": b.digest("test-virtual-v1"),
        "profile_version": "0.1.0",
    }
    values.update(overrides)
    return ModelProfile(**values)


def user(text: str) -> Message:
    return Message(role=Role.USER, content=(TextBlock(type="text", text=text),))


def request(*messages: Message, **overrides: Any) -> ModelRequest:
    msgs = messages or (user("synthetic question"),)
    values: dict[str, Any] = {
        "model_request_id": b.new_id(ModelRequestId),
        "attempt_id": b.new_id(AttemptId),
        "profile": ProfileRef(model_id="scripted-model-small", profile_version="0.1.0"),
        "messages": msgs,
        "tools": (ToolSpec(name="read_file", input_schema_ref=b.digest("schema")),),
        "params": GenerationParams(max_output_tokens=200, temperature_milli=0, timeout_ms=30_000),
        "context_hash": context_hash(b"synthetic-ir"),
        "rendering_hash": rendering_hash(b"synthetic-rendering"),
        "request_hash": request_hash(b"synthetic-request"),
    }
    values.update(overrides)
    return ModelRequest(**values)
