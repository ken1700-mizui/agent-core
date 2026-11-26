"""Replay Provider (WP-06): a ModelPort that replays verified synthetic records.

Order of checks (B1 6.3, B1.C10, B1.C24, E.Z14):

At construction, for the record:
  1. The record is pinned in the trusted ``ReplayManifest`` for this tenant.
  2. Access is checked for the record blob before it is read.
  3. The record's format version is known (1); its bytes match the pinned
     content address and the pinned ``record_hash``; it parses strictly.
  4. Origin is SYNTHETIC (Phase 1), the tenant matches, and the replay Run is
     not the source Run.
For each call, against the next step (strict order):
  5. The request's three hashes name the next step (else OUT_OF_ORDER if they
     name another step, REQUEST_MISMATCH otherwise).
  6. Access is checked for every blob of the step before any is read.
  7. ``context_hash``, ``rendering_hash`` and ``request_hash`` are recomputed
     from the IR, the rendered context and the request record; the request
     record must name the verified rendering; the request's profile,
     parameters and tools must equal the request record.
  8. A recorded response must match its content address, parse strictly and
     answer the step's source request.
  9. Access is checked again before the result is returned (revocation).

Any failure raises ``ReplayUnavailableError`` with a fixed reason and stops
this replay for good. Nothing is filled in from the current memory, files,
another copy, another provider or a real model. The record and blobs are
never modified. Tool requests in replayed responses are only data.

Usage: the replay itself uses no tokens and costs a known zero; the source's
recorded usage is kept apart (``source_usage``) and is never billed again.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from agent_core.ports.replay import ReplayAccessPort, ReplayBlobStore
from agent_core.types.errors import ReplayBlobNotFoundError
from agent_core.types.ids import ModelRequestId, ReplayRecordId, RunId, TenantId
from agent_core.types.model import (
    CostStatus,
    ModelRequest,
    ModelResponse,
    Usage,
    UsageStatus,
    context_hash,
    rendering_hash,
    request_hash,
)
from agent_core.types.model_errors import (
    ModelDefiniteFailureError,
    ModelNotSentError,
    ModelOutcomeUnknownError,
    ModelResponseInvalidError,
    ModelTimeoutError,
)
from agent_core.types.replay import (
    RecordedFailure,
    RecordedResponse,
    ReplayAccessDecision,
    ReplayFailureKind,
    ReplayManifest,
    ReplayOrigin,
    ReplayReason,
    ReplayRecord,
    ReplayRequestRecord,
    ReplayStep,
    ReplayUnavailableError,
    replay_record_hash,
)
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.values import Budget, Currency, sha256_digest

_REPLAY_USAGE = Usage(
    status=UsageStatus.FINAL,
    input_tokens=0,
    output_tokens=0,
    total_tokens=0,
    cost=Budget(currency=Currency.XTU, micro_units=0),
    cost_status=CostStatus.FINAL,
    synthetic=True,
)


class DenyAllReplayAccess:
    """The default when no access verifier exists: every replay is refused."""

    def check(
        self, tenant_id: TenantId, refs: tuple[str, ...], key_version: int
    ) -> ReplayAccessDecision:
        return ReplayAccessDecision(allowed=False, reason=ReplayReason.ACCESS_DENIED)


class ReplayModelProvider:
    def __init__(
        self,
        *,
        manifest: ReplayManifest,
        record_id: ReplayRecordId,
        tenant_id: TenantId,
        replay_run_id: RunId,
        store: ReplayBlobStore,
        access: ReplayAccessPort,
    ) -> None:
        self._tenant = tenant_id
        self._store = store
        self._access = access
        self._stopped: ReplayReason | None = None
        self._cursor = 0
        self._sources: dict[ModelRequestId, ModelRequestId] = {}
        self._source_usage: dict[ModelRequestId, Usage] = {}
        self._record = self._load(manifest, record_id, replay_run_id)

    def source_of(self, model_request_id: ModelRequestId) -> ModelRequestId:
        """The source request a replayed request was mapped to."""
        return self._sources[model_request_id]

    def source_usage(self, model_request_id: ModelRequestId) -> Usage:
        """The usage recorded for the source call (unchanged, not billed again)."""
        return self._source_usage[model_request_id]

    # ------------------------------------------------------------ record

    def _fail(self, reason: ReplayReason) -> ReplayUnavailableError:
        self._stopped = reason
        return ReplayUnavailableError(reason)

    def _allowed(self, refs: tuple[str, ...], key_version: int, *, revoked: bool) -> None:
        decision = self._access.check(self._tenant, refs, key_version)
        if type(decision) is not ReplayAccessDecision or not decision.allowed:
            if revoked:
                raise self._fail(ReplayReason.ACCESS_REVOKED)
            reason = decision.reason if type(decision) is ReplayAccessDecision else None
            raise self._fail(reason or ReplayReason.ACCESS_DENIED)

    def _read(self, ref: str) -> bytes:
        try:
            data = self._store.get(self._tenant, ref)
        except ReplayBlobNotFoundError:
            raise self._fail(ReplayReason.DATA_MISSING) from None
        if type(data) is not bytes:
            raise self._fail(ReplayReason.DATA_MISSING)
        return data

    def _load(
        self, manifest: ReplayManifest, record_id: ReplayRecordId, replay_run_id: RunId
    ) -> ReplayRecord:
        entry = manifest.find(record_id)
        if entry is None:
            raise self._fail(ReplayReason.RECORD_MISSING)
        if entry.tenant_id != self._tenant:
            raise self._fail(ReplayReason.TENANT_MISMATCH)
        self._allowed((entry.record_blob_ref,), 0, revoked=False)
        data = self._read(entry.record_blob_ref)
        try:
            raw: Any = parse_strict_json(data)
        except StrictJsonError:
            raise self._fail(ReplayReason.MANIFEST_MISMATCH) from None
        if not isinstance(raw, dict) or raw.get("format_version") != 1:
            raise self._fail(ReplayReason.UNKNOWN_FORMAT)
        if sha256_digest(data) != entry.record_blob_ref:
            raise self._fail(ReplayReason.MANIFEST_MISMATCH)
        try:
            record = ReplayRecord.model_validate_json(data)
        except ValidationError:
            raise self._fail(ReplayReason.MANIFEST_MISMATCH) from None
        if replay_record_hash(record) != entry.record_hash or record.record_id != record_id:
            raise self._fail(ReplayReason.MANIFEST_MISMATCH)
        if record.tenant_id != self._tenant:
            raise self._fail(ReplayReason.TENANT_MISMATCH)
        if record.origin is not ReplayOrigin.SYNTHETIC:
            raise self._fail(ReplayReason.ORIGIN_NOT_ALLOWED)
        if record.source_run_id == replay_run_id:
            raise self._fail(ReplayReason.SOURCE_RUN_REUSED)
        return record

    # ------------------------------------------------------------ calls

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._stopped is not None:
            raise ReplayUnavailableError(self._stopped)
        if type(request) is not ModelRequest:
            raise self._fail(ReplayReason.REQUEST_MISMATCH)
        steps = self._record.steps
        if self._cursor >= len(steps):
            raise self._fail(ReplayReason.EXHAUSTED)
        step = steps[self._cursor]
        if not _names(step, request):
            if any(_names(other, request) for other in steps):
                raise self._fail(ReplayReason.OUT_OF_ORDER)
            raise self._fail(ReplayReason.REQUEST_MISMATCH)
        self._allowed(step.blob_refs(), step.key_version, revoked=False)
        self._verify_hashes(step, request)
        result = self._recorded_result(step, request)
        self._allowed(step.blob_refs(), step.key_version, revoked=True)
        self._cursor += 1
        self._sources[request.model_request_id] = step.source_model_request_id
        if isinstance(result, ModelResponse):
            return result
        raise result

    def _verify_hashes(self, step: ReplayStep, request: ModelRequest) -> None:
        if context_hash(self._read(step.ir_blob_ref)) != step.context_hash:
            raise self._fail(ReplayReason.CONTEXT_HASH_MISMATCH)
        verified_rendering = rendering_hash(self._read(step.rendered_blob_ref))
        if verified_rendering != step.rendering_hash:
            raise self._fail(ReplayReason.RENDERING_HASH_MISMATCH)
        record_bytes = self._read(step.request_record_ref)
        if request_hash(record_bytes) != step.request_hash:
            raise self._fail(ReplayReason.REQUEST_HASH_MISMATCH)
        try:
            parse_strict_json(record_bytes)
            record = ReplayRequestRecord.model_validate_json(record_bytes)
        except (StrictJsonError, ValidationError):
            raise self._fail(ReplayReason.REQUEST_RECORD_INCONSISTENT) from None
        if record.rendering_hash != verified_rendering:
            raise self._fail(ReplayReason.REQUEST_RECORD_INCONSISTENT)
        if (record.profile, record.params, record.tools) != (
            request.profile,
            request.params,
            request.tools,
        ):
            raise self._fail(ReplayReason.PROFILE_MISMATCH)

    def _recorded_result(
        self, step: ReplayStep, request: ModelRequest
    ) -> ModelResponse | Exception:
        outcome = step.outcome
        if isinstance(outcome, RecordedFailure):
            self._source_usage[request.model_request_id] = outcome.source_usage
            return _replayed_failure(outcome)
        if not isinstance(outcome, RecordedResponse):
            raise self._fail(ReplayReason.UNKNOWN_FORMAT)
        data = self._read(outcome.response_blob_ref)
        if sha256_digest(data) != outcome.response_blob_ref:
            raise self._fail(ReplayReason.RESPONSE_MISMATCH)
        try:
            parse_strict_json(data)
            recorded = ModelResponse.model_validate_json(data)
        except (StrictJsonError, ValidationError):
            raise self._fail(ReplayReason.RESPONSE_MISMATCH) from None
        if recorded.model_request_id != step.source_model_request_id:
            raise self._fail(ReplayReason.RESPONSE_MISMATCH)
        self._source_usage[request.model_request_id] = recorded.usage
        replayed = json.loads(recorded.model_dump_json())
        replayed["model_request_id"] = str(request.model_request_id)
        replayed["usage"] = json.loads(_REPLAY_USAGE.model_dump_json())
        replayed["final_usage"] = True
        return ModelResponse.model_validate_json(json.dumps(replayed))


def _names(step: ReplayStep, request: ModelRequest) -> bool:
    return (step.context_hash, step.rendering_hash, step.request_hash) == (
        request.context_hash,
        request.rendering_hash,
        request.request_hash,
    )


def _replayed_failure(outcome: RecordedFailure) -> Exception:
    kind = outcome.failure
    if kind is ReplayFailureKind.NOT_SENT:
        return ModelNotSentError("replayed: not sent")
    if kind is ReplayFailureKind.FAILED_DEFINITE:
        return ModelDefiniteFailureError("replayed: failed definite")
    if kind is ReplayFailureKind.TIMEOUT:
        return ModelTimeoutError("replayed: timeout", usage=_REPLAY_USAGE)
    if kind is ReplayFailureKind.RESPONSE_INVALID:
        return ModelResponseInvalidError("replayed: invalid response", usage=_REPLAY_USAGE)
    return ModelOutcomeUnknownError("replayed: outcome unknown", usage=_REPLAY_USAGE)
