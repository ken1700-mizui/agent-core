"""Policy Decision Point (WP-09A; C1 4.2, 6.2 step 4, 11.4; E 5; C1.S1, S2, S7, S8).

``decide`` is a pure function of trusted inputs: the owner's ``Policy``, the
Run's ``RunGrant``, the trusted ``ToolRegistration`` (for tool calls), the
``ActionDescriptor`` built by the trusted builder and the current
``SafetyContext`` read from the ledger. Nothing here comes from a model: there
is no parameter for claimed permissions, and a ``ToolProposal``'s own
``requires_approval`` is advisory only (the PDP computes its own answer).

Default deny: every check must pass. All failing checks are reported.
"""

from __future__ import annotations

from agent_core.safety.paths import any_match
from agent_core.types.errors import PolicyDeniedError
from agent_core.types.safety import (
    ActionDescriptor,
    ActionKind,
    DenyReason,
    HashScheme,
    ModelCostBasis,
    ModelProviderKind,
    Policy,
    PolicyDecision,
    RunGrant,
    SafetyContext,
    StopScopeType,
    StopStatus,
    ToolCostBasis,
)
from agent_core.types.tools import (
    ApprovalCondition,
    ResourceAccess,
    ToolEffectClass,
    ToolKind,
    ToolRegistration,
)

# Phase 1 runs no shell and sends nothing outside (PHASE_1 plan section 2).
PHASE_1_TOOL_KINDS = frozenset({ToolKind.BUILTIN})
PHASE_1_MODEL_PROVIDERS = frozenset({ModelProviderKind.FAKE, ModelProviderKind.REPLAY})


def stop_reasons(context: SafetyContext, descriptor: ActionDescriptor) -> list[DenyReason]:
    """C1 11.4: run only if global, tenant and run are all RUNNING. A missing global
    entry is inconsistent; a missing tenant or run entry means never stopped."""
    wanted = {
        (StopScopeType.GLOBAL, "global"),
        (StopScopeType.TENANT, str(descriptor.tenant_id)),
        (StopScopeType.RUN, str(descriptor.run_id)),
    }
    found = {(e.scope_type, e.scope_id): e for e in context.stops}
    if (StopScopeType.GLOBAL, "global") not in found:
        return [DenyReason.STOP_STATE_INCONSISTENT]
    for key in wanted:
        entry = found.get(key)
        if entry is not None and entry.status is not StopStatus.RUNNING:
            return [DenyReason.STOPPED]
    return []


def _approval_required(
    policy: Policy, descriptor: ActionDescriptor, registration: ToolRegistration | None
) -> bool:
    if descriptor.effect_class in policy.approval_effects:
        return True
    if registration is None:
        return False
    manifest = registration.manifest
    if manifest.tool_id in policy.approval_tools:
        return True
    conditions = set(manifest.approval_conditions)
    if ApprovalCondition.ALWAYS in conditions:
        return True
    if ApprovalCondition.ON_WRITE in conditions and manifest.declares_writes:
        return True
    return (
        ApprovalCondition.ON_IRREVERSIBLE in conditions
        and manifest.effect_class is ToolEffectClass.IRREVERSIBLE
    )


def approval_required(
    policy: Policy, descriptor: ActionDescriptor, registration: ToolRegistration | None
) -> bool:
    """The approval rule the ledger applies (the PDP's own), for the runtime to state."""
    return _approval_required(policy, descriptor, registration)


def _tool_reasons(
    policy: Policy,
    grant: RunGrant,
    descriptor: ActionDescriptor,
    registration: ToolRegistration | None,
    context: SafetyContext,
) -> list[DenyReason]:
    reasons: list[DenyReason] = []
    pin = descriptor.tool
    if pin is None or registration is None:
        return [DenyReason.UNKNOWN_ACTION]
    manifest = registration.manifest
    actual = (
        manifest.tool_id,
        manifest.version,
        manifest.manifest_hash,
        registration.artifact_hash,
    )
    if actual != (
        pin.tool_id,
        pin.version,
        pin.manifest_hash,
        pin.artifact_hash,
    ):
        reasons.append(DenyReason.DESCRIPTOR_MISMATCH)
    if pin not in policy.tools:
        reasons.append(DenyReason.TOOL_NOT_IN_POLICY)
    if pin not in grant.tools:
        reasons.append(DenyReason.TOOL_NOT_GRANTED)
    if manifest.kind not in PHASE_1_TOOL_KINDS:
        reasons.append(DenyReason.TOOL_KIND_NOT_ALLOWED)
    if manifest.destinations or descriptor.destinations:
        reasons.append(DenyReason.EXTERNAL_COMMUNICATION)
    if manifest.sandbox_profile is not None or descriptor.sandbox_profile_hash is not None:
        reasons.append(DenyReason.SANDBOX_NOT_AVAILABLE)
    if descriptor.effect_class is not manifest.effect_class:
        reasons.append(DenyReason.DESCRIPTOR_MISMATCH)
    basis = descriptor.cost_basis
    if not isinstance(basis, ToolCostBasis) or basis.estimate != manifest.cost_estimate:
        # The required amount is the trusted manifest's estimate (WP09-R01).
        reasons.append(DenyReason.DESCRIPTOR_MISMATCH)
    for resource in descriptor.target_resources:
        declared = [s for s in manifest.resources if s.access is resource.access]
        if not any_match(tuple(s.path_pattern for s in declared), resource.path):
            reasons.append(DenyReason.RESOURCE_OUTSIDE_TOOL)
        scope = grant.files.write if resource.access is ResourceAccess.WRITE else grant.files.read
        if not any_match(scope, resource.path):
            reasons.append(DenyReason.RESOURCE_OUTSIDE_GRANT)
        denied = policy.deny_resources + context.denied_resources
        if any_match(denied, resource.path) or resource.path in denied:
            reasons.append(DenyReason.RESOURCE_DENIED)
    if descriptor.target_resources and (
        descriptor.workspace_ref is None
        or descriptor.workspace_ref not in policy.trusted_synthetic_workspaces
    ):
        # A "synthetic" label is not trust: only an owner-pinned synthetic
        # workspace counts (C2 4.1 minimal SAG; signed trees come later).
        reasons.append(DenyReason.UNTRUSTED_WORKSPACE)
    return reasons


def _model_reasons(
    policy: Policy, grant: RunGrant, descriptor: ActionDescriptor
) -> list[DenyReason]:
    model = descriptor.model
    if model is None:
        return [DenyReason.UNKNOWN_ACTION]
    reasons: list[DenyReason] = []
    if model.provider not in PHASE_1_MODEL_PROVIDERS:
        reasons.append(DenyReason.LIVE_PROVIDER_FORBIDDEN)
    elif model.provider not in policy.model_providers:
        reasons.append(DenyReason.EFFECT_NOT_ALLOWED)
    if model.profile not in grant.model_profiles:
        reasons.append(DenyReason.MODEL_NOT_GRANTED)
    basis = descriptor.cost_basis
    if not isinstance(basis, ModelCostBasis) or (
        basis.price_table_id,
        basis.price_table_version,
    ) != (policy.price_table_id, policy.price_table_version):
        # Only the owner's price table named by the policy prices a call (WP09-R01).
        reasons.append(DenyReason.COST_BASIS_UNTRUSTED)
    return reasons


def decide(
    *,
    policy: Policy,
    grant: RunGrant,
    descriptor: ActionDescriptor,
    registration: ToolRegistration | None,
    context: SafetyContext,
) -> PolicyDecision:
    for value, kind in (
        (policy, Policy),
        (grant, RunGrant),
        (descriptor, ActionDescriptor),
        (context, SafetyContext),
    ):
        if type(value) is not kind:
            raise PolicyDeniedError(DenyReason.UNKNOWN_ACTION)
    if registration is not None and type(registration) is not ToolRegistration:
        raise PolicyDeniedError(DenyReason.UNKNOWN_ACTION)
    reasons: list[DenyReason] = []
    tenant = descriptor.tenant_id
    if not tenant == policy.tenant_id == grant.tenant_id == context.tenant_id:
        reasons.append(DenyReason.TENANT_MISMATCH)
    if (grant.task_id, grant.run_id) != (descriptor.task_id, descriptor.run_id):
        reasons.append(DenyReason.GRANT_SCOPE_MISMATCH)
    if descriptor.hash_scheme is not HashScheme.SYNTHETIC_SHA256:
        reasons.append(DenyReason.HASH_SCHEME_NOT_ALLOWED)
    if (
        grant.synthetic_task_ref is None
        or grant.synthetic_task_ref not in policy.trusted_synthetic_tasks
    ):
        # P-6 (F07-3): only a Run of a synthetic task definition the owner's
        # current policy trusts. The ref comes from the owner-installed grant.
        reasons.append(DenyReason.SYNTHETIC_TASK_UNTRUSTED)
    if context.now_ms >= grant.expires_at_ms or context.now_ms < grant.issued_at_ms:
        reasons.append(DenyReason.GRANT_EXPIRED)
    if descriptor.expires_at_ms <= context.now_ms:
        reasons.append(DenyReason.DESCRIPTOR_EXPIRED)
    if descriptor.expires_at_ms > grant.expires_at_ms:
        reasons.append(DenyReason.DESCRIPTOR_OUTLIVES_GRANT)
    versions = {
        descriptor.policy_version,
        grant.policy_version,
        policy.policy_version,
        context.policy_version,
    }
    if len(versions) != 1:
        reasons.append(DenyReason.STALE_POLICY)
    epochs = {descriptor.access_epoch, grant.access_epoch_at_grant, context.access_epoch}
    if len(epochs) != 1:
        reasons.append(DenyReason.STALE_ACCESS_EPOCH)
    if context.runtime_owner_epoch != context.journal_owner_epoch:
        reasons.append(DenyReason.STALE_OWNER)
    if descriptor.effect_class not in policy.allowed_effects:
        reasons.append(DenyReason.EFFECT_NOT_ALLOWED)
    cap = descriptor.cost_cap
    # Budget has one currency in Phase 1 (XTU); the type rejects any other.
    if cap.micro_units > grant.max_cost_per_action.micro_units:
        reasons.append(DenyReason.COST_CAP_EXCEEDS_GRANT)
    if descriptor.kind is ActionKind.TOOL_CALL:
        reasons.extend(_tool_reasons(policy, grant, descriptor, registration, context))
    elif registration is not None:
        reasons.append(DenyReason.DESCRIPTOR_MISMATCH)
    else:
        reasons.extend(_model_reasons(policy, grant, descriptor))
    reasons.extend(stop_reasons(context, descriptor))
    unique = tuple(dict.fromkeys(reasons))
    return PolicyDecision(
        decision_version=1,
        allowed=not unique,
        reasons=unique,
        action_hash=descriptor.action_hash(),
        policy_hash=policy.policy_hash(),
        grant_hash=grant.grant_hash(),
        policy_version=policy.policy_version,
        access_epoch=context.access_epoch,
        approval_required=_approval_required(policy, descriptor, registration),
        decided_at_ms=context.now_ms,
    )


def require_allowed(decision: PolicyDecision) -> PolicyDecision:
    if not decision.allowed:
        raise PolicyDeniedError(*decision.reasons)
    return decision
