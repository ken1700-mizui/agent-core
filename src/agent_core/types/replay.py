"""Replay record types (WP-06; B1 6.2-6.3, B1.C10, B1.C24, A 1.1, E.Z14).

Record format version 2 (``format_version``; WP06-R01). Version 1 is retired:
its rendered blob was opaque bytes that could not be bound to the current
request, so a version 1 record is refused (``FORMAT_SUPERSEDED``) and never
reinterpreted.

- ``ReplayRecord``: one source Run's model calls, in order (``ReplayStep``).
  Bodies are not inside the record: each step references content-addressed
  blobs (``sha256:`` of the stored bytes) for the IR, the rendered context,
  the request record and, for a recorded response, the response.
- ``ReplayStep`` keeps the three hashes of B1 6.2. They are recomputed from
  the referenced blobs before replay; equal strings alone prove nothing.
- The rendered blob is the model-facing input in an explicit synthetic normal
  form: ``ReplayRenderedInput`` serialized with ``dump_json`` (keys sorted, no
  whitespace, UTF-8). The current request's messages (order, role, content),
  tools and generation parameters must equal it. The current context is not
  rebuilt (that is the Context Engine, WP-14); it is compared with what was
  stored.
- ``ReplayManifest`` is the trusted anchor: it pins each record's
  ``record_hash`` (domain-tagged SHA-256 of the canonical record). A record
  whose hashes were all recomputed consistently is still refused if it is not
  the pinned one. Phase 1 manifests are fixed synthetic fixtures; signatures
  and independent witnesses come later.

Hashes here are unkeyed SHA-256 over stored bytes, for synthetic data only
(``origin=SYNTHETIC``). Real data needs keyed HMACs (C2 6.2, WP-14). The
canonical JSON is the Phase 1 internal format (RFC 8785 conformance not
verified).
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel, ImmutableRecord
from agent_core.types.ids import AttemptId, ModelRequestId, ReplayRecordId, RunId, TenantId
from agent_core.types.model import (
    ContextHash,
    GenerationParams,
    Message,
    ModelCallOutcome,
    ProfileRef,
    RenderingHash,
    RequestHash,
    ToolSpec,
    Usage,
)
from agent_core.types.model_errors import ModelError, _no_tokens
from agent_core.types.serialization import dump_json
from agent_core.types.values import Count, Digest

REPLAY_FORMAT_VERSION = 2
SUPERSEDED_REPLAY_FORMAT_VERSIONS = frozenset({1})
REPLAY_RECORD_HASH_DOMAIN = b"agent-core/replay-record/v2\x00"


class ReplayOrigin(StrEnum):
    """A 1.1. Phase 1 accepts SYNTHETIC only; LIVE_APPROVED starts in Phase 3B."""

    SYNTHETIC = "SYNTHETIC"
    LIVE_APPROVED = "LIVE_APPROVED"


class ReplayReason(StrEnum):
    """Fixed reasons for REPLAY_UNAVAILABLE (B1.C24). Never a message."""

    RECORD_MISSING = "RECORD_MISSING"
    DATA_MISSING = "DATA_MISSING"
    CONTEXT_HASH_MISMATCH = "CONTEXT_HASH_MISMATCH"
    RENDERING_HASH_MISMATCH = "RENDERING_HASH_MISMATCH"
    REQUEST_HASH_MISMATCH = "REQUEST_HASH_MISMATCH"
    REQUEST_RECORD_INCONSISTENT = "REQUEST_RECORD_INCONSISTENT"
    RESPONSE_MISMATCH = "RESPONSE_MISMATCH"
    REQUEST_MISMATCH = "REQUEST_MISMATCH"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN_FORMAT = "UNKNOWN_FORMAT"
    ORIGIN_NOT_ALLOWED = "ORIGIN_NOT_ALLOWED"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    SOURCE_RUN_REUSED = "SOURCE_RUN_REUSED"
    ACCESS_DENIED = "ACCESS_DENIED"
    ACCESS_REVOKED = "ACCESS_REVOKED"
    DELETED = "DELETED"
    KEY_UNAVAILABLE = "KEY_UNAVAILABLE"
    FORMAT_SUPERSEDED = "FORMAT_SUPERSEDED"
    RENDERED_INPUT_INVALID = "RENDERED_INPUT_INVALID"
    INPUT_MISMATCH = "INPUT_MISMATCH"
    REQUEST_REUSED = "REQUEST_REUSED"
    ATTEMPT_REUSED = "ATTEMPT_REUSED"
    CONCURRENT_CALL = "CONCURRENT_CALL"
    STORE_FAILED = "STORE_FAILED"
    ACCESS_CHECK_FAILED = "ACCESS_CHECK_FAILED"


class ReplayUnavailableError(ModelError):
    """REPLAY_UNAVAILABLE with a fixed reason. Nothing is sent or filled in."""

    code: ClassVar[str] = "REPLAY_UNAVAILABLE"
    outcome: ClassVar[ModelCallOutcome] = ModelCallOutcome.NOT_SENT

    def __init__(self, reason: ReplayReason) -> None:
        super().__init__(
            f"replay unavailable: {reason.value}",
            usage=_no_tokens(cost_known_zero=True, synthetic=True),
        )
        self.reason = reason


class ReplayFailureKind(StrEnum):
    NOT_SENT = "NOT_SENT"
    FAILED_DEFINITE = "FAILED_DEFINITE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    TIMEOUT = "TIMEOUT"
    RESPONSE_INVALID = "RESPONSE_INVALID"


class RecordedResponse(FrozenModel):
    kind: Literal["response"]
    response_blob_ref: Digest


class RecordedFailure(FrozenModel):
    kind: Literal["failure"]
    failure: ReplayFailureKind
    # Usage the source call reported. Kept as recorded; never re-billed.
    source_usage: Usage


RecordedOutcome = Annotated[RecordedResponse | RecordedFailure, Field(discriminator="kind")]


class ReplayRenderedInput(FrozenModel):
    """The model-facing input as rendered for the source call, in the synthetic
    normal form (``dump_json`` of this model). Version 1."""

    rendered_input_version: Literal[1]
    messages: Annotated[tuple[Message, ...], Field(min_length=1)]
    tools: tuple[ToolSpec, ...]
    params: GenerationParams


class ReplayRequestRecord(FrozenModel):
    """The request record (B1 6.2 ``request_record_ref``): what was sent, without
    credentials, request IDs or times."""

    request_record_version: Literal[1]
    destination: Literal["synthetic"]
    profile: ProfileRef
    params: GenerationParams
    tools: tuple[ToolSpec, ...]
    rendering_hash: RenderingHash


class ReplayStep(FrozenModel):
    position: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    source_model_request_id: ModelRequestId
    source_attempt_id: AttemptId
    ir_blob_ref: Digest
    rendered_blob_ref: Digest
    request_record_ref: Digest
    context_hash: ContextHash
    rendering_hash: RenderingHash
    request_hash: RequestHash
    key_version: Count
    outcome: RecordedOutcome

    def blob_refs(self) -> tuple[str, ...]:
        refs = [self.ir_blob_ref, self.rendered_blob_ref, self.request_record_ref]
        if isinstance(self.outcome, RecordedResponse):
            refs.append(self.outcome.response_blob_ref)
        return tuple(refs)


class ReplayRecord(ImmutableRecord):
    format_version: Literal[2]
    record_id: ReplayRecordId
    origin: ReplayOrigin
    tenant_id: TenantId
    source_run_id: RunId
    steps: Annotated[tuple[ReplayStep, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if [s.position for s in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("steps are numbered 1..n in order")
        ids = [s.source_model_request_id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("each step has its own source request")
        attempts = [s.source_attempt_id for s in self.steps]
        if len(set(attempts)) != len(attempts):
            raise ValueError("each step has its own source attempt")
        return self


def replay_record_hash(record: ReplayRecord) -> str:
    return "sha256:" + hashlib.sha256(REPLAY_RECORD_HASH_DOMAIN + dump_json(record)).hexdigest()


class ReplayManifestEntry(FrozenModel):
    record_id: ReplayRecordId
    tenant_id: TenantId
    record_blob_ref: Digest
    record_hash: Digest


class ReplayManifest(FrozenModel):
    """Trusted, fixed pins of replay records (Phase 1: synthetic fixtures)."""

    entries: tuple[ReplayManifestEntry, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [e.record_id for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("one entry per record")
        return self

    def find(self, record_id: ReplayRecordId) -> ReplayManifestEntry | None:
        for entry in self.entries:
            if entry.record_id == record_id:
                return entry
        return None


class ReplayAccessDecision(FrozenModel):
    allowed: bool
    reason: ReplayReason | None

    @model_validator(mode="after")
    def _reason_when_denied(self) -> Self:
        if self.allowed != (self.reason is None):
            raise ValueError("a denial carries a reason; an allowance does not")
        return self
