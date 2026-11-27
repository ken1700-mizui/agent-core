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

WP09-R01/R06:

- Targets are derived from the arguments the manifest marks as resource
  arguments (``FieldSpec.resource``); no caller states them. A marked argument
  that is missing or is not a relative path refuses the descriptor.
- The cost basis is derived too: a tool's manifest ``cost_estimate``, or for a
  model call the owner's price table named by the policy, the model, and the
  ceilings verified from the request record and the rendered input it names.
- ``rebuild`` builds the descriptor again from its content; the ledger uses it
  to refuse a descriptor that was not built from that content.

WP-08 (descriptor version 3): a write's ApplyPlan (``WriteChange``) is derived
from the arguments the manifest marks (``FieldSpec.apply``): the target, the
operation (``absent`` -> CREATE, a file digest -> REPLACE with that expected
state) and the digest and size of the content. It is part of ``action_hash``.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import ValidationError

from agent_core.safety.paths import matches
from agent_core.types.errors import ActionDescriptorError
from agent_core.types.files import MANIFEST_FILE_NAME, TEMP_FILE_PREFIX
from agent_core.types.ids import AttemptId, ReservationId, RunId, StepId, TaskId, TenantId
from agent_core.types.model import rendering_hash, request_hash
from agent_core.types.replay import ReplayRequestRecord
from agent_core.types.safety import (
    ActionContent,
    ActionDescriptor,
    ActionKind,
    HashScheme,
    ModelCostBasis,
    ModelTarget,
    TargetResource,
    ToolContent,
    ToolCostBasis,
    ToolPin,
    WriteChange,
    WriteOp,
)
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.tools import (
    ApplyField,
    ResourceAccess,
    ToolEffectClass,
    ToolProposal,
    ToolRegistration,
    canonical_json,
    input_digest,
)
from agent_core.types.values import Budget, Currency

_MODEL_ARGS_DOMAIN = b"agent-core/model-call-args/v1\x00"
_ABSENT = "absent"
_FILE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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
    """The input's UTF-8 bytes, which must be canonical JSON of an object."""
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


def tool_targets(
    registration: ToolRegistration, data: dict[str, object]
) -> tuple[TargetResource, ...]:
    """The operation's resources, from the arguments marked in the input schema."""
    manifest = registration.manifest
    targets: list[TargetResource] = []
    for field in manifest.input_schema.fields:
        if field.resource is None:
            continue
        value = data.get(field.name)
        if type(value) is not str:
            raise ActionDescriptorError("RESOURCE_ARGUMENT_MISSING")
        try:
            target = TargetResource(path=value, access=field.resource)
        except ValidationError:
            raise ActionDescriptorError("RESOURCE_PATH_INVALID") from None
        if any(
            part == MANIFEST_FILE_NAME or part.startswith(TEMP_FILE_PREFIX)
            for part in target.path.split("/")
        ):
            # WP08-R01: the manifest and the broker's temporary files are never targets.
            raise ActionDescriptorError("RESOURCE_PATH_RESERVED")
        declared = [
            spec
            for spec in manifest.resources
            if spec.access is target.access and matches(spec.path_pattern, target.path)
        ]
        if not declared:
            raise ActionDescriptorError("RESOURCE_NOT_DECLARED")
        if target not in targets:
            targets.append(target)
    return tuple(targets)


def write_change(registration: ToolRegistration, data: dict[str, object]) -> WriteChange | None:
    """The write's ApplyPlan from the marked arguments, or None for a tool that
    does not write. The manifest guarantees one target, one content and one
    expected-state argument for every write tool."""
    fields = registration.manifest.input_schema.fields
    target = [f for f in fields if f.resource is ResourceAccess.WRITE]
    if not target:
        return None
    by_role = {f.apply: f for f in fields if f.apply is not None}
    path = data.get(target[0].name)
    content = data.get(by_role[ApplyField.CONTENT].name)
    expected = data.get(by_role[ApplyField.EXPECTED_STATE].name)
    if type(path) is not str or type(content) is not str or type(expected) is not str:
        raise ActionDescriptorError("APPLY_ARGUMENT_MISSING")
    if expected == _ABSENT:
        op, pre = WriteOp.CREATE, None
    elif _FILE_DIGEST.fullmatch(expected) is not None:
        op, pre = WriteOp.REPLACE, expected
    else:
        raise ActionDescriptorError("EXPECTED_STATE_INVALID")
    try:
        body = content.encode("utf-8")
    except UnicodeEncodeError:
        raise ActionDescriptorError("APPLY_CONTENT_INVALID") from None
    try:
        return WriteChange(
            op=op,
            path=path,
            expected_pre=pre,
            content_digest="sha256:" + hashlib.sha256(body).hexdigest(),
            content_size=len(body),
        )
    except ValidationError:
        raise ActionDescriptorError("APPLY_CHANGE_INVALID") from None


def tool_action(
    *,
    registration: ToolRegistration,
    canonical_input: str,
    proposal: ToolProposal | None,
    attempt: AttemptRef,
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
    parsed = parse_strict_json(data)
    assert isinstance(parsed, dict)  # noqa: S101 (checked by _canonical_input)
    targets = tool_targets(registration, parsed)
    change = write_change(registration, parsed)
    _check_cost(cost_cap)
    try:
        return ActionDescriptor(
            descriptor_version=3,
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
            target_resources=targets,
            destinations=(),
            request_hash=None,
            effect_class=manifest.effect_class,
            workspace_ref=workspace_ref,
            policy_version=policy_version,
            access_epoch=access_epoch,
            cost_cap=cost_cap,
            cost_basis=ToolCostBasis(kind="tool", estimate=manifest.cost_estimate),
            reservation_id=reservation_id,
            expires_at_ms=expires_at_ms,
            apply_change=change,
        )
    except ValidationError:
        raise ActionDescriptorError("DESCRIPTOR_INVALID") from None


def model_action(
    *,
    model: ModelTarget,
    request_record: bytes,
    rendered_input: bytes,
    price_table_id: str,
    price_table_version: str,
    attempt: AttemptRef,
    cost_cap: Budget,
    reservation_id: ReservationId,
    policy_version: str,
    access_epoch: int,
    expires_at_ms: int,
) -> ActionDescriptor:
    """A model call (Fake or Replay in Phase 1). ``request_record`` is the exact
    request record (``ReplayRequestRecord``, version 1) and ``rendered_input`` the
    rendered input it names; both hashes and the ceilings are computed here."""
    if type(request_record) is not bytes or type(rendered_input) is not bytes:
        raise ActionDescriptorError("REQUEST_RECORD_REQUIRED")
    if not request_record or not rendered_input:
        raise ActionDescriptorError("REQUEST_RECORD_REQUIRED")
    try:
        parse_strict_json(request_record)
        record = ReplayRequestRecord.model_validate_json(request_record)
    except (StrictJsonError, ValidationError):
        raise ActionDescriptorError("REQUEST_RECORD_INVALID") from None
    if record.rendering_hash != rendering_hash(rendered_input):
        raise ActionDescriptorError("RENDERED_INPUT_MISMATCH")
    if type(model) is not ModelTarget or record.profile != model.profile:
        raise ActionDescriptorError("MODEL_MISMATCH")
    _check_cost(cost_cap)
    try:
        basis = ModelCostBasis(
            kind="model",
            price_table_id=price_table_id,
            price_table_version=price_table_version,
            model_id=model.profile.model_id,
            max_input_tokens=len(rendered_input),
            max_output_tokens=record.params.max_output_tokens,
        )
        return ActionDescriptor(
            descriptor_version=3,
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
            cost_basis=basis,
            reservation_id=reservation_id,
            expires_at_ms=expires_at_ms,
            apply_change=None,
        )
    except ValidationError:
        raise ActionDescriptorError("DESCRIPTOR_INVALID") from None


def rebuild(
    descriptor: ActionDescriptor,
    content: ActionContent,
    registration: ToolRegistration | None,
) -> ActionDescriptor:
    """The descriptor built again from ``content`` with ``descriptor``'s free values
    (attempt chain, workspace, cost cap, reservation, epochs, expiry, model). The
    caller compares the result with ``descriptor``."""
    attempt = AttemptRef(
        tenant_id=descriptor.tenant_id,
        task_id=descriptor.task_id,
        run_id=descriptor.run_id,
        step_id=descriptor.step_id,
        attempt_id=descriptor.attempt_id,
    )
    common = {
        "attempt": attempt,
        "cost_cap": descriptor.cost_cap,
        "reservation_id": descriptor.reservation_id,
        "policy_version": descriptor.policy_version,
        "access_epoch": descriptor.access_epoch,
        "expires_at_ms": descriptor.expires_at_ms,
    }
    if isinstance(content, ToolContent):
        if registration is None:
            raise ActionDescriptorError("REGISTRATION_REQUIRED")
        return tool_action(
            registration=registration,
            canonical_input=content.canonical_input,
            proposal=None,
            workspace_ref=descriptor.workspace_ref,
            **common,  # type: ignore[arg-type]
        )
    if descriptor.model is None:
        raise ActionDescriptorError("CONTENT_INVALID")
    basis = descriptor.cost_basis
    if not isinstance(basis, ModelCostBasis):
        raise ActionDescriptorError("CONTENT_INVALID")
    return model_action(
        model=descriptor.model,
        request_record=content.request_record.encode("utf-8"),
        rendered_input=content.rendered_input.encode("utf-8"),
        price_table_id=basis.price_table_id,
        price_table_version=basis.price_table_version,
        **common,  # type: ignore[arg-type]
    )
