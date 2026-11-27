"""Tool Registry (WP-07; C1 5.1-5.3, C1.S8, C1.S23).

- ``TrustedToolCatalog`` is the owner-managed list of approved
  ``(tool_id, version, manifest_hash, artifact_hash)`` entries. The registry
  cannot add to it. In Phase 1 the owner provides it as configuration; the
  Policy Store with its approval chain comes later (C1 5.3).
- ``register`` accepts a manifest only if it is a builtin tool, uses no feature
  the Phase forbids, and both hashes equal a catalog entry. A hash that a
  model or caller merely declares registers nothing. A registered
  ``(tool_id, version)`` is never overwritten.
- ``propose`` validates input against the registered input schema and returns
  a ``ToolProposal``: data, not a Permit. ``validate_output`` checks output.
- The registry stores no callables: there is nothing to fetch and run.

What Phase 1 can verify about ``artifact_hash``: only that it equals the
owner's entry. That the running code really has this hash is checked by the
PEP at execution time (WP-08/WP-09, C1 5.3) and is not verified here.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Self

from pydantic import ValidationError, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.errors import (
    ToolAlreadyRegisteredError,
    ToolInputError,
    ToolKindNotAllowedError,
    ToolManifestError,
    ToolNotApprovedError,
    ToolOutputError,
    UnknownToolError,
    UnknownToolVersionError,
    ValidationIssue,
)
from agent_core.types.ids import AttemptId
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.tools import (
    ApprovalCondition,
    ExternalGuarantee,
    FieldType,
    ObjectSchema,
    ToolEffectClass,
    ToolKind,
    ToolManifest,
    ToolProposal,
    ToolRegistration,
    ToolVersion,
    ValidatedToolOutput,
    canonical_json,
    input_digest,
    output_digest,
)
from agent_core.types.values import Digest, Identifier

UNKNOWN_FIELD = "<unknown>"
# Raw text may carry whitespace beyond the canonical form, but only this much.
RAW_SIZE_FACTOR = 2
# Phase 1 allows builtin tools only (PHASE_1_IMPLEMENTATION_PLAN WP-07).
ALLOWED_KINDS = frozenset({ToolKind.BUILTIN})


class TrustedToolEntry(FrozenModel):
    tool_id: Identifier
    version: ToolVersion
    manifest_hash: Digest
    artifact_hash: Digest
    approval_ref: Digest


class TrustedToolCatalog(FrozenModel):
    entries: tuple[TrustedToolEntry, ...]

    @model_validator(mode="after")
    def _one_entry_per_version(self) -> Self:
        keys = [(e.tool_id, e.version) for e in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("one trusted entry per tool_id and version")
        return self

    def find(self, tool_id: str, version: str) -> TrustedToolEntry | None:
        for entry in self.entries:
            if entry.tool_id == tool_id and entry.version == version:
                return entry
        return None


def _check_phase(manifest: ToolManifest) -> None:
    if manifest.kind not in ALLOWED_KINDS:
        raise ToolKindNotAllowedError("only builtin tools are allowed in Phase 1")
    if manifest.destinations:
        raise ToolKindNotAllowedError("network destinations are not allowed in Phase 1")
    if manifest.sandbox_profile is not None:
        raise ToolKindNotAllowedError("sandbox profiles are not used in Phase 1")


def _requires_approval(manifest: ToolManifest) -> bool:
    """Derived from the declaration only: a hint for the PDP, never a decision."""
    conditions = set(manifest.approval_conditions)
    writes = manifest.declares_writes
    return (
        ApprovalCondition.ALWAYS in conditions
        or (ApprovalCondition.ON_WRITE in conditions and writes)
        or (
            ApprovalCondition.ON_IRREVERSIBLE in conditions
            and manifest.effect_class is ToolEffectClass.IRREVERSIBLE
        )
    )


def _validate_object(
    schema: ObjectSchema, raw: object, *, max_bytes: int | None = None
) -> tuple[bytes, list[ValidationIssue]]:
    """Validate ``raw`` JSON against ``schema``; issues never contain input values.

    Sizes are checked before parsing (WP07-R01): the raw text may use at most
    ``RAW_SIZE_FACTOR`` times the canonical limit, counted first in characters
    (cheap, and never more than the bytes) and then in UTF-8 bytes. After
    parsing, the canonical UTF-8 bytes must fit the limit. ``max_length`` of a
    string counts Unicode characters.
    """
    limit = schema.max_bytes if max_bytes is None else min(schema.max_bytes, max_bytes)
    raw_limit = limit * RAW_SIZE_FACTOR
    if type(raw) is not str:
        return b"", [ValidationIssue(location="$", type="string_required")]
    if len(raw) > raw_limit:
        return b"", [ValidationIssue(location="$", type="too_large")]
    try:
        encoded_size = len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        return b"", [ValidationIssue(location="$", type="json_invalid_string")]
    if encoded_size > raw_limit:
        return b"", [ValidationIssue(location="$", type="too_large")]
    issues: list[ValidationIssue] = []
    try:
        data: Any = parse_strict_json(raw)
    except StrictJsonError as exc:
        return b"", [ValidationIssue(location="$", type=exc.issue_type)]
    if not isinstance(data, dict):
        return b"", [ValidationIssue(location="$", type="object_required")]
    declared = {f.name: f for f in schema.fields}
    if any(key not in declared for key in data):
        issues.append(ValidationIssue(location=UNKNOWN_FIELD, type="extra_forbidden"))
    for name, spec in declared.items():
        if name not in data:
            if spec.required:
                issues.append(ValidationIssue(location=name, type="missing"))
            continue
        value = data[name]
        if spec.type is FieldType.STRING:
            if type(value) is not str:
                issues.append(ValidationIssue(location=name, type="string_type"))
            elif spec.max_length is not None and len(value) > spec.max_length:
                issues.append(ValidationIssue(location=name, type="string_too_long"))
        elif spec.type is FieldType.INTEGER:
            if type(value) is not int:
                issues.append(ValidationIssue(location=name, type="int_type"))
            elif not -(2**63) <= value <= 2**63 - 1:
                issues.append(ValidationIssue(location=name, type="int_out_of_range"))
            elif spec.minimum is not None and value < spec.minimum:
                issues.append(ValidationIssue(location=name, type="greater_than_equal"))
            elif spec.maximum is not None and value > spec.maximum:
                issues.append(ValidationIssue(location=name, type="less_than_equal"))
        elif type(value) is not bool:
            issues.append(ValidationIssue(location=name, type="bool_type"))
    canonical = canonical_json(data)
    if len(canonical) > limit:
        issues.append(ValidationIssue(location="$", type="too_large"))
    return canonical, issues


class ToolRegistry:
    def __init__(self, catalog: TrustedToolCatalog) -> None:
        try:
            # Re-validated: a catalog built without validation is not trusted.
            self._catalog = TrustedToolCatalog.model_validate(catalog)
        except ValidationError:
            raise ToolManifestError("trusted tool catalog is invalid") from None
        self._registrations: dict[tuple[str, str], ToolRegistration] = {}

    @property
    def registrations(self) -> MappingProxyType[tuple[str, str], ToolRegistration]:
        return MappingProxyType(self._registrations)

    def register(self, manifest: ToolManifest, *, artifact_hash: str) -> ToolRegistration:
        if type(manifest) is not ToolManifest:
            raise ToolNotApprovedError("a sealed ToolManifest is required")
        try:
            manifest = ToolManifest.model_validate(manifest)  # re-checks manifest_hash
        except ValidationError:
            raise ToolManifestError("tool manifest is invalid") from None
        _check_phase(manifest)
        entry = self._catalog.find(manifest.tool_id, manifest.version)
        if (
            entry is None
            or entry.manifest_hash != manifest.manifest_hash
            or entry.artifact_hash != artifact_hash
        ):
            raise ToolNotApprovedError("no owner-approved entry matches this manifest")
        registration = ToolRegistration(
            manifest=manifest, artifact_hash=artifact_hash, approval_ref=entry.approval_ref
        )
        key = (manifest.tool_id, manifest.version)
        existing = self._registrations.get(key)
        if existing is not None:
            if existing != registration:
                raise ToolAlreadyRegisteredError("this tool version is already registered")
            return existing
        self._registrations[key] = registration
        return registration

    def get(self, tool_id: str, version: str) -> ToolRegistration:
        registration = self._registrations.get((tool_id, version))
        if registration is not None:
            return registration
        if any(key[0] == tool_id for key in self._registrations):
            raise UnknownToolVersionError("this version of the tool is not registered")
        raise UnknownToolError("tool is not registered")

    def propose(
        self, *, tool_id: str, version: str, arguments: str, attempt_id: AttemptId
    ) -> ToolProposal:
        registration = self.get(tool_id, version)
        manifest = registration.manifest
        canonical, issues = _validate_object(manifest.input_schema, arguments)
        if issues:
            raise ToolInputError(tuple(issues))
        return ToolProposal(
            attempt_id=attempt_id,
            tool_id=manifest.tool_id,
            version=manifest.version,
            manifest_hash=manifest.manifest_hash,
            artifact_hash=registration.artifact_hash,
            effect_class=manifest.effect_class,
            effective_external_guarantee=ExternalGuarantee.NONE,
            requires_approval=_requires_approval(manifest),
            canonical_input=canonical.decode("utf-8"),
            input_hash=input_digest(canonical),
        )

    def validate_output(self, registration: ToolRegistration, output: str) -> ValidatedToolOutput:
        current = self.get(registration.manifest.tool_id, registration.manifest.version)
        if current != registration:
            raise ToolNotApprovedError("registration does not match the registry")
        canonical, issues = _validate_object(
            current.manifest.output_schema, output, max_bytes=current.manifest.max_output_bytes
        )
        if issues:
            raise ToolOutputError(tuple(issues))
        return ValidatedToolOutput(
            canonical_output=canonical.decode("utf-8"), output_hash=output_digest(canonical)
        )
