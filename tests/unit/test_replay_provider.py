"""Replay Provider (WP-06; B1 6.2-6.3, B1.C10, B1.C24, E.Z14, A 1.1).

Synthetic records only. Every check that fails makes the replay unavailable
with a fixed reason code; nothing is filled in from other sources.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import traceback
from typing import Any

import pytest

from agent_core.adapters.replay_model import DenyAllReplayAccess, ReplayModelProvider
from agent_core.ports.model import ModelPort
from agent_core.types.ids import AttemptId, ModelRequestId, RunId, TenantId
from agent_core.types.model import (
    CostStatus,
    GenerationParams,
    Message,
    ModelCallOutcome,
    ModelResponse,
    ProfileRef,
    Role,
    TextBlock,
    ToolSpec,
    Usage,
    UsageStatus,
    context_hash,
    rendering_hash,
    request_hash,
)
from agent_core.types.model_errors import (
    ModelDefiniteFailureError,
    ModelError,
    ModelOutcomeUnknownError,
    ModelTimeoutError,
)
from agent_core.types.replay import (
    ReplayFailureKind,
    ReplayManifest,
    ReplayOrigin,
    ReplayReason,
    ReplayRenderedInput,
    ReplayUnavailableError,
    replay_record_hash,
)
from agent_core.types.serialization import dump_json
from support import builders as b
from support import model_world as m
from support import replay_world as r

MARKER = "synthetic-secret-" + "Rk2" * 6


def _provider(w: r.World, access: Any = None, **overrides: Any) -> ReplayModelProvider:
    values: dict[str, Any] = {
        "manifest": w.manifest,
        "record_id": w.record.record_id,
        "tenant_id": w.tenant,
        "replay_run_id": b.new_id(RunId),
        "store": w.store,
        "access": access if access is not None else r.ScriptedAccess(),
    }
    values.update(overrides)
    return ReplayModelProvider(**values)


def _unavailable(reason: ReplayReason | None, call: Any) -> ReplayUnavailableError:
    with pytest.raises(ReplayUnavailableError) as excinfo:
        call()
    if reason is not None:
        assert excinfo.value.reason is reason
    return excinfo.value


# ---------------------------------------------------------------- normal replay


def test_replays_recorded_responses_in_order() -> None:
    w = r.world("response", "response")
    provider = _provider(w)
    assert isinstance(provider, ModelPort)
    first = provider.complete(w.requests[0])
    second = provider.complete(w.requests[1])
    assert isinstance(first, ModelResponse)
    assert first.model_request_id == w.requests[0].model_request_id
    assert first.content == (TextBlock(type="text", text="recorded answer 1"),)
    assert second.content == (TextBlock(type="text", text="recorded answer 2"),)
    # Source and replay request IDs are mapped explicitly.
    assert (
        provider.source_of(w.requests[0].model_request_id)
        == w.record.steps[0].source_model_request_id
    )


def test_replay_is_deterministic() -> None:
    w = r.world("response", "response")
    runs = []
    for _ in range(2):
        provider = _provider(w)
        runs.append([provider.complete(q) for q in w.requests])
    assert runs[0] == runs[1]


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (ReplayFailureKind.FAILED_DEFINITE, ModelDefiniteFailureError),
        (ReplayFailureKind.OUTCOME_UNKNOWN, ModelOutcomeUnknownError),
        (ReplayFailureKind.TIMEOUT, ModelTimeoutError),
    ],
)
def test_recorded_failures_are_reproduced(
    failure: ReplayFailureKind, error: type[ModelError]
) -> None:
    w = r.world(failure)
    provider = _provider(w)
    with pytest.raises(error) as excinfo:
        provider.complete(w.requests[0])
    assert not isinstance(excinfo.value, ReplayUnavailableError)
    assert provider.source_usage(w.requests[0].model_request_id) == r.SOURCE_USAGE


# ---------------------------------------------------------------- usage


def test_replay_usage_is_zero_and_source_usage_is_kept_apart() -> None:
    w = r.world("response")
    provider = _provider(w)
    response = provider.complete(w.requests[0])
    # The replay itself consumed nothing: a known zero, not an unknown.
    assert response.usage.status is UsageStatus.FINAL
    assert response.usage.total_tokens == 0
    assert response.usage.cost_status is CostStatus.FINAL
    assert response.usage.cost is not None
    assert response.usage.cost.micro_units == 0
    # The recorded (source) usage is unchanged and reported separately.
    assert provider.source_usage(response.model_request_id) == r.SOURCE_USAGE


# ---------------------------------------------------------------- hashes, each from its own data


@pytest.mark.parametrize(
    ("blob", "reason"),
    [
        ("ir_blob_ref", ReplayReason.CONTEXT_HASH_MISMATCH),
        ("rendered_blob_ref", ReplayReason.RENDERING_HASH_MISMATCH),
        ("request_record_ref", ReplayReason.REQUEST_HASH_MISMATCH),
    ],
)
def test_each_hash_is_recomputed_from_its_stored_data(blob: str, reason: ReplayReason) -> None:
    w = r.world("response")
    ref = getattr(w.record.steps[0], blob)
    r.replace_blob(w, ref, b'{"tampered":true}')
    # The blob no longer matches its content address either; both are checked.
    _unavailable(reason, lambda: _provider(w).complete(w.requests[0]))


def test_request_record_must_name_the_verified_rendering() -> None:
    w = r.world("response")
    step = w.record.steps[0]
    record = json.loads(w.store.blobs[(str(w.tenant), step.request_record_ref)])
    record["rendering_hash"] = rendering_hash(b"another rendering")
    data = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    forged = _rebuilt_world(w, request_bytes=data)
    _unavailable(
        ReplayReason.REQUEST_RECORD_INCONSISTENT,
        lambda: _provider(forged).complete(forged.requests[0]),
    )


def _rebuilt_world(w: r.World, *, request_bytes: bytes) -> r.World:
    """A record whose request record and every hash were recomputed consistently and
    re-pinned in a new manifest, as an attacker controlling the whole store would."""
    step = w.record.steps[0]
    new_ref = w.store.put(w.tenant, request_bytes)
    new_step = step.model_copy(
        update={"request_record_ref": new_ref, "request_hash": request_hash(request_bytes)}
    )
    record = w.record.model_copy(update={"steps": (new_step,)})
    record_ref = w.store.put(w.tenant, dump_json(record))
    entry = w.manifest.entries[0].model_copy(
        update={"record_blob_ref": record_ref, "record_hash": replay_record_hash(record)}
    )
    request = w.requests[0].model_copy(update={"request_hash": new_step.request_hash})
    return r.World(
        w.tenant, w.store, record, ReplayManifest(entries=(entry,)), [request], record_ref
    )


def test_recomputed_record_not_in_the_trusted_manifest_is_refused() -> None:
    w = r.world("response")
    step = w.record.steps[0]
    record = json.loads(w.store.blobs[(str(w.tenant), step.request_record_ref)])
    record["params"]["temperature_milli"] = 999
    data = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    forged = _rebuilt_world(w, request_bytes=data)
    # The attacker also overwrites the stored record at the pinned address.
    r.replace_blob(w, w.record_ref, dump_json(forged.record))
    # Every hash inside the forged record is consistent, but the trusted manifest
    # still pins the original record: the forged record is refused.
    # It is refused when the provider is built, before any step is replayed.
    _unavailable(ReplayReason.MANIFEST_MISMATCH, lambda: _provider(forged, manifest=w.manifest))


def test_same_hash_strings_without_matching_data_do_not_pass() -> None:
    w = r.world("response")
    step = w.record.steps[0]
    # Make the stored IR differ while keeping every recorded hash string.
    r.replace_blob(w, step.ir_blob_ref, b"other ir")
    _unavailable(ReplayReason.CONTEXT_HASH_MISMATCH, lambda: _provider(w).complete(w.requests[0]))


def test_tampered_response_and_usage_are_detected() -> None:
    w = r.world("response")
    outcome = w.record.steps[0].outcome
    ref = outcome.response_blob_ref  # type: ignore[union-attr]
    data = json.loads(w.store.blobs[(str(w.tenant), ref)])
    data["usage"]["cost"]["micro_units"] = 1
    r.replace_blob(w, ref, json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
    _unavailable(ReplayReason.RESPONSE_MISMATCH, lambda: _provider(w).complete(w.requests[0]))


def test_tampered_record_blob_is_detected() -> None:
    w = r.world(ReplayFailureKind.OUTCOME_UNKNOWN)
    data = json.loads(w.store.blobs[(str(w.tenant), w.record_ref)])
    data["steps"][0]["outcome"]["source_usage"]["input_tokens"] = 1
    r.replace_blob(
        w, w.record_ref, json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    )
    _unavailable(ReplayReason.MANIFEST_MISMATCH, lambda: _provider(w))


# ---------------------------------------------------------------- missing, order, exhaustion


def test_missing_record_and_missing_data() -> None:
    w = r.world("response")
    other = r.world("response")
    _unavailable(
        ReplayReason.RECORD_MISSING, lambda: _provider(w, record_id=other.record.record_id)
    )
    del w.store.blobs[(str(w.tenant), w.record.steps[0].rendered_blob_ref)]
    _unavailable(ReplayReason.DATA_MISSING, lambda: _provider(w).complete(w.requests[0]))


def test_order_must_match_and_records_run_out() -> None:
    w = r.world("response", "response")
    provider = _provider(w)
    _unavailable(ReplayReason.OUT_OF_ORDER, lambda: provider.complete(w.requests[1]))
    # Once unavailable, the replay of this run stops.
    _unavailable(ReplayReason.OUT_OF_ORDER, lambda: provider.complete(w.requests[0]))
    fresh = _provider(w)
    fresh.complete(w.requests[0])
    fresh.complete(w.requests[1])
    # A new request (new IDs) for a step that was already replayed: the record ran out.
    later = w.requests[1].model_copy(
        update={"model_request_id": b.new_id(ModelRequestId), "attempt_id": b.new_id(AttemptId)}
    )
    _unavailable(ReplayReason.EXHAUSTED, lambda: fresh.complete(later))


def test_unrecorded_input_is_not_guessed() -> None:
    w = r.world("response")
    stranger = m.request(m.user(MARKER))
    error = _unavailable(ReplayReason.REQUEST_MISMATCH, lambda: _provider(w).complete(stranger))
    assert MARKER not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"profile": ProfileRef(model_id="other-model", profile_version="0.1.0")},
            ReplayReason.PROFILE_MISMATCH,
        ),
        (
            # Generation settings are part of the bound model input (WP06-R01).
            {
                "params": GenerationParams(
                    max_output_tokens=201, temperature_milli=0, timeout_ms=30_000
                )
            },
            ReplayReason.INPUT_MISMATCH,
        ),
    ],
)
def test_model_and_generation_settings_must_match_the_record(
    change: dict[str, Any], reason: ReplayReason
) -> None:
    w = r.world("response")
    request = w.requests[0].model_copy(update=change)
    _unavailable(reason, lambda: _provider(w).complete(request))


def test_request_hashes_must_match_the_step() -> None:
    w = r.world("response")
    request = w.requests[0].model_copy(update={"context_hash": context_hash(b"other")})
    _unavailable(ReplayReason.REQUEST_MISMATCH, lambda: _provider(w).complete(request))


# ---------------------------------------------------------------- formats and origin


def test_unknown_record_format_is_refused() -> None:
    w = r.world("response")
    data = json.loads(w.store.blobs[(str(w.tenant), w.record_ref)])
    data["format_version"] = 3
    r.replace_blob(w, w.record_ref, json.dumps(data).encode())
    _unavailable(ReplayReason.UNKNOWN_FORMAT, lambda: _provider(w))


def test_live_records_are_refused_in_phase_1() -> None:
    w = r.world("response", origin=ReplayOrigin.LIVE_APPROVED)
    _unavailable(ReplayReason.ORIGIN_NOT_ALLOWED, lambda: _provider(w))


def test_replay_run_must_differ_from_the_source_run() -> None:
    w = r.world("response")
    _unavailable(
        ReplayReason.SOURCE_RUN_REUSED, lambda: _provider(w, replay_run_id=w.record.source_run_id)
    )


# ---------------------------------------------------------------- access, deletion, keys (E.Z14)


def test_access_is_checked_before_any_stored_data_is_read() -> None:
    w = r.world("response")
    access = r.ScriptedAccess(deny_all=True)
    reads_before = len(w.store.reads)
    _unavailable(ReplayReason.ACCESS_DENIED, lambda: _provider(w, access=access))
    assert len(w.store.reads) == reads_before


def test_step_data_is_not_read_without_access() -> None:
    w = r.world("response")
    step = w.record.steps[0]
    access = r.ScriptedAccess(deleted={step.ir_blob_ref})
    provider = _provider(w, access=access)
    before = list(w.store.reads)
    _unavailable(ReplayReason.DELETED, lambda: provider.complete(w.requests[0]))
    assert step.ir_blob_ref not in w.store.reads[len(before) :]


def test_missing_key_version_is_refused() -> None:
    w = r.world("response")
    access = r.ScriptedAccess(missing_key_versions={0})
    _unavailable(ReplayReason.KEY_UNAVAILABLE, lambda: _provider(w, access=access))


def test_revocation_during_verification_is_detected() -> None:
    w = r.world("response")
    # Allowed for the record and the step's first check, revoked before the response is returned.
    access = r.ScriptedAccess(revoke_after_checks=2)
    provider = _provider(w, access=access)
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.complete(w.requests[0]))


def test_other_tenant_records_cannot_be_used() -> None:
    w = r.world("response")
    other = b.new_id(TenantId)
    _unavailable(ReplayReason.TENANT_MISMATCH, lambda: _provider(w, tenant_id=other))


def test_without_an_access_verifier_everything_is_refused() -> None:
    w = r.world("response")
    _unavailable(ReplayReason.ACCESS_DENIED, lambda: _provider(w, access=DenyAllReplayAccess()))


# ---------------------------------------------------------------- no substitutes, no fallback


def test_no_substitute_from_other_stores_or_providers() -> None:
    w = r.world("response")
    del w.store.blobs[(str(w.tenant), w.record.steps[0].ir_blob_ref)]
    current_files = r.MemoryBlobStore()
    current_files.put(w.tenant, b"current file content")
    provider = _provider(w)
    _unavailable(ReplayReason.DATA_MISSING, lambda: provider.complete(w.requests[0]))
    assert not hasattr(provider, "fallback")
    assert current_files.reads == []


def test_original_record_and_blobs_are_never_modified() -> None:
    w = r.world("response", ReplayFailureKind.OUTCOME_UNKNOWN)
    snapshot = dict(w.store.blobs)
    record = w.record.model_copy()
    provider = _provider(w)
    provider.complete(w.requests[0])
    with pytest.raises(ModelOutcomeUnknownError):
        provider.complete(w.requests[1])
    assert w.store.blobs == snapshot
    assert w.record == record


def test_tool_requests_in_recorded_responses_are_not_executed() -> None:
    w = r.world("response")
    response = _provider(w).complete(w.requests[0])
    public = {n for n in dir(response) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("execute", "run", "dispatch"))}


def test_no_network_is_needed() -> None:
    with pytest.raises(RuntimeError):
        socket.socket()
    w = r.world("response")
    assert _provider(w).complete(w.requests[0]).final_usage


# --------------------------------- WP06-R01: current input is bound to the record


def _copied_hashes(w: r.World, **change: Any) -> Any:
    """A different current request that carries the recorded hashes verbatim."""
    return w.requests[0].model_copy(update=change)


@pytest.mark.parametrize(
    "messages",
    [
        (m.user("synthetic input 1 but different"),),
        (Message(role=Role.ASSISTANT, content=m.user("synthetic input 1").content),),
        (m.user("synthetic input 1"), m.user("an extra message")),
    ],
)
def test_copied_hashes_with_different_messages_are_refused(messages: tuple[Message, ...]) -> None:
    w = r.world("response")
    request = _copied_hashes(w, messages=messages)
    _unavailable(ReplayReason.INPUT_MISMATCH, lambda: _provider(w).complete(request))


def test_message_order_is_part_of_the_bound_input() -> None:
    w = r.world("response")
    two = (m.user("first"), m.user("second"))
    rendered = dump_json(
        ReplayRenderedInput(
            rendered_input_version=1,
            messages=two,
            tools=w.requests[0].tools,
            params=w.requests[0].params,
        )
    )
    pinned = r.with_rendered(w, rendered)
    swapped = pinned.requests[0].model_copy(update={"messages": tuple(reversed(two))})
    _unavailable(ReplayReason.INPUT_MISMATCH, lambda: _provider(pinned).complete(swapped))
    exact = pinned.requests[0].model_copy(update={"messages": two})
    assert _provider(pinned).complete(exact).final_usage


@pytest.mark.parametrize(
    "change",
    [
        {"tools": (ToolSpec(name="write_file", input_schema_ref=b.digest("schema")),)},
        {"params": GenerationParams(max_output_tokens=200, temperature_milli=1, timeout_ms=30_000)},
    ],
)
def test_tools_and_generation_settings_are_bound(change: dict[str, Any]) -> None:
    w = r.world("response")
    request = _copied_hashes(w, **change)
    error = _unavailable(None, lambda: _provider(w).complete(request))
    assert error.reason in {ReplayReason.INPUT_MISMATCH, ReplayReason.PROFILE_MISMATCH}


@pytest.mark.parametrize(
    "rendered",
    [
        b'{"rendered":"synthetic input 1"}',  # not the synthetic normal form
        b"not json",
    ],
)
def test_rendered_input_must_be_the_explicit_normal_form(rendered: bytes) -> None:
    w = r.with_rendered(r.world("response"), rendered)
    _unavailable(ReplayReason.RENDERED_INPUT_INVALID, lambda: _provider(w).complete(w.requests[0]))


def test_non_canonical_bytes_of_the_right_shape_are_refused() -> None:
    w = r.world("response")
    canonical = w.store.blobs[(str(w.tenant), w.record.steps[0].rendered_blob_ref)]
    spaced = json.dumps(json.loads(canonical), indent=1).encode()
    pinned = r.with_rendered(w, spaced)
    _unavailable(
        ReplayReason.RENDERED_INPUT_INVALID, lambda: _provider(pinned).complete(pinned.requests[0])
    )


def test_format_1_records_are_refused_not_reinterpreted() -> None:
    w = r.world("response")
    data = json.loads(w.store.blobs[(str(w.tenant), w.record_ref)])
    data["format_version"] = 1
    r.replace_blob(w, w.record_ref, json.dumps(data).encode())
    _unavailable(ReplayReason.FORMAT_SUPERSEDED, lambda: _provider(w))


# --------------------------------- WP06-R02: access and disclosure


def test_revocation_at_the_final_check_publishes_nothing() -> None:
    w = r.world("response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    access.revoke_after_checks = access.checks + 5  # step check and four reads pass
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.complete(w.requests[0]))
    access.revoke_after_checks = None
    rid = w.requests[0].model_request_id
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.source_of(rid))
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.source_usage(rid))


@pytest.mark.parametrize("failure", [ReplayFailureKind.FAILED_DEFINITE, "response"])
def test_source_usage_is_not_disclosed_after_revocation(failure: Any) -> None:
    w = r.world(failure)
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    rid = w.requests[0].model_request_id
    with contextlib.suppress(ModelDefiniteFailureError):
        provider.complete(w.requests[0])
    assert provider.source_usage(rid) == r.SOURCE_USAGE
    access.deleted.add(w.record.steps[0].ir_blob_ref)
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.source_usage(rid))
    access.deleted.clear()
    # Nothing cached is handed out again after the revocation.
    _unavailable(ReplayReason.ACCESS_REVOKED, lambda: provider.source_of(rid))


def test_accessors_check_current_access_each_time() -> None:
    w = r.world("response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    provider.complete(w.requests[0])
    before = access.checks
    provider.source_of(w.requests[0].model_request_id)
    provider.source_usage(w.requests[0].model_request_id)
    assert access.checks == before + 2
    assert all(w.record_ref in refs for refs in access.calls[before:])


def test_every_step_check_includes_the_record_itself() -> None:
    w = r.world("response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    first = access.checks
    provider.complete(w.requests[0])
    assert access.calls[first:]
    assert all(w.record_ref in refs for refs in access.calls[first:])


def test_deleting_the_record_after_loading_stops_the_replay() -> None:
    w = r.world("response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    access.deleted.add(w.record_ref)
    _unavailable(ReplayReason.DELETED, lambda: provider.complete(w.requests[0]))


def test_each_blob_read_is_preceded_by_an_access_check() -> None:
    events: list[str] = []
    w = r.world("response")
    w.store.events = events
    access = r.ScriptedAccess(events=events)
    provider = _provider(w, access=access)
    events.clear()
    provider.complete(w.requests[0])
    reads = [i for i, e in enumerate(events) if e == "read"]
    assert len(reads) == 4
    assert all(events[i - 1] == "check" for i in reads)
    assert events[-1] == "check"  # the final check, after every read


def test_unknown_request_ids_reveal_nothing() -> None:
    w = r.world("response")
    provider = _provider(w)
    _unavailable(
        ReplayReason.REQUEST_MISMATCH, lambda: provider.source_of(b.new_id(ModelRequestId))
    )


# --------------------------------- WP06-R03: reuse, concurrency, exceptions


def test_the_same_model_request_id_cannot_be_used_twice() -> None:
    w = r.world("response", "response")
    provider = _provider(w)
    provider.complete(w.requests[0])
    again = w.requests[1].model_copy(update={"model_request_id": w.requests[0].model_request_id})
    _unavailable(ReplayReason.REQUEST_REUSED, lambda: provider.complete(again))


def test_the_same_attempt_cannot_make_two_replayed_calls() -> None:
    w = r.world("response", "response")
    provider = _provider(w)
    provider.complete(w.requests[0])
    again = w.requests[1].model_copy(update={"attempt_id": w.requests[0].attempt_id})
    _unavailable(ReplayReason.ATTEMPT_REUSED, lambda: provider.complete(again))


def test_records_with_a_repeated_source_attempt_are_refused() -> None:
    w = r.world("response", "response")
    data = json.loads(w.store.blobs[(str(w.tenant), w.record_ref)])
    data["steps"][1]["source_attempt_id"] = data["steps"][0]["source_attempt_id"]
    r.replace_blob(w, w.record_ref, json.dumps(data).encode())
    _unavailable(ReplayReason.MANIFEST_MISMATCH, lambda: _provider(w))


def test_reentrant_calls_are_refused_and_stop_the_replay() -> None:
    w = r.world("response", "response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    inner: list[BaseException] = []

    def reenter() -> None:
        access.hook = None
        try:
            provider.complete(w.requests[1])
        except ReplayUnavailableError as exc:
            inner.append(exc)

    access.hook = reenter
    _unavailable(ReplayReason.CONCURRENT_CALL, lambda: provider.complete(w.requests[0]))
    assert [e.reason for e in inner] == [ReplayReason.CONCURRENT_CALL]  # type: ignore[attr-defined]
    _unavailable(ReplayReason.CONCURRENT_CALL, lambda: provider.complete(w.requests[0]))


def test_concurrent_calls_from_threads_do_not_break_the_order() -> None:
    w = r.world("response", "response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    entered, release = threading.Event(), threading.Event()

    def block() -> None:
        access.hook = None
        entered.set()
        release.wait(5)

    access.hook = block
    results: list[BaseException | ModelResponse] = []

    def first() -> None:
        try:
            results.append(provider.complete(w.requests[0]))
        except ReplayUnavailableError as exc:
            results.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    _unavailable(ReplayReason.CONCURRENT_CALL, lambda: provider.complete(w.requests[1]))
    release.set()
    thread.join(5)
    # The first call does not publish a result once the replay has stopped.
    assert isinstance(results[0], ReplayUnavailableError)
    _unavailable(ReplayReason.CONCURRENT_CALL, lambda: provider.complete(w.requests[0]))


@pytest.mark.parametrize("where", ["store", "access"])
def test_unexpected_port_errors_stop_the_replay_without_leaking(where: str) -> None:
    w = r.world("response")
    access = r.ScriptedAccess()
    provider = _provider(w, access=access)
    port: Any = w.store if where == "store" else access
    port.fail_with = RuntimeError(f"port failure near {MARKER}")
    wanted = ReplayReason.STORE_FAILED if where == "store" else ReplayReason.ACCESS_CHECK_FAILED
    error = _unavailable(wanted, lambda: provider.complete(w.requests[0]))
    text = "".join(traceback.format_exception(error))
    assert MARKER not in text
    assert error.__cause__ is None
    port.fail_with = None
    _unavailable(wanted, lambda: provider.complete(w.requests[0]))


def test_unexpected_errors_while_loading_the_record_fail_closed() -> None:
    w = r.world("response")
    w.store.fail_with = OSError(f"disk failure near {MARKER}")
    error = _unavailable(ReplayReason.STORE_FAILED, lambda: _provider(w))
    assert MARKER not in "".join(traceback.format_exception(error))


# --------------------------------- WP06-R04: replay cost


@pytest.mark.parametrize(
    "failure",
    [
        ReplayFailureKind.NOT_SENT,
        ReplayFailureKind.FAILED_DEFINITE,
        ReplayFailureKind.OUTCOME_UNKNOWN,
        ReplayFailureKind.TIMEOUT,
        ReplayFailureKind.RESPONSE_INVALID,
    ],
)
def test_replayed_failures_cost_a_known_zero_and_sent_nothing(failure: ReplayFailureKind) -> None:
    w = r.world(failure)
    with pytest.raises(ModelError) as excinfo:
        _provider(w).complete(w.requests[0])
    error = excinfo.value
    assert not isinstance(error, ReplayUnavailableError)
    assert error.replayed
    # This call: nothing was sent, no tokens, a known zero cost.
    assert error.actual_outcome is ModelCallOutcome.NOT_SENT
    assert error.usage.cost_status is CostStatus.FINAL
    assert error.usage.cost is not None
    assert error.usage.cost.micro_units == 0
    assert error.usage.total_tokens == 0
    # The recorded outcome and usage are kept as they were.
    assert error.source_usage == r.SOURCE_USAGE


def test_unknown_source_usage_stays_unknown() -> None:
    unknown = Usage(
        status=UsageStatus.UNKNOWN,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost=None,
        cost_status=CostStatus.UNKNOWN,
        synthetic=True,
    )
    w = r.world(ReplayFailureKind.OUTCOME_UNKNOWN, source_usage=unknown)
    provider = _provider(w)
    with pytest.raises(ModelOutcomeUnknownError) as excinfo:
        provider.complete(w.requests[0])
    assert excinfo.value.source_usage == unknown
    assert provider.source_usage(w.requests[0].model_request_id) == unknown
    assert excinfo.value.usage.cost_status is CostStatus.FINAL


def test_live_model_errors_are_not_marked_as_replayed() -> None:
    error = ModelDefiniteFailureError("synthetic")
    assert not error.replayed
    assert error.actual_outcome is ModelCallOutcome.FAILED_DEFINITE
    assert error.source_usage is None
