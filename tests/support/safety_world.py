"""Synthetic policy, grant, descriptors and approvals for WP-09 tests."""

from __future__ import annotations

import secrets
from typing import Any

from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.descriptor import AttemptRef, model_action, pin_of, tool_action
from agent_core.tool.registry import ToolRegistry
from agent_core.types.ids import (
    ApprovalId,
    AttemptId,
    ReservationId,
    RunGrantId,
    RunId,
    StepId,
    TaskId,
    UserId,
)
from agent_core.types.model import ProfileRef
from agent_core.types.safety import (
    ActionDescriptor,
    ApproverRole,
    ExactApproval,
    ModelProviderKind,
    ModelTarget,
    PathScope,
    Policy,
    RunGrant,
    SafetyContext,
    SecretKey,
    StopEntry,
    StopScopeType,
    StopStatus,
    TargetResource,
)
from agent_core.types.tools import ResourceAccess, ToolEffectClass, ToolManifest, ToolRegistration
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import tool_world as tw

NOW = 1_800_000_000_000
POLICY_VERSION = "policy-1"
OWNER = b.new_id(UserId)
WORKSPACE = b.digest("synthetic-workspace-tree")
PROFILE = ProfileRef(model_id="scripted-model-small", profile_version="0.1.0")
READ_MANIFEST = tw.manifest()
WRITE_MANIFEST = tw.manifest(**tw.WRITE_BODY)


def xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def registry(*manifests: ToolManifest) -> ToolRegistry:
    chosen = manifests or (READ_MANIFEST, WRITE_MANIFEST)
    reg = ToolRegistry(tw.catalog(*chosen))
    for sealed in chosen:
        reg.register(sealed, artifact_hash=tw.ARTIFACT)
    return reg


REGISTRY = registry()
READ_TOOL: ToolRegistration = REGISTRY.get("read_file", "1.0.0")
WRITE_TOOL: ToolRegistration = REGISTRY.get("write_file", "1.0.0")


def ref(**overrides: Any) -> AttemptRef:
    values: dict[str, Any] = {
        "tenant_id": b.TENANT,
        "task_id": b.new_id(TaskId),
        "run_id": b.new_id(RunId),
        "step_id": b.new_id(StepId),
        "attempt_id": b.new_id(AttemptId),
    }
    values.update(overrides)
    return AttemptRef(**values)


def policy(**overrides: Any) -> Policy:
    values: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "tenant_id": b.TENANT,
        "tools": (pin_of(READ_TOOL), pin_of(WRITE_TOOL)),
        "allowed_effects": (
            ToolEffectClass.READ_ONLY,
            ToolEffectClass.IRREVERSIBLE,
            ToolEffectClass.MODEL_CALL,
        ),
        "approval_effects": (ToolEffectClass.IRREVERSIBLE,),
        "approval_tools": (),
        "model_providers": (ModelProviderKind.FAKE, ModelProviderKind.REPLAY),
        "deny_resources": ("workspace/secret/**",),
        "trusted_synthetic_workspaces": (WORKSPACE,),
        "max_permit_ttl_ms": 60_000,
        "max_approval_ttl_ms": 600_000,
    }
    values.update(overrides)
    return Policy(**values)


def grant(at: AttemptRef, **overrides: Any) -> RunGrant:
    values: dict[str, Any] = {
        "run_grant_id": b.new_id(RunGrantId),
        "tenant_id": at.tenant_id,
        "user_id": OWNER,
        "task_id": at.task_id,
        "run_id": at.run_id,
        "tools": (pin_of(READ_TOOL), pin_of(WRITE_TOOL)),
        "files": PathScope(read=("workspace/**",), write=("workspace/out/**",)),
        "model_profiles": (PROFILE,),
        "max_cost_per_action": xtu(5_000),
        "policy_version": POLICY_VERSION,
        "access_epoch_at_grant": 0,
        "issued_at_ms": NOW - 1_000,
        "expires_at_ms": NOW + 3_600_000,
    }
    values.update(overrides)
    return RunGrant(**values)


def read_descriptor(at: AttemptRef, **overrides: Any) -> ActionDescriptor:
    values: dict[str, Any] = {
        "registration": READ_TOOL,
        "canonical_input": '{"path":"workspace/a.txt"}',
        "proposal": None,
        "attempt": at,
        "resources": (TargetResource(path="workspace/a.txt", access=ResourceAccess.READ),),
        "workspace_ref": WORKSPACE,
        "cost_cap": xtu(1_000),
        "reservation_id": b.new_id(ReservationId),
        "policy_version": POLICY_VERSION,
        "access_epoch": 0,
        "expires_at_ms": NOW + 60_000,
    }
    values.update(overrides)
    return tool_action(**values)


def write_descriptor(at: AttemptRef, **overrides: Any) -> ActionDescriptor:
    values: dict[str, Any] = {
        "registration": WRITE_TOOL,
        "canonical_input": '{"path":"workspace/out/r.txt"}',
        "resources": (TargetResource(path="workspace/out/r.txt", access=ResourceAccess.WRITE),),
    }
    values.update(overrides)
    return read_descriptor(at, **values)


def model_descriptor(at: AttemptRef, **overrides: Any) -> ActionDescriptor:
    values: dict[str, Any] = {
        "model": ModelTarget(profile=PROFILE, provider=ModelProviderKind.FAKE),
        "request_record": b'{"synthetic":"request record"}',
        "attempt": at,
        "cost_cap": xtu(2_000),
        "reservation_id": b.new_id(ReservationId),
        "policy_version": POLICY_VERSION,
        "access_epoch": 0,
        "expires_at_ms": NOW + 60_000,
    }
    values.update(overrides)
    return model_action(**values)


def running() -> tuple[StopEntry, ...]:
    return (
        StopEntry(
            scope_type=StopScopeType.GLOBAL,
            scope_id="global",
            stop_seq=0,
            status=StopStatus.RUNNING,
        ),
    )


def context(**overrides: Any) -> SafetyContext:
    values: dict[str, Any] = {
        "now_ms": NOW,
        "tenant_id": b.TENANT,
        "policy_version": POLICY_VERSION,
        "access_epoch": 0,
        "journal_owner_epoch": 1,
        "runtime_owner_epoch": 1,
        "stops": running(),
        "denied_resources": (),
    }
    values.update(overrides)
    return SafetyContext(**values)


def new_key() -> SecretKey:
    return SecretKey(secrets.token_bytes(32))


def approval(action_hash: str, **overrides: Any) -> ExactApproval:
    values: dict[str, Any] = {
        "approval_version": 1,
        "approval_id": b.new_id(ApprovalId),
        "tenant_id": b.TENANT,
        "action_hash": action_hash,
        "approver_id": OWNER,
        "approver_role": ApproverRole.OWNER,
        "granted_at_ms": NOW - 1_000,
        "expires_at_ms": NOW + 300_000,
        "max_uses": 1,
    }
    values.update(overrides)
    return ExactApproval(**values)


def channel(key: SecretKey) -> OwnerApprovalChannel:
    return OwnerApprovalChannel(key)
