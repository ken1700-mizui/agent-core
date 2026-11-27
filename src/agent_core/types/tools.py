"""Tool types (WP-07; C1 5.1-5.3, A 3.2).

- ``ToolManifestBody`` holds the declared fields of C1 5.1. ``ToolManifest``
  adds ``manifest_hash``, recomputed and compared on every validation, so a
  changed field is detected.
- ``manifest_hash`` fixes the declaration; ``artifact_hash`` fixes the code that
  would run. They are different values, both pinned by the owner's trusted
  registration entry (C1 5.3, C1.S23).
- Schemas are a small closed subset (object of typed fields). Anything the
  subset cannot express is rejected by validation, never silently ignored.
- ``ToolProposal`` is validated data about a proposed call. It is not a Permit
  and grants nothing; execution needs Safety and a Permit (WP-09).

The hashes here are unkeyed SHA-256 for synthetic data only (WP05-R03).
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import Field, StringConstraints, field_validator, model_validator
from pydantic_core import PydanticCustomError

from agent_core.types.base import FrozenModel, ImmutableRecord
from agent_core.types.ids import AttemptId
from agent_core.types.values import Budget, Digest, Identifier, SemVer, require_sorted_unique

MANIFEST_HASH_DOMAIN = b"agent-core/tool-manifest/v1\x00"
TOOL_INPUT_HASH_DOMAIN = b"agent-core/tool-input/v1\x00"
TOOL_OUTPUT_HASH_DOMAIN = b"agent-core/tool-output/v1\x00"
MAX_SCHEMA_BYTES = 10_000_000
MAX_FIELDS = 64

ToolId = Identifier
ToolVersion = SemVer
DestinationId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")
]
_PATH_PATTERN = re.compile(r"[A-Za-z0-9._*-]+(?:/[A-Za-z0-9._*-]+)*")


class ToolKind(StrEnum):
    BUILTIN = "BUILTIN"
    SANDBOXED_COMMAND = "SANDBOXED_COMMAND"
    MCP = "MCP"
    MODEL_CALL = "MODEL_CALL"


class ToolEffectClass(StrEnum):
    """A 3.2."""

    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    COMPENSATABLE = "COMPENSATABLE"
    IRREVERSIBLE = "IRREVERSIBLE"
    MODEL_CALL = "MODEL_CALL"


class ExternalGuarantee(StrEnum):
    """What the external service declares it guarantees (A08). Until verified, the
    runtime treats every declaration as NONE (C1 5.2)."""

    NONE = "NONE"
    IDEMPOTENCY_KEY = "IDEMPOTENCY_KEY"
    CONDITIONAL = "CONDITIONAL"


class ToolPermission(StrEnum):
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"


class ResourceAccess(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


class ApplyField(StrEnum):
    """WP-08: an argument that fixes part of a write's ApplyPlan (``WriteChange``)."""

    # The full text to write (UTF-8).
    CONTENT = "CONTENT"
    # ``absent`` (the target must not exist: create) or the sha256 digest of the
    # file the write replaces.
    EXPECTED_STATE = "EXPECTED_STATE"


class ApprovalCondition(StrEnum):
    ALWAYS = "ALWAYS"
    ON_WRITE = "ON_WRITE"
    ON_IRREVERSIBLE = "ON_IRREVERSIBLE"


class FieldType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


class FieldSpec(FrozenModel):
    name: Identifier
    type: FieldType
    required: bool
    max_length: Annotated[int, Field(strict=True, ge=1, le=MAX_SCHEMA_BYTES)] | None = None
    minimum: Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)] | None = None
    maximum: Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)] | None = None
    # WP09-R06: this argument names a resource the tool touches, with this access.
    # The trusted descriptor builder derives the operation's targets from these
    # arguments; a caller cannot state the targets itself.
    resource: ResourceAccess | None = None
    # WP-08: this argument is part of the write's ApplyPlan. The trusted descriptor
    # builder derives the ``WriteChange`` from it.
    apply: ApplyField | None = None

    @model_validator(mode="after")
    def _constraints_fit_type(self) -> Self:
        if self.resource is not None and (self.type is not FieldType.STRING or not self.required):
            raise ValueError("a resource argument is a required STRING field")
        if self.apply is not None and (self.type is not FieldType.STRING or not self.required):
            raise ValueError("an ApplyPlan argument is a required STRING field")
        if self.apply is not None and self.resource is not None:
            raise ValueError("an argument is either a resource or part of the ApplyPlan")
        if self.max_length is not None and self.type is not FieldType.STRING:
            raise ValueError("max_length applies to STRING fields only")
        if (self.minimum is not None or self.maximum is not None) and (
            self.type is not FieldType.INTEGER
        ):
            raise ValueError("minimum and maximum apply to INTEGER fields only")
        if self.type is FieldType.STRING and self.max_length is None:
            raise ValueError("STRING fields need max_length (sizes are always bounded)")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        return self


class ObjectSchema(FrozenModel):
    """A JSON object with exactly the declared fields (no others)."""

    fields: Annotated[tuple[FieldSpec, ...], Field(max_length=MAX_FIELDS)]
    max_bytes: Annotated[int, Field(strict=True, ge=2, le=MAX_SCHEMA_BYTES)]

    @field_validator("fields")
    @classmethod
    def _unique_names(cls, value: tuple[FieldSpec, ...]) -> tuple[FieldSpec, ...]:
        names = [f.name for f in value]
        if len(set(names)) != len(names):
            raise ValueError("field names must be unique")
        return value


class ResourceSpec(FrozenModel):
    """A relative path pattern inside the managed work tree, and the access."""

    path_pattern: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
    access: ResourceAccess

    @field_validator("path_pattern")
    @classmethod
    def _contained(cls, value: str) -> str:
        if _PATH_PATTERN.fullmatch(value) is None or ".." in value.split("/"):
            raise ValueError("resource patterns are relative paths without '..'")
        return value


def _canonical(data: Any) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _digest(domain: bytes, data: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + data).hexdigest()


class ToolManifestBody(FrozenModel):
    """C1 5.1. Registered by the owner; never trusted from a model or a caller."""

    tool_id: ToolId
    version: ToolVersion
    kind: ToolKind
    input_schema: ObjectSchema
    output_schema: ObjectSchema
    effect_class: ToolEffectClass
    external_guarantee: ExternalGuarantee
    required_permissions: tuple[ToolPermission, ...]
    resources: tuple[ResourceSpec, ...]
    destinations: tuple[DestinationId, ...]
    timeout_ms: Annotated[int, Field(strict=True, ge=1, le=600_000)]
    max_output_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_SCHEMA_BYTES)]
    cost_estimate: Budget
    approval_conditions: tuple[ApprovalCondition, ...]
    sandbox_profile: Digest | None

    @field_validator("required_permissions", "approval_conditions", "destinations")
    @classmethod
    def _canonical_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_sorted_unique(value, "items")
        return value

    @model_validator(mode="after")
    def _effects_fit_declaration(self) -> Self:
        writes = any(r.access is ResourceAccess.WRITE for r in self.resources)
        reads = any(r.access is ResourceAccess.READ for r in self.resources)
        permissions = set(self.required_permissions)
        # Declared accesses and permissions must agree in both directions (WP07-R02).
        if writes != (ToolPermission.FILE_WRITE in permissions):
            raise ValueError("WRITE resources and the FILE_WRITE permission go together")
        if reads != (ToolPermission.FILE_READ in permissions):
            raise ValueError("READ resources and the FILE_READ permission go together")
        if self.effect_class is ToolEffectClass.READ_ONLY and writes:
            raise ValueError("a READ_ONLY tool cannot declare writes")
        if (self.effect_class is ToolEffectClass.MODEL_CALL) != (self.kind is ToolKind.MODEL_CALL):
            raise ValueError("the MODEL_CALL effect belongs to MODEL_CALL tools only")
        marked = {f.resource for f in self.input_schema.fields if f.resource is not None}
        for access in {r.access for r in self.resources}:
            if access not in marked:
                # Without a resource argument the targets could not be derived (WP09-R06).
                raise ValueError("each declared resource access needs a resource argument")
        fields = self.input_schema.fields
        write_targets = [f for f in fields if f.resource is ResourceAccess.WRITE]
        applies = [f.apply for f in fields if f.apply is not None]
        if writes:
            # WP-08: one write target, and exactly one content and one expected-state
            # argument, so the ApplyPlan of every call can be derived.
            if len(write_targets) != 1 or sorted(applies) != sorted(ApplyField):
                raise ValueError(
                    "a write tool has one write target, one content and one expected state"
                )
        elif applies:
            raise ValueError("ApplyPlan arguments belong to write tools only")
        return self

    @property
    def declares_writes(self) -> bool:
        return ToolPermission.FILE_WRITE in self.required_permissions

    def compute_hash(self) -> str:
        fields = ToolManifestBody.model_fields
        return _digest(
            MANIFEST_HASH_DOMAIN, _canonical(self.model_dump(mode="json", include=set(fields)))
        )


class ToolManifest(ToolManifestBody):
    manifest_hash: Digest

    @model_validator(mode="after")
    def _hash_matches(self) -> Self:
        if self.compute_hash() != self.manifest_hash:
            raise PydanticCustomError("manifest_hash_mismatch", "manifest_hash does not match")
        return self

    @classmethod
    def seal(cls, body: ToolManifestBody) -> ToolManifest:
        values = {name: getattr(body, name) for name in ToolManifestBody.model_fields}
        return cls.model_validate({**values, "manifest_hash": body.compute_hash()})


class ToolRegistration(ImmutableRecord):
    """A tool the registry accepted: its manifest and the pinned artifact hash."""

    manifest: ToolManifest
    artifact_hash: Digest
    approval_ref: Digest

    @property
    def effective_external_guarantee(self) -> ExternalGuarantee:
        """Phase 1 cannot verify a guarantee, so every declaration counts as NONE."""
        return ExternalGuarantee.NONE


class ToolProposal(ImmutableRecord):
    """Validated data about a proposed tool call. Not a Permit; it grants nothing."""

    attempt_id: AttemptId
    tool_id: ToolId
    version: ToolVersion
    manifest_hash: Digest
    artifact_hash: Digest
    effect_class: ToolEffectClass
    effective_external_guarantee: ExternalGuarantee
    requires_approval: bool
    canonical_input: Annotated[str, StringConstraints(strict=True, max_length=MAX_SCHEMA_BYTES)]
    input_hash: Digest


class ValidatedToolOutput(ImmutableRecord):
    canonical_output: Annotated[str, StringConstraints(strict=True, max_length=MAX_SCHEMA_BYTES)]
    output_hash: Digest


def input_digest(canonical: bytes) -> str:
    return _digest(TOOL_INPUT_HASH_DOMAIN, canonical)


def output_digest(canonical: bytes) -> str:
    return _digest(TOOL_OUTPUT_HASH_DOMAIN, canonical)


def canonical_json(data: Any) -> bytes:
    return _canonical(data)
