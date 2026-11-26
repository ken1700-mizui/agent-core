"""Safety Engine (WP-09A; C1 4, 6, 7, 11; E 5, 38; C1.S1-S8, S27).

Pure policy decisions, the trusted ActionDescriptor builder, ExactApproval
checks and the minimal synthetic storage admission. Synthetic data only.
"""

from __future__ import annotations

import pickle
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.safety.approval import OwnerApprovalChannel, check_approval, verify_signature
from agent_core.safety.descriptor import pin_of
from agent_core.safety.pdp import decide, require_allowed
from agent_core.safety.sag import admit_synthetic
from agent_core.tool.registry import ToolRegistry
from agent_core.types.errors import (
    ActionDescriptorError,
    ApprovalRejectedError,
    PolicyDeniedError,
    StorageAdmissionError,
)
from agent_core.types.ids import AttemptId, ReservationId, RunId, TenantId
from agent_core.types.safety import (
    ActionDescriptor,
    ApprovalReason,
    ApproverRole,
    DenyReason,
    HashScheme,
    ModelProviderKind,
    ModelTarget,
    Policy,
    SecretKey,
    SignedApproval,
    StopEntry,
    StopScopeType,
    StopStatus,
    TargetResource,
)
from agent_core.types.tools import ResourceAccess, ToolKind
from support import builders as b
from support import safety_world as s
from support import tool_world as tw


def _decide(descriptor: ActionDescriptor, **kw: Any) -> Any:
    at = kw.pop("at")
    registration = kw.pop("registration", s.READ_TOOL)
    return decide(
        policy=kw.pop("policy", s.policy()),
        grant=kw.pop("grant", s.grant(at)),
        descriptor=descriptor,
        registration=registration,
        context=kw.pop("context", s.context()),
    )


def _denied(decision: Any, reason: DenyReason) -> None:
    assert not decision.allowed
    assert reason in decision.reasons


# ---------------------------------------------------------------- allowed operations


def test_a_granted_read_is_allowed_without_approval() -> None:
    at = s.ref()
    descriptor = s.read_descriptor(at)
    decision = _decide(descriptor, at=at)
    assert decision.allowed
    assert decision.reasons == ()
    assert decision.approval_required is False
    assert decision.action_hash == descriptor.action_hash()
    assert require_allowed(decision) is decision


def test_an_irreversible_write_needs_approval() -> None:
    at = s.ref()
    decision = _decide(s.write_descriptor(at), at=at, registration=s.WRITE_TOOL)
    assert decision.allowed
    assert decision.approval_required is True


def test_a_fake_model_call_is_allowed() -> None:
    at = s.ref()
    decision = _decide(s.model_descriptor(at), at=at, registration=None)
    assert decision.allowed


# ---------------------------------------------------------------- default deny


def test_unregistered_or_unpinned_tools_are_denied() -> None:
    at = s.ref()
    other = tw.manifest(tool_id="list_dir")
    reg = ToolRegistry(tw.catalog(other))
    registration = reg.register(other, artifact_hash=tw.ARTIFACT)
    descriptor = s.read_descriptor(
        at, registration=registration, canonical_input='{"path":"workspace/a.txt"}'
    )
    decision = _decide(descriptor, at=at, registration=registration)
    _denied(decision, DenyReason.TOOL_NOT_IN_POLICY)
    _denied(decision, DenyReason.TOOL_NOT_GRANTED)


def test_an_unapproved_version_is_denied() -> None:
    at = s.ref()
    newer = tw.manifest(version="1.0.1")
    reg = ToolRegistry(tw.catalog(newer))
    registration = reg.register(newer, artifact_hash=tw.ARTIFACT)
    decision = _decide(
        s.read_descriptor(at, registration=registration), at=at, registration=registration
    )
    _denied(decision, DenyReason.TOOL_NOT_IN_POLICY)


def test_a_tool_outside_the_run_grant_is_denied() -> None:
    at = s.ref()
    decision = _decide(
        s.write_descriptor(at),
        at=at,
        registration=s.WRITE_TOOL,
        grant=s.grant(at, tools=(pin_of(s.READ_TOOL),)),
    )
    _denied(decision, DenyReason.TOOL_NOT_GRANTED)


def test_an_unknown_operation_is_denied() -> None:
    at = s.ref()
    # A tool call with no trusted registration cannot be decided.
    decision = _decide(s.read_descriptor(at), at=at, registration=None)
    _denied(decision, DenyReason.UNKNOWN_ACTION)
    with pytest.raises(PolicyDeniedError):
        decide(
            policy=s.policy(),
            grant=s.grant(at),
            descriptor="not a descriptor",  # type: ignore[arg-type]
            registration=None,
            context=s.context(),
        )


def test_registration_must_match_the_descriptor() -> None:
    at = s.ref()
    decision = _decide(s.read_descriptor(at), at=at, registration=s.WRITE_TOOL)
    _denied(decision, DenyReason.DESCRIPTOR_MISMATCH)


@pytest.mark.parametrize(
    ("path", "access", "reason"),
    [
        ("workspace/a.txt", ResourceAccess.WRITE, DenyReason.RESOURCE_OUTSIDE_GRANT),
        ("workspace/secret/key.txt", ResourceAccess.READ, DenyReason.RESOURCE_DENIED),
    ],
)
def test_resources_beyond_the_grant_or_on_the_deny_list_are_denied(
    path: str, access: ResourceAccess, reason: DenyReason
) -> None:
    at = s.ref()
    registration = s.WRITE_TOOL if access is ResourceAccess.WRITE else s.READ_TOOL
    canonical = f'{{"path":"{path}"}}'
    descriptor = s.read_descriptor(
        at,
        registration=registration,
        canonical_input=canonical,
    )
    _denied(_decide(descriptor, at=at, registration=registration), reason)


def test_currently_deleted_resources_are_denied() -> None:
    at = s.ref()
    decision = _decide(
        s.read_descriptor(at), at=at, context=s.context(denied_resources=("workspace/a.txt",))
    )
    _denied(decision, DenyReason.RESOURCE_DENIED)


def test_cost_cap_above_the_grant_is_denied() -> None:
    at = s.ref()
    decision = _decide(s.read_descriptor(at, cost_cap=s.xtu(5_001)), at=at)
    _denied(decision, DenyReason.COST_CAP_EXCEEDS_GRANT)


@pytest.mark.parametrize(
    ("grant_change", "reason"),
    [
        ({"expires_at_ms": s.NOW}, DenyReason.GRANT_EXPIRED),
        ({"issued_at_ms": s.NOW + 1, "expires_at_ms": s.NOW + 10}, DenyReason.GRANT_EXPIRED),
        ({"expires_at_ms": s.NOW + 30_000}, DenyReason.DESCRIPTOR_OUTLIVES_GRANT),
    ],
)
def test_expired_grants_are_denied(grant_change: dict[str, Any], reason: DenyReason) -> None:
    at = s.ref()
    decision = _decide(s.read_descriptor(at), at=at, grant=s.grant(at, **grant_change))
    _denied(decision, reason)


def test_expired_descriptor_is_denied() -> None:
    at = s.ref()
    decision = _decide(s.read_descriptor(at, expires_at_ms=s.NOW), at=at)
    _denied(decision, DenyReason.DESCRIPTOR_EXPIRED)


def test_other_tenant_and_other_run_are_denied() -> None:
    at = s.ref()
    other_tenant = s.ref(tenant_id=b.new_id(TenantId), run_id=at.run_id, task_id=at.task_id)
    _denied(_decide(s.read_descriptor(other_tenant), at=at), DenyReason.TENANT_MISMATCH)
    other_run = s.ref(task_id=at.task_id, run_id=b.new_id(RunId))
    _denied(_decide(s.read_descriptor(other_run), at=at), DenyReason.GRANT_SCOPE_MISMATCH)


@pytest.mark.parametrize(
    ("context_change", "reason"),
    [
        ({"policy_version": "policy-2"}, DenyReason.STALE_POLICY),
        ({"access_epoch": 1}, DenyReason.STALE_ACCESS_EPOCH),
        ({"runtime_owner_epoch": 0}, DenyReason.STALE_OWNER),
    ],
)
def test_stale_policy_epoch_or_owner_is_denied(
    context_change: dict[str, Any], reason: DenyReason
) -> None:
    at = s.ref()
    _denied(_decide(s.read_descriptor(at), at=at, context=s.context(**context_change)), reason)


@pytest.mark.parametrize("scope", ["global", "tenant", "run"])
def test_any_stopped_scope_denies(scope: str) -> None:
    at = s.ref()
    scopes = {
        "global": (StopScopeType.GLOBAL, "global"),
        "tenant": (StopScopeType.TENANT, str(at.tenant_id)),
        "run": (StopScopeType.RUN, str(at.run_id)),
    }
    kind, scope_id = scopes[scope]
    entries = [e for e in s.running() if (e.scope_type, e.scope_id) != (kind, scope_id)]
    for status in (StopStatus.STOPPED, StopStatus.RESUMING):
        stop = StopEntry(scope_type=kind, scope_id=scope_id, stop_seq=3, status=status)
        decision = _decide(s.read_descriptor(at), at=at, context=s.context(stops=(*entries, stop)))
        _denied(decision, DenyReason.STOPPED)


def test_missing_global_stop_entry_is_inconsistent() -> None:
    at = s.ref()
    decision = _decide(s.read_descriptor(at), at=at, context=s.context(stops=()))
    _denied(decision, DenyReason.STOP_STATE_INCONSISTENT)


def test_live_providers_and_ungranted_models_are_denied() -> None:
    at = s.ref()
    live = s.model_descriptor(
        at, model=ModelTarget(profile=s.PROFILE, provider=ModelProviderKind.LIVE)
    )
    _denied(_decide(live, at=at, registration=None), DenyReason.LIVE_PROVIDER_FORBIDDEN)
    with pytest.raises(ValidationError):
        s.policy(model_providers=(ModelProviderKind.LIVE,))
    other = s.model_descriptor(at)
    _denied(
        _decide(other, at=at, registration=None, grant=s.grant(at, model_profiles=())),
        DenyReason.MODEL_NOT_GRANTED,
    )


def test_shell_and_external_tools_are_denied() -> None:
    at = s.ref()
    shell = tw.manifest(
        tool_id="run_cmd", kind=ToolKind.SANDBOXED_COMMAND, sandbox_profile=b.digest("sbx")
    )
    pin = pin_of(_unchecked_registration(shell))
    registration = _unchecked_registration(shell)
    descriptor = s.read_descriptor(at, registration=registration)
    decision = _decide(
        descriptor,
        at=at,
        registration=registration,
        policy=s.policy(tools=(pin,)),
        grant=s.grant(at, tools=(pin,)),
    )
    _denied(decision, DenyReason.TOOL_KIND_NOT_ALLOWED)
    _denied(decision, DenyReason.SANDBOX_NOT_AVAILABLE)


def _unchecked_registration(manifest: Any) -> Any:
    from agent_core.types.tools import ToolRegistration

    return ToolRegistration(
        manifest=manifest, artifact_hash=tw.ARTIFACT, approval_ref=b.digest("x")
    )


def test_the_synthetic_label_alone_is_not_trust() -> None:
    at = s.ref()
    untrusted = s.read_descriptor(at, workspace_ref=b.digest("model-said-synthetic"))
    _denied(_decide(untrusted, at=at), DenyReason.UNTRUSTED_WORKSPACE)
    _denied(
        _decide(s.read_descriptor(at, workspace_ref=None), at=at), DenyReason.UNTRUSTED_WORKSPACE
    )
    with pytest.raises(StorageAdmissionError):
        admit_synthetic(
            s.policy(), origin_ref=b.digest("model-said-synthetic"), labels=("synthetic",)
        )
    with pytest.raises(StorageAdmissionError):
        admit_synthetic(s.policy(), origin_ref=s.WORKSPACE, labels=("synthetic", "personal"))
    admit_synthetic(s.policy(), origin_ref=s.WORKSPACE, labels=("synthetic",))


def test_all_failing_checks_are_reported_and_nothing_is_allowed_by_default() -> None:
    at = s.ref()
    decision = _decide(
        s.read_descriptor(at, cost_cap=s.xtu(9_999)),
        at=at,
        context=s.context(access_epoch=4, stops=()),
    )
    assert {
        DenyReason.COST_CAP_EXCEEDS_GRANT,
        DenyReason.STALE_ACCESS_EPOCH,
        DenyReason.STOP_STATE_INCONSISTENT,
    } <= set(decision.reasons)
    with pytest.raises(PolicyDeniedError) as excinfo:
        require_allowed(decision)
    assert DenyReason.STALE_ACCESS_EPOCH in excinfo.value.reasons


def test_model_claims_and_proposals_are_not_authority() -> None:
    at = s.ref()
    proposal = s.REGISTRY.propose(
        tool_id="write_file",
        version="1.0.0",
        arguments='{"path":"workspace/out/r.txt"}',
        attempt_id=at.attempt_id,
    )
    # The registry's suggestion is not the decision: the PDP derives its own.
    advisory = proposal.model_copy(update={"requires_approval": False})
    descriptor = s.write_descriptor(at, proposal=advisory)
    decision = _decide(descriptor, at=at, registration=s.WRITE_TOOL)
    assert decision.approval_required is True
    # There is no input for claimed permissions at all.
    assert "permissions" not in decide.__code__.co_varnames


# ---------------------------------------------------------------- the trusted descriptor builder


def test_descriptor_recomputes_hashes_and_rejects_mismatched_proposals() -> None:
    at = s.ref()
    proposal = s.REGISTRY.propose(
        tool_id="read_file",
        version="1.0.0",
        arguments='{"path":"workspace/a.txt"}',
        attempt_id=at.attempt_id,
    )
    descriptor = s.read_descriptor(at, proposal=proposal)
    assert descriptor.args_hash == proposal.input_hash
    for change in (
        {"manifest_hash": b.digest("forged")},
        {"artifact_hash": b.digest("forged")},
        {"input_hash": b.digest("forged")},
        {"attempt_id": b.new_id(AttemptId)},
    ):
        with pytest.raises(ActionDescriptorError):
            s.read_descriptor(at, proposal=proposal.model_copy(update=change))


@pytest.mark.parametrize("canonical", ['{ "path":"workspace/a.txt"}', '["x"]', '{"a":1,"a":2}'])
def test_descriptor_needs_canonical_arguments(canonical: str) -> None:
    with pytest.raises(ActionDescriptorError):
        s.read_descriptor(s.ref(), canonical_input=canonical)


def test_descriptor_rejects_undeclared_resources() -> None:
    with pytest.raises(ActionDescriptorError):
        # The target is derived from the argument; it is outside the manifest.
        s.read_descriptor(s.ref(), canonical_input='{"path":"elsewhere/a.txt"}')
    for bad in ("../a.txt", "/etc/passwd", "workspace/../x", "workspace/*"):
        with pytest.raises(ValidationError):
            TargetResource(path=bad, access=ResourceAccess.READ)


@pytest.mark.parametrize(
    "change",
    [
        {"canonical_input": '{"path":"workspace/b.txt"}'},
        {"cost_cap": s.xtu(999)},
        {"reservation_id": b.new_id(ReservationId)},
        {"expires_at_ms": s.NOW + 59_000},
    ],
)
def test_action_hash_covers_arguments_cost_reservation_and_expiry(change: dict[str, Any]) -> None:
    at = s.ref()
    base = s.read_descriptor(at, reservation_id=b.new_id(ReservationId))
    values = {"reservation_id": base.reservation_id, **change}
    assert s.read_descriptor(at, **values).action_hash() != base.action_hash()


def test_action_hash_covers_the_attempt_and_the_executable() -> None:
    at = s.ref()
    rsv = b.new_id(ReservationId)
    base = s.read_descriptor(at, reservation_id=rsv)
    again = s.ref(
        task_id=at.task_id, run_id=at.run_id, step_id=at.step_id, attempt_id=b.new_id(AttemptId)
    )
    assert s.read_descriptor(again, reservation_id=rsv).action_hash() != base.action_hash()
    other_artifact = base.model_copy(
        update={"tool": base.tool.model_copy(update={"artifact_hash": b.digest("v2")})}  # type: ignore[union-attr]
    )
    assert other_artifact.action_hash() != base.action_hash()


def test_model_call_request_hash_is_computed_from_the_record() -> None:
    at = s.ref()
    record_a, rendered_a = s.model_request(max_output_tokens=100)
    record_b, rendered_b = s.model_request(max_output_tokens=101)
    first = s.model_descriptor(at, request_record=record_a, rendered_input=rendered_a)
    second = s.model_descriptor(
        at, request_record=record_b, rendered_input=rendered_b, reservation_id=first.reservation_id
    )
    assert first.request_hash != second.request_hash
    # The record must be a valid request record (WP09-R01): arbitrary bytes are refused.
    for record in (b"", b'{"a":1}'):
        with pytest.raises(ActionDescriptorError):
            s.model_descriptor(at, request_record=record)


def test_keyed_hashes_are_declared_but_refused_in_phase_1() -> None:
    at = s.ref()
    keyed = s.read_descriptor(at).model_copy(update={"hash_scheme": HashScheme.KEYED_HMAC_SHA256})
    _denied(_decide(keyed, at=at), DenyReason.HASH_SCHEME_NOT_ALLOWED)


# ---------------------------------------------------------------- ExactApproval


def _check(signed: SignedApproval, key: SecretKey, action_hash: str, **kw: Any) -> Any:
    values: dict[str, Any] = {
        "key": key,
        "action_hash": action_hash,
        "tenant_id": b.TENANT,
        "now_ms": s.NOW,
        "max_ttl_ms": 600_000,
        "uses": 0,
        "revoked": False,
    }
    values.update(kw)
    return check_approval(signed, **values)


def _reasons(call: Any) -> set[str]:
    with pytest.raises(ApprovalRejectedError) as excinfo:
        call()
    return set(excinfo.value.reasons)


def test_owner_channel_approval_is_valid_for_its_action_only() -> None:
    key = s.new_key()
    at = s.ref()
    action = s.write_descriptor(at).action_hash()
    signed = s.channel(key).approve(s.approval(action))
    assert _check(signed, key, action).action_hash == action
    other = s.write_descriptor(
        at,
        canonical_input='{"path":"workspace/out/x.txt"}',
    )
    assert ApprovalReason.WRONG_ACTION in _reasons(lambda: _check(signed, key, other.action_hash()))


def test_forged_approvals_are_rejected() -> None:
    key = s.new_key()
    action = s.write_descriptor(s.ref()).action_hash()
    # A typed approval object with a made-up MAC grants nothing.
    forged = SignedApproval(approval=s.approval(action), owner_mac="0" * 64)
    assert _reasons(lambda: _check(forged, key, action)) == {ApprovalReason.FORGED}
    # Signed with another key (not the owner channel's key).
    other = s.channel(s.new_key()).approve(s.approval(action))
    assert _reasons(lambda: _check(other, key, action)) == {ApprovalReason.FORGED}
    # Changing the approval after signing breaks the MAC.
    signed = s.channel(key).approve(s.approval(action))
    widened = signed.model_copy(
        update={"approval": signed.approval.model_copy(update={"expires_at_ms": s.NOW + 10**7})}
    )
    assert _reasons(lambda: verify_signature(key, widened)) == {ApprovalReason.FORGED}


@pytest.mark.parametrize(
    ("change", "check", "reason"),
    [
        ({"expires_at_ms": s.NOW}, {}, ApprovalReason.EXPIRED),
        (
            {"granted_at_ms": s.NOW + 1, "expires_at_ms": s.NOW + 10},
            {},
            ApprovalReason.NOT_YET_VALID,
        ),
        ({"expires_at_ms": s.NOW + 10**7}, {}, ApprovalReason.TTL_TOO_LONG),
        ({"approver_role": ApproverRole.USER}, {}, ApprovalReason.ROLE_INSUFFICIENT),
        ({"tenant_id": b.new_id(TenantId)}, {}, ApprovalReason.WRONG_TENANT),
        ({}, {"uses": 1}, ApprovalReason.USED),
        ({}, {"revoked": True}, ApprovalReason.REVOKED),
    ],
)
def test_approval_limits(
    change: dict[str, Any], check: dict[str, Any], reason: ApprovalReason
) -> None:
    key = s.new_key()
    action = s.write_descriptor(s.ref()).action_hash()
    signed = s.channel(key).approve(s.approval(action, **change))
    assert reason in _reasons(lambda: _check(signed, key, action, **check))


def test_approvals_are_single_use_by_type() -> None:
    with pytest.raises(ValidationError):
        s.approval(b.digest("a"), max_uses=2)


def test_the_owner_channel_and_keys_do_not_leak() -> None:
    key = s.new_key()
    assert "<SecretKey>" == repr(key) == str(key)
    with pytest.raises(TypeError):
        pickle.dumps(key)
    with pytest.raises(TypeError):
        OwnerApprovalChannel(b"raw-bytes-are-not-a-key")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="32"):
        SecretKey(b"short")
    signed = s.channel(key).approve(s.approval(b.digest("a")))
    assert key.reveal().hex() not in signed.model_dump_json()


def test_policy_values_are_explicit_not_defaults() -> None:
    required = {name for name, f in Policy.model_fields.items() if f.is_required()}
    assert {"max_permit_ttl_ms", "max_approval_ttl_ms", "allowed_effects"} <= required
