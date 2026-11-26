"""Trusted ActionDescriptor builder (WP-09A; C1 6.2 step 3, 7.1; E 38.1).

The descriptor is computed from the actual operation, never from hashes a
model or a proposal declares:

- Tool calls: the manifest and artifact hashes come from the trusted
  ``ToolRegistration`` (the registry's owner-approved entry). The argument hash
  is recomputed from the canonical input bytes, which must already be in the
  canonical form. A ``ToolProposal``, if given, must agree with all of these; its
  own hashes are compared, never used.
- Model calls: ``request_hash`` is recomputed from the request record bytes.

The ``reservation_id`` is allocated before the descriptor is fixed (AUDIT-01)
and is part of the hash, as are the Attempt and the cost cap: changing any of
them changes ``action_hash`` and voids every approval for it.
"""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from agent_core.safety.paths import matches
from agent_core.types.errors import ActionDescriptorError
from agent_core.types.ids import AttemptId, ReservationId, RunId, StepId, TaskId, TenantId
from agent_core.types.model import request_hash
from agent_core.types.safety import (
    ActionDescriptor,
    ActionKind,
    HashScheme,
    ModelTarget,
    TargetResource,
    ToolPin,
)
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.tools import (
    ToolEffectClass,
    ToolProposal,
    ToolRegistration,
    canonical_json,
    input_digest,
)
from agent_core.types.values import Budget, Currency

_MODEL_ARGS_DOMAIN = b"agent-core/model-call-args/v1\x00"


class AttemptRef:
    """The Task-Run-Step-Attempt chain the operation belongs to (from the runtime state)."""

    __slots__ = ("attempt_id", "run_id", "step_id", "task_id", "tenant_id")

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        task_id: TaskId,
        run_id: RunId,
        step_id: StepId,
        attempt_id: AttemptId,
    ) -> None:
        self.tenant_id = tenant_id
        self.task_id = task_id
        self.run_id = run_id
        self.step_id = step_id
        self.attempt_id = attempt_id


def pin_of(registration: ToolRegistration) -> ToolPin:
    manifest = registration.manifest
    return ToolPin(
        tool_id=manifest.tool_id,
        version=manifest.version,
        manifest_hash=manifest.manifest_hash,
        artifact_hash=registration.artifact_hash,
    )


def _canonical_input(canonical_input: str) -> bytes:
    if type(canonical_input) is not str:
        raise ActionDescriptorError("ARGS_NOT_CANONICAL")
    try:
        data = canonical_input.encode("utf-8")
        parsed = parse_strict_json(data)
    except (UnicodeEncodeError, StrictJsonError):
        raise ActionDescriptorError("ARGS_NOT_CANONICAL") from None
    if not isinstance(parsed, dict) or canonical_json(parsed) != data:
        raise ActionDescriptorError("ARGS_NOT_CANONICAL")
    return data


def _check_cost(cost_cap: Budget) -> None:
    if type(cost_cap) is not Budget or cost_cap.currency is not Currency.XTU:
        raise ActionDescriptorError("COST_CAP_INVALID")


def tool_action(
    *,
    registration: ToolRegistration,
    canonical_input: str,
    proposal: ToolProposal | None,
    attempt: AttemptRef,
    resources: tuple[TargetResource, ...],
    workspace_ref: str | None,
    cost_cap: Budget,
    reservation_id: ReservationId,
    policy_version: str,
    access_epoch: int,
    expires_at_ms: int,
) -> ActionDescriptor:
    if type(registration) is not ToolRegistration:
        raise ActionDescriptorError("REGISTRATION_REQUIRED")
    try:
        registration = ToolRegistration.model_validate(registration)
    except ValidationError:
        raise ActionDescriptorError("REGISTRATION_INVALID") from None
    data = _canonical_input(canonical_input)
    pin = pin_of(registration)
    manifest = registration.manifest
    if proposal is not None:
        if type(proposal) is not ToolProposal:
            raise ActionDescriptorError("PROPOSAL_INVALID")
        claimed = (
            proposal.attempt_id,
            proposal.tool_id,
            proposal.version,
            proposal.manifest_hash,
            proposal.artifact_hash,
            proposal.effect_class,
            proposal.canonical_input,
            proposal.input_hash,
        )
        actual = (
            attempt.attempt_id,
            pin.tool_id,
            pin.version,
            pin.manifest_hash,
            pin.artifact_hash,
            manifest.effect_class,
            canonical_input,
            input_digest(data),
        )
        if claimed != actual:
            raise ActionDescriptorError("PROPOSAL_MISMATCH")
    for resource in resources:
        declared = [
            spec
            for spec in manifest.resources
            if spec.access is resource.access and matches(spec.path_pattern, resource.path)
        ]
        if not declared:
            raise ActionDescriptorError("RESOURCE_NOT_DECLARED")
    _check_cost(cost_cap)
    try:
        return ActionDescriptor(
            descriptor_version=1,
            hash_scheme=HashScheme.SYNTHETIC_SHA256,
            kind=ActionKind.TOOL_CALL,
            tenant_id=attempt.tenant_id,
            task_id=attempt.task_id,
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            tool=pin,
            model=None,
            sandbox_profile_hash=manifest.sandbox_profile,
            args_hash=input_digest(data),
            target_resources=tuple(resources),
            destinations=(),
            request_hash=None,
            effect_class=manifest.effect_class,
            workspace_ref=workspace_ref,
            policy_version=policy_version,
            access_epoch=access_epoch,
            cost_cap=cost_cap,
            reservation_id=reservation_id,
            expires_at_ms=expires_at_ms,
        )
    except ValidationError:
        raise ActionDescriptorError("DESCRIPTOR_INVALID") from None


def model_action(
    *,
    model: ModelTarget,
    request_record: bytes,
    attempt: AttemptRef,
    cost_cap: Budget,
    reservation_id: ReservationId,
    policy_version: str,
    access_epoch: int,
    expires_at_ms: int,
) -> ActionDescriptor:
    """A model call (Fake or Replay in Phase 1). ``request_record`` is the exact
    request record bytes; its hash is computed here."""
    if type(request_record) is not bytes or not request_record:
        raise ActionDescriptorError("REQUEST_RECORD_REQUIRED")
    _check_cost(cost_cap)
    try:
        return ActionDescriptor(
            descriptor_version=1,
            hash_scheme=HashScheme.SYNTHETIC_SHA256,
            kind=ActionKind.MODEL_CALL,
            tenant_id=attempt.tenant_id,
            task_id=attempt.task_id,
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            tool=None,
            model=model,
            sandbox_profile_hash=None,
            args_hash="sha256:" + hashlib.sha256(_MODEL_ARGS_DOMAIN + request_record).hexdigest(),
            target_resources=(),
            destinations=(),
            request_hash=request_hash(request_record),
            effect_class=ToolEffectClass.MODEL_CALL,
            workspace_ref=None,
            policy_version=policy_version,
            access_epoch=access_epoch,
            cost_cap=cost_cap,
            reservation_id=reservation_id,
            expires_at_ms=expires_at_ms,
        )
    except ValidationError:
        raise ActionDescriptorError("DESCRIPTOR_INVALID") from None
