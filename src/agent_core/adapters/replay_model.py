"""Replay Provider (WP-06, WP06-R01..R04): a ModelPort that replays verified
synthetic records.

Order of checks (B1 6.3, B1.C10, B1.C24, E.Z14):

At construction, for the record:
  1. The record is pinned in the trusted ``ReplayManifest`` for this tenant.
  2. Access is checked for the record blob before it is read.
  3. The record's format version is current (2; version 1 is superseded and
     refused); its bytes match the pinned content address and the pinned
     ``record_hash``; it parses strictly.
  4. Origin is SYNTHETIC (Phase 1), the tenant matches, and the replay Run is
     not the source Run.
For each call, against the next step (strict order, one call at a time):
  5. The request's ID and Attempt were not used by an earlier replayed call.
  6. The request's three hashes name the next step (else OUT_OF_ORDER if they
     name another step, REQUEST_MISMATCH otherwise).
  7. Access is checked for the record and every blob of the step before any is
     read, and again for the record and each blob right before it is read.
  8. ``context_hash``, ``rendering_hash`` and ``request_hash`` are recomputed
     from the IR, the rendered input and the request record. The rendered input
     must be the explicit synthetic normal form, and the current request's
     messages, tools and parameters must equal it (WP06-R01). The request
     record must name the verified rendering, and the request's profile,
     parameters and tools must equal it.
  9. A recorded response must match its content address, parse strictly and
     answer the step's source request.
 10. Access is checked a last time. Only then is the result published (the
     source mapping and source usage) and returned (WP06-R02).

``source_of`` and ``source_usage`` check current access each time; a denial
stops the replay and discards everything published.

Any failure raises ``ReplayUnavailableError`` with a fixed reason and stops
this replay for good. Unexpected errors from the store or the access port stop
it too (``STORE_FAILED``, ``ACCESS_CHECK_FAILED``); their text is never kept.
Nothing is filled in from the current memory, files, another copy, another
provider or a real model. The record and blobs are never modified. Tool
requests in replayed responses are only data.

Usage (WP06-R04): the replay itself sends nothing, uses no tokens and costs a
known zero, for responses and failures alike. The source's recorded usage is
kept apart (``source_usage``, unchanged; unknown stays unknown).

Not claimed: atomicity with a full revocation gate (WP-09 and later), and
recall of data already returned to a caller before a revocation.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from pydantic import ValidationError

from agent_core.ports.replay import ReplayAccessPort, ReplayBlobStore
from agent_core.types.errors import ReplayBlobNotFoundError
from agent_core.types.ids import AttemptId, ModelRequestId, ReplayRecordId, RunId, TenantId
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
    ModelError,
    ModelNotSentError,
    ModelOutcomeUnknownError,
    ModelResponseInvalidError,
    ModelTimeoutError,
)
from agent_core.types.replay import (
    REPLAY_FORMAT_VERSION,
    SUPERSEDED_REPLAY_FORMAT_VERSIONS,
    RecordedFailure,
    RecordedResponse,
    ReplayAccessDecision,
    ReplayFailureKind,
    ReplayManifest,
    ReplayOrigin,
    ReplayReason,
    ReplayRecord,
    ReplayRenderedInput,
    ReplayRequestRecord,
    ReplayStep,
    ReplayUnavailableError,
    replay_record_hash,
)
from agent_core.types.serialization import StrictJsonError, dump_json, parse_strict_json
from agent_core.types.values import Budget, Currency, sha256_digest

REPLAY_USAGE = Usage(
    status=UsageStatus.FINAL,
    input_tokens=0,
    output_tokens=0,
    total_tokens=0,
    cost=Budget(currency=Currency.XTU, micro_units=0),
    cost_status=CostStatus.FINAL,
    synthetic=True,
)

_FAILURES: dict[ReplayFailureKind, type[ModelError]] = {
    ReplayFailureKind.NOT_SENT: ModelNotSentError,
    ReplayFailureKind.FAILED_DEFINITE: ModelDefiniteFailureError,
    ReplayFailureKind.OUTCOME_UNKNOWN: ModelOutcomeUnknownError,
    ReplayFailureKind.TIMEOUT: ModelTimeoutError,
    ReplayFailureKind.RESPONSE_INVALID: ModelResponseInvalidError,
}


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
        self._lock = threading.Lock()
        self._stopped: ReplayReason | None = None
        self._cursor = 0
        self._record_ref = ""
        # Published only after the final access check of a call (WP06-R02).
        self._published: dict[ModelRequestId, tuple[ReplayStep, Usage]] = {}
        self._used_requests: set[ModelRequestId] = set()
        self._used_attempts: set[AttemptId] = set()
        self._record = self._load(manifest, record_id, replay_run_id)

    # ------------------------------------------------------------ accessors

    def source_of(self, model_request_id: ModelRequestId) -> ModelRequestId:
        """The source request a replayed request was mapped to (current access required)."""
        return self._disclose(model_request_id)[0].source_model_request_id

    def source_usage(self, model_request_id: ModelRequestId) -> Usage:
        """The usage recorded for the source call (unchanged, not billed again;
        current access required)."""
        return self._disclose(model_request_id)[1]

    def _disclose(self, model_request_id: ModelRequestId) -> tuple[ReplayStep, Usage]:
        if not self._lock.acquire(blocking=False):
            raise self._fail(ReplayReason.CONCURRENT_CALL)
        try:
            self._raise_if_stopped()
            entry = self._published.get(model_request_id)
            if entry is None:
                raise self._fail(ReplayReason.REQUEST_MISMATCH)
            step = entry[0]
            self._allowed((self._record_ref, *step.blob_refs()), step.key_version, revoked=True)
            return entry
        finally:
            self._lock.release()

    # ------------------------------------------------------------ failure handling

    def _fail(self, reason: ReplayReason) -> ReplayUnavailableError:
        if self._stopped is None:
            self._stopped = reason
        self._published.clear()
        return ReplayUnavailableError(self._stopped)

    def _raise_if_stopped(self) -> None:
        if self._stopped is not None:
            self._published.clear()
            raise ReplayUnavailableError(self._stopped)

    def _allowed(self, refs: tuple[str, ...], key_version: int, *, revoked: bool) -> None:
        try:
            decision = self._access.check(self._tenant, refs, key_version)
        except Exception:  # noqa: BLE001 (any port failure stops the replay; its text is dropped)
            raise self._fail(ReplayReason.ACCESS_CHECK_FAILED) from None
        if type(decision) is ReplayAccessDecision and decision.allowed:
            return
        if revoked:
            raise self._fail(ReplayReason.ACCESS_REVOKED)
        reason = decision.reason if type(decision) is ReplayAccessDecision else None
        raise self._fail(reason or ReplayReason.ACCESS_DENIED)

    def _read(self, ref: str, key_version: int, *, checked: tuple[str, ...]) -> bytes:
        self._allowed((*checked, ref), key_version, revoked=bool(checked))
        try:
            data = self._store.get(self._tenant, ref)
        except ReplayBlobNotFoundError:
            raise self._fail(ReplayReason.DATA_MISSING) from None
        except Exception:  # noqa: BLE001 (any port failure stops the replay; its text is dropped)
            raise self._fail(ReplayReason.STORE_FAILED) from None
        if type(data) is not bytes:
            raise self._fail(ReplayReason.DATA_MISSING)
        return data

    # ------------------------------------------------------------ record

    def _load(
        self, manifest: ReplayManifest, record_id: ReplayRecordId, replay_run_id: RunId
    ) -> ReplayRecord:
        entry = manifest.find(record_id)
        if entry is None:
            raise self._fail(ReplayReason.RECORD_MISSING)
        if entry.tenant_id != self._tenant:
            raise self._fail(ReplayReason.TENANT_MISMATCH)
        data = self._read(entry.record_blob_ref, 0, checked=())
        try:
            raw: Any = parse_strict_json(data)
        except StrictJsonError:
            raise self._fail(ReplayReason.MANIFEST_MISMATCH) from None
        version = raw.get("format_version") if isinstance(raw, dict) else None
        if type(version) is int and version in SUPERSEDED_REPLAY_FORMAT_VERSIONS:
            raise self._fail(ReplayReason.FORMAT_SUPERSEDED)
        if type(version) is not int or version != REPLAY_FORMAT_VERSION:
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
        self._record_ref = entry.record_blob_ref
        return record

    # ------------------------------------------------------------ calls

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._lock.acquire(blocking=False):
            raise self._fail(ReplayReason.CONCURRENT_CALL)
        try:
            return self._complete(request)
        finally:
            self._lock.release()

    def _complete(self, request: ModelRequest) -> ModelResponse:
        self._raise_if_stopped()
        if type(request) is not ModelRequest:
            raise self._fail(ReplayReason.REQUEST_MISMATCH)
        if request.model_request_id in self._used_requests:
            raise self._fail(ReplayReason.REQUEST_REUSED)
        if request.attempt_id in self._used_attempts:
            raise self._fail(ReplayReason.ATTEMPT_REUSED)
        steps = self._record.steps
        if self._cursor >= len(steps):
            raise self._fail(ReplayReason.EXHAUSTED)
        step = steps[self._cursor]
        if not _names(step, request):
            if any(_names(other, request) for other in steps):
                raise self._fail(ReplayReason.OUT_OF_ORDER)
            raise self._fail(ReplayReason.REQUEST_MISMATCH)
        refs = (self._record_ref, *step.blob_refs())
        self._allowed(refs, step.key_version, revoked=False)
        self._verify_input(step, request)
        result, source_usage = self._recorded_result(step, request)
        self._allowed(refs, step.key_version, revoked=True)
        # A concurrent or reentrant caller may have stopped the replay meanwhile.
        self._raise_if_stopped()
        self._cursor += 1
        self._used_requests.add(request.model_request_id)
        self._used_attempts.add(request.attempt_id)
        self._published[request.model_request_id] = (step, source_usage)
        if isinstance(result, ModelResponse):
            return result
        raise result

    def _blob(self, step: ReplayStep, ref: str) -> bytes:
        return self._read(ref, step.key_version, checked=(self._record_ref,))

    def _verify_input(self, step: ReplayStep, request: ModelRequest) -> None:
        if context_hash(self._blob(step, step.ir_blob_ref)) != step.context_hash:
            raise self._fail(ReplayReason.CONTEXT_HASH_MISMATCH)
        rendered = self._blob(step, step.rendered_blob_ref)
        verified_rendering = rendering_hash(rendered)
        if verified_rendering != step.rendering_hash:
            raise self._fail(ReplayReason.RENDERING_HASH_MISMATCH)
        record_bytes = self._blob(step, step.request_record_ref)
        if request_hash(record_bytes) != step.request_hash:
            raise self._fail(ReplayReason.REQUEST_HASH_MISMATCH)
        # WP06-R01: the current request must be exactly the stored model input.
        try:
            parse_strict_json(rendered)
            stored = ReplayRenderedInput.model_validate_json(rendered)
        except (StrictJsonError, ValidationError):
            raise self._fail(ReplayReason.RENDERED_INPUT_INVALID) from None
        if dump_json(stored) != rendered:
            raise self._fail(ReplayReason.RENDERED_INPUT_INVALID)
        if (stored.messages, stored.tools, stored.params) != (
            request.messages,
            request.tools,
            request.params,
        ):
            raise self._fail(ReplayReason.INPUT_MISMATCH)
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
    ) -> tuple[ModelResponse | ModelError, Usage]:
        outcome = step.outcome
        if isinstance(outcome, RecordedFailure):
            error = _FAILURES[outcome.failure].replayed_from_record(
                f"replayed: {outcome.failure.value}",
                replay_usage=REPLAY_USAGE,
                source_usage=outcome.source_usage,
            )
            return error, outcome.source_usage
        if not isinstance(outcome, RecordedResponse):
            raise self._fail(ReplayReason.UNKNOWN_FORMAT)
        data = self._blob(step, outcome.response_blob_ref)
        if sha256_digest(data) != outcome.response_blob_ref:
            raise self._fail(ReplayReason.RESPONSE_MISMATCH)
        try:
            parse_strict_json(data)
            recorded = ModelResponse.model_validate_json(data)
        except (StrictJsonError, ValidationError):
            raise self._fail(ReplayReason.RESPONSE_MISMATCH) from None
        if recorded.model_request_id != step.source_model_request_id:
            raise self._fail(ReplayReason.RESPONSE_MISMATCH)
        replayed = json.loads(recorded.model_dump_json())
        replayed["model_request_id"] = str(request.model_request_id)
        replayed["usage"] = json.loads(REPLAY_USAGE.model_dump_json())
        replayed["final_usage"] = True
        return ModelResponse.model_validate_json(json.dumps(replayed)), recorded.usage


def _names(step: ReplayStep, request: ModelRequest) -> bool:
    return (step.context_hash, step.rendering_hash, step.request_hash) == (
        request.context_hash,
        request.rendering_hash,
        request.request_hash,
    )
