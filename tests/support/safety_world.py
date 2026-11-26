"""Synthetic policy, grant, descriptors and approvals for WP-09 tests."""

from __future__ import annotations

import secrets
from typing import Any

from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.descriptor import AttemptRef, model_action, pin_of, tool_action
from agent_core.tool.registry import ToolRegistry
from agent_core.types.cost import PriceItem, PriceTable
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
from agent_core.types.model import GenerationParams, ProfileRef, rendering_hash
from agent_core.types.replay import ReplayRenderedInput, ReplayRequestRecord
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
)
from agent_core.types.serialization import dump_json
from agent_core.types.tools import ToolEffectClass, ToolManifest, ToolRegistration
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import model_world as m
from support import tool_world as tw

NOW = 1_800_000_000_000
POLICY_VERSION = "policy-1"
OWNER = b.new_id(UserId)
WORKSPACE = b.digest("synthetic-workspace-tree")
PROFILE = ProfileRef(model_id="scripted-model-small", profile_version="0.1.0")
READ_MANIFEST = tw.manifest()
WRITE_MANIFEST = tw.manifest(**tw.WRITE_BODY)
# A read tool with a synthetic manifest cost of 300 micro XTU (WP09-R01 tests).
COSTLY_MANIFEST = tw.manifest(
    tool_id="costly_read", cost_estimate=Budget(currency=Currency.XTU, micro_units=300)
)
PERIOD = "synthetic-2026-11"
PRICE_TABLE = PriceTable(
    price_table_id="test_virtual",
    version="1.0.0",
    status="test",
    items=(
        (
            "scripted-model-small",
            PriceItem(input_micro_per_mtok=1_000_000, output_micro_per_mtok=4_000_000),
        ),
    ),
)


def xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def registry(*manifests: ToolManifest) -> ToolRegistry:
    chosen = manifests or (READ_MANIFEST, WRITE_MANIFEST, COSTLY_MANIFEST)
    reg = ToolRegistry(tw.catalog(*chosen))
    for sealed in chosen:
        reg.register(sealed, artifact_hash=tw.ARTIFACT)
    return reg


REGISTRY = registry()
READ_TOOL: ToolRegistration = REGISTRY.get("read_file", "1.0.0")
WRITE_TOOL: ToolRegistration = REGISTRY.get("write_file", "1.0.0")
COSTLY_TOOL: ToolRegistration = REGISTRY.get("costly_read", "1.0.0")
ALL_PINS = (pin_of(READ_TOOL), pin_of(WRITE_TOOL), pin_of(COSTLY_TOOL))


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
        "tools": ALL_PINS,
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
        "price_table_id": PRICE_TABLE.price_table_id,
        "price_table_version": PRICE_TABLE.version,
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
        "period_id": PERIOD,
        "experiment_id": None,
        "task_id": at.task_id,
        "run_id": at.run_id,
        "tools": ALL_PINS,
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
    }
    values.update(overrides)
    return read_descriptor(at, **values)


def model_request(max_output_tokens: int = 200) -> tuple[bytes, bytes]:
    """A synthetic request record and the rendered input it names."""
    rendered = dump_json(
        ReplayRenderedInput(
            rendered_input_version=1,
            messages=(m.user("synthetic question"),),
            tools=(),
            params=GenerationParams(
                max_output_tokens=max_output_tokens, temperature_milli=0, timeout_ms=30_000
            ),
        )
    )
    record = ReplayRequestRecord(
        request_record_version=1,
        destination="synthetic",
        profile=PROFILE,
        params=GenerationParams(
            max_output_tokens=max_output_tokens, temperature_milli=0, timeout_ms=30_000
        ),
        tools=(),
        rendering_hash=rendering_hash(rendered),
    )
    return dump_json(record), rendered


MODEL_RECORD, MODEL_RENDERED = model_request()


def model_descriptor(at: AttemptRef, **overrides: Any) -> ActionDescriptor:
    values: dict[str, Any] = {
        "model": ModelTarget(profile=PROFILE, provider=ModelProviderKind.FAKE),
        "request_record": MODEL_RECORD,
        "rendered_input": MODEL_RENDERED,
        "price_table_id": PRICE_TABLE.price_table_id,
        "price_table_version": PRICE_TABLE.version,
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
