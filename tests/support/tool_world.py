"""Synthetic tool manifests and trusted catalog entries for WP-07 tests."""

from __future__ import annotations

from typing import Any

from agent_core.tool.registry import TrustedToolCatalog, TrustedToolEntry
from agent_core.types.tools import (
    ApplyField,
    ApprovalCondition,
    ExternalGuarantee,
    FieldSpec,
    FieldType,
    ObjectSchema,
    ResourceAccess,
    ResourceSpec,
    ToolEffectClass,
    ToolKind,
    ToolManifest,
    ToolManifestBody,
    ToolPermission,
    canonical_json,
)
from agent_core.types.values import Budget, Currency
from support import builders as b

ARTIFACT = b.digest("agent-core-release-synthetic")


def body(**overrides: Any) -> ToolManifestBody:
    values: dict[str, Any] = {
        "tool_id": "read_file",
        "version": "1.0.0",
        "kind": ToolKind.BUILTIN,
        "input_schema": ObjectSchema(
            fields=(
                FieldSpec(
                    name="path",
                    type=FieldType.STRING,
                    required=True,
                    max_length=256,
                    resource=ResourceAccess.READ,
                ),
                FieldSpec(name="max_bytes", type=FieldType.INTEGER, required=False, maximum=65_536),
            ),
            max_bytes=1_024,
        ),
        "output_schema": ObjectSchema(
            fields=(
                FieldSpec(name="content", type=FieldType.STRING, required=True, max_length=65_536),
                FieldSpec(name="truncated", type=FieldType.BOOLEAN, required=True),
            ),
            max_bytes=70_000,
        ),
        "effect_class": ToolEffectClass.READ_ONLY,
        "external_guarantee": ExternalGuarantee.NONE,
        "required_permissions": (ToolPermission.FILE_READ,),
        "resources": (ResourceSpec(path_pattern="workspace/**", access=ResourceAccess.READ),),
        "destinations": (),
        "timeout_ms": 5_000,
        "max_output_bytes": 70_000,
        "cost_estimate": Budget(currency=Currency.XTU, micro_units=0),
        "approval_conditions": (),
        "sandbox_profile": None,
    }
    values.update(overrides)
    return ToolManifestBody(**values)


def manifest(**overrides: Any) -> ToolManifest:
    return ToolManifest.seal(body(**overrides))


def entry(sealed: ToolManifest, artifact_hash: str = ARTIFACT) -> TrustedToolEntry:
    return TrustedToolEntry(
        tool_id=sealed.tool_id,
        version=sealed.version,
        manifest_hash=sealed.manifest_hash,
        artifact_hash=artifact_hash,
        approval_ref=b.digest("owner-approval"),
    )


def catalog(*sealed: ToolManifest) -> TrustedToolCatalog:
    return TrustedToolCatalog(entries=tuple(entry(s) for s in sealed))


def input_schema(access: ResourceAccess) -> ObjectSchema:
    """The default input schema, its ``path`` argument naming a resource with ``access``."""
    return ObjectSchema(
        fields=(
            FieldSpec(
                name="path", type=FieldType.STRING, required=True, max_length=256, resource=access
            ),
            FieldSpec(name="max_bytes", type=FieldType.INTEGER, required=False, maximum=65_536),
        ),
        max_bytes=1_024,
    )


def write_schema() -> ObjectSchema:
    """A write tool's input: the target, the content and the expected state (WP-08)."""
    return ObjectSchema(
        fields=(
            FieldSpec(
                name="content",
                type=FieldType.STRING,
                required=True,
                max_length=65_536,
                apply=ApplyField.CONTENT,
            ),
            FieldSpec(
                name="expected",
                type=FieldType.STRING,
                required=True,
                max_length=71,
                apply=ApplyField.EXPECTED_STATE,
            ),
            FieldSpec(
                name="path",
                type=FieldType.STRING,
                required=True,
                max_length=256,
                resource=ResourceAccess.WRITE,
            ),
        ),
        max_bytes=70_000,
    )


def write_input(path: str, *, content: str = "synthetic", expected: str = "absent") -> str:
    """Canonical arguments of the write tool: ``expected`` is ``absent`` (create) or
    the sha256 digest of the file being replaced."""
    return canonical_json({"content": content, "expected": expected, "path": path}).decode()


WRITE_BODY: dict[str, Any] = {
    "tool_id": "write_file",
    "input_schema": write_schema(),
    "effect_class": ToolEffectClass.IRREVERSIBLE,
    "required_permissions": (ToolPermission.FILE_WRITE,),
    "resources": (ResourceSpec(path_pattern="workspace/**", access=ResourceAccess.WRITE),),
    "approval_conditions": (ApprovalCondition.ON_WRITE,),
}


def synthetic_body(tool_id: str, **overrides: Any) -> dict[str, Any]:
    """A synthetic operation with no file target (WP08-R06): the gate settles it
    with start and finish; it never touches the work tree."""
    values: dict[str, Any] = {
        "tool_id": tool_id,
        "input_schema": ObjectSchema(
            fields=(FieldSpec(name="value", type=FieldType.STRING, required=True, max_length=256),),
            max_bytes=1_024,
        ),
        "required_permissions": (),
        "resources": (),
    }
    values.update(overrides)
    return values


SYNTHETIC_INPUT = '{"value":"synthetic"}'

__all__ = ["ApprovalCondition", "ResourceAccess", "ResourceSpec"]
