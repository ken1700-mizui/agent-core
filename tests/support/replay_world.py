"""Synthetic replay records, blob store and access control for WP-06 tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_core.types.errors import ReplayBlobNotFoundError
from agent_core.types.ids import AttemptId, ModelRequestId, ReplayRecordId, RunId, TenantId
from agent_core.types.model import (
    CostStatus,
    ModelRequest,
    StopReason,
    Usage,
    UsageStatus,
    context_hash,
    rendering_hash,
    request_hash,
)
from agent_core.types.replay import (
    RecordedFailure,
    RecordedResponse,
    ReplayAccessDecision,
    ReplayFailureKind,
    ReplayManifest,
    ReplayManifestEntry,
    ReplayOrigin,
    ReplayReason,
    ReplayRecord,
    ReplayRenderedInput,
    ReplayRequestRecord,
    ReplayStep,
    replay_record_hash,
)
from agent_core.types.serialization import dump_json
from agent_core.types.values import Budget, Currency, sha256_digest
from support import builders as b
from support import model_world as m


class MemoryBlobStore:
    """Tenant-scoped content-addressed blobs (synthetic)."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.reads: list[str] = []
        self.events = events if events is not None else []
        self.fail_with: BaseException | None = None

    def put(self, tenant_id: TenantId, data: bytes) -> str:
        ref = sha256_digest(data)
        self.blobs[(str(tenant_id), ref)] = data
        return ref

    def get(self, tenant_id: TenantId, ref: str) -> bytes:
        self.reads.append(ref)
        self.events.append("read")
        if self.fail_with is not None:
            raise self.fail_with
        try:
            return self.blobs[(str(tenant_id), ref)]
        except KeyError:
            raise ReplayBlobNotFoundError("blob not found") from None


@dataclass
class ScriptedAccess:
    """Synthetic access control: deletions, missing keys, revocation after N checks."""

    deleted: set[str] = field(default_factory=set)
    missing_key_versions: set[int] = field(default_factory=set)
    deny_all: bool = False
    revoke_after_checks: int | None = None
    checks: int = 0
    order: list[str] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    events: list[str] | None = None
    hook: Callable[[], None] | None = None
    fail_with: BaseException | None = None

    def check(
        self, tenant_id: TenantId, refs: tuple[str, ...], key_version: int
    ) -> ReplayAccessDecision:
        self.checks += 1
        self.order.append("check")
        self.calls.append(refs)
        if self.events is not None:
            self.events.append("check")
        if self.hook is not None:
            self.hook()
        if self.fail_with is not None:
            raise self.fail_with
        if self.deny_all:
            return ReplayAccessDecision(allowed=False, reason=ReplayReason.ACCESS_DENIED)
        if self.revoke_after_checks is not None and self.checks > self.revoke_after_checks:
            return ReplayAccessDecision(allowed=False, reason=ReplayReason.ACCESS_DENIED)
        if any(r in self.deleted for r in refs):
            return ReplayAccessDecision(allowed=False, reason=ReplayReason.DELETED)
        if key_version in self.missing_key_versions:
            return ReplayAccessDecision(allowed=False, reason=ReplayReason.KEY_UNAVAILABLE)
        return ReplayAccessDecision(allowed=True, reason=None)


SOURCE_USAGE = Usage(
    status=UsageStatus.FINAL,
    input_tokens=100,
    output_tokens=20,
    total_tokens=120,
    cost=Budget(currency=Currency.XTU, micro_units=180),
    cost_status=CostStatus.FINAL,
    synthetic=True,
)


@dataclass
class World:
    tenant: TenantId
    store: MemoryBlobStore
    record: ReplayRecord
    manifest: ReplayManifest
    requests: list[ModelRequest]
    record_ref: str


def _step(
    store: MemoryBlobStore,
    tenant: TenantId,
    position: int,
    text: str,
    outcome: Any,
    source_usage: Usage,
) -> tuple[ReplayStep, ModelRequest]:
    ir = json.dumps({"ir": text, "position": position}, sort_keys=True).encode()
    template = m.request(m.user(text))
    rendered = dump_json(
        ReplayRenderedInput(
            rendered_input_version=1,
            messages=template.messages,
            tools=template.tools,
            params=template.params,
        )
    )
    request_record = ReplayRequestRecord(
        request_record_version=1,
        destination="synthetic",
        profile=template.profile,
        params=template.params,
        tools=template.tools,
        rendering_hash=rendering_hash(rendered),
    )
    request_bytes = dump_json(request_record)
    source_id = b.new_id(ModelRequestId)
    if outcome == "response":
        response = {
            "model_request_id": str(source_id),
            "content": [{"type": "text", "text": f"recorded answer {position}"}],
            "stop_reason": StopReason.END_TURN.value,
            "usage": json.loads(dump_json(source_usage)),
            "provider_request_id": f"fake-{position}",
            "final_usage": True,
        }
        response_bytes = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
        recorded: Any = RecordedResponse(
            kind="response", response_blob_ref=store.put(tenant, response_bytes)
        )
    else:
        recorded = RecordedFailure(kind="failure", failure=outcome, source_usage=source_usage)
    step = ReplayStep(
        position=position,
        source_model_request_id=source_id,
        source_attempt_id=b.new_id(AttemptId),
        ir_blob_ref=store.put(tenant, ir),
        rendered_blob_ref=store.put(tenant, rendered),
        request_record_ref=store.put(tenant, request_bytes),
        context_hash=context_hash(ir),
        rendering_hash=rendering_hash(rendered),
        request_hash=request_hash(request_bytes),
        key_version=0,
        outcome=recorded,
    )
    current = m.request(
        m.user(text),
        context_hash=step.context_hash,
        rendering_hash=step.rendering_hash,
        request_hash=step.request_hash,
    )
    return step, current


def world(
    *outcomes: Any,
    origin: ReplayOrigin = ReplayOrigin.SYNTHETIC,
    tenant: TenantId | None = None,
    source_usage: Usage = SOURCE_USAGE,
) -> World:
    tenant_id = tenant if tenant is not None else b.TENANT
    store = MemoryBlobStore()
    steps, requests = [], []
    for position, outcome in enumerate(outcomes or ("response",), start=1):
        step, current = _step(
            store, tenant_id, position, f"synthetic input {position}", outcome, source_usage
        )
        steps.append(step)
        requests.append(current)
    record = ReplayRecord(
        format_version=2,
        record_id=b.new_id(ReplayRecordId),
        origin=origin,
        tenant_id=tenant_id,
        source_run_id=b.new_id(RunId),
        steps=tuple(steps),
    )
    record_ref = store.put(tenant_id, dump_json(record))
    manifest = ReplayManifest(
        entries=(
            ReplayManifestEntry(
                record_id=record.record_id,
                tenant_id=tenant_id,
                record_blob_ref=record_ref,
                record_hash=replay_record_hash(record),
            ),
        )
    )
    return World(tenant_id, store, record, manifest, requests, record_ref)


def replace_blob(w: World, ref: str, data: bytes) -> None:
    """Overwrite a stored blob in place (simulated tampering)."""
    w.store.blobs[(str(w.tenant), ref)] = data


def repin(w: World, record: ReplayRecord) -> World:
    """Store ``record`` and pin it in a new trusted manifest (a legitimate record
    built with different stored data, not an attacker's forgery)."""
    record_ref = w.store.put(w.tenant, dump_json(record))
    entry = ReplayManifestEntry(
        record_id=record.record_id,
        tenant_id=w.tenant,
        record_blob_ref=record_ref,
        record_hash=replay_record_hash(record),
    )
    return World(
        w.tenant, w.store, record, ReplayManifest(entries=(entry,)), w.requests, record_ref
    )


def with_rendered(w: World, rendered: bytes) -> World:
    """The first step recorded with ``rendered`` as its model-facing input."""
    step = w.record.steps[0]
    ref = w.store.put(w.tenant, rendered)
    record_bytes = json.loads(w.store.blobs[(str(w.tenant), step.request_record_ref)])
    record_bytes["rendering_hash"] = rendering_hash(rendered)
    request_bytes = dump_json(ReplayRequestRecord.model_validate_json(json.dumps(record_bytes)))
    new_step = step.model_copy(
        update={
            "rendered_blob_ref": ref,
            "rendering_hash": rendering_hash(rendered),
            "request_record_ref": w.store.put(w.tenant, request_bytes),
            "request_hash": request_hash(request_bytes),
        }
    )
    pinned = repin(w, w.record.model_copy(update={"steps": (new_step, *w.record.steps[1:])}))
    first = w.requests[0].model_copy(
        update={"rendering_hash": new_step.rendering_hash, "request_hash": new_step.request_hash}
    )
    pinned.requests = [first, *w.requests[1:]]
    return pinned


__all__ = ["ReplayFailureKind"]
