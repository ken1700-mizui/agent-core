"""Tool Registry (WP-07; C1 5, C1.S8, C1.S23).

Registration needs a matching owner-managed trusted catalog entry; a proposal
is validated data, never a Permit, and the registry holds no executable code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.tool.registry import ToolRegistry, TrustedToolCatalog
from agent_core.types.errors import (
    ToolAlreadyRegisteredError,
    ToolInputError,
    ToolKindNotAllowedError,
    ToolNotApprovedError,
    ToolOutputError,
    UnknownToolError,
    UnknownToolVersionError,
)
from agent_core.types.ids import AttemptId
from agent_core.types.model import ToolUseBlock
from agent_core.types.serialization import dump_json
from agent_core.types.tools import (
    ExternalGuarantee,
    ResourceAccess,
    ResourceSpec,
    ToolEffectClass,
    ToolKind,
    ToolManifest,
    ToolProposal,
)
from support import builders as b
from support import tool_world as t

MARKER = "synthetic-secret-" + "Wm3" * 6


def _registry(*manifests: ToolManifest) -> ToolRegistry:
    registry = ToolRegistry(t.catalog(*manifests))
    for sealed in manifests:
        registry.register(sealed, artifact_hash=t.ARTIFACT)
    return registry


def _propose(registry: ToolRegistry, arguments: str, **overrides: Any) -> ToolProposal:
    values: dict[str, Any] = {
        "tool_id": "read_file",
        "version": "1.0.0",
        "arguments": arguments,
        "attempt_id": b.new_id(AttemptId),
    }
    values.update(overrides)
    return registry.propose(**values)


# ---------------------------------------------------------------- registration


def test_builtin_tool_registers_against_the_trusted_catalog() -> None:
    sealed = t.manifest()
    registry = ToolRegistry(t.catalog(sealed))
    registration = registry.register(sealed, artifact_hash=t.ARTIFACT)
    assert registration.manifest == sealed
    assert registration.artifact_hash == t.ARTIFACT
    assert registration.manifest.manifest_hash != registration.artifact_hash
    assert registry.get("read_file", "1.0.0") == registration


def test_unknown_tool_and_unknown_version_are_rejected() -> None:
    registry = _registry(t.manifest())
    with pytest.raises(UnknownToolError):
        registry.get("delete_everything", "1.0.0")
    with pytest.raises(UnknownToolVersionError):
        registry.get("read_file", "2.0.0")
    with pytest.raises(UnknownToolError):
        _propose(registry, "{}", tool_id="delete_everything")


def test_registration_without_a_trusted_entry_is_refused() -> None:
    sealed = t.manifest()
    registry = ToolRegistry(TrustedToolCatalog(entries=()))
    with pytest.raises(ToolNotApprovedError):
        registry.register(sealed, artifact_hash=t.ARTIFACT)


def test_self_declared_hashes_do_not_register_a_tool() -> None:
    approved = t.manifest()
    changed = t.manifest(timeout_ms=9_999)  # a validly sealed, but unreviewed, change
    registry = ToolRegistry(t.catalog(approved))
    with pytest.raises(ToolNotApprovedError):
        registry.register(changed, artifact_hash=t.ARTIFACT)


def test_artifact_hash_must_match_the_trusted_entry() -> None:
    sealed = t.manifest()
    registry = ToolRegistry(t.catalog(sealed))
    with pytest.raises(ToolNotApprovedError):
        registry.register(sealed, artifact_hash=b.digest("other-build"))


def test_same_id_and_version_cannot_be_overwritten() -> None:
    sealed = t.manifest()
    other = t.manifest(timeout_ms=1_000)
    catalog = TrustedToolCatalog(entries=(t.entry(sealed),))
    registry = ToolRegistry(catalog)
    first = registry.register(sealed, artifact_hash=t.ARTIFACT)
    assert registry.register(sealed, artifact_hash=t.ARTIFACT) == first  # idempotent
    with pytest.raises((ToolAlreadyRegisteredError, ToolNotApprovedError)):
        registry.register(other, artifact_hash=t.ARTIFACT)
    assert registry.get("read_file", "1.0.0") == first


def test_catalog_rejects_two_entries_for_one_id_and_version() -> None:
    sealed = t.manifest()
    with pytest.raises(ValidationError):
        TrustedToolCatalog(entries=(t.entry(sealed), t.entry(sealed, b.digest("x"))))


@pytest.mark.parametrize("kind", [ToolKind.SANDBOXED_COMMAND, ToolKind.MCP, ToolKind.MODEL_CALL])
def test_only_builtin_tools_are_allowed_in_phase_1(kind: ToolKind) -> None:
    sealed = t.manifest(kind=kind)
    registry = ToolRegistry(t.catalog(sealed))
    with pytest.raises(ToolKindNotAllowedError):
        registry.register(sealed, artifact_hash=t.ARTIFACT)


def test_invalid_tool_kind_is_rejected() -> None:
    data = json.loads(dump_json(t.manifest()))
    data["kind"] = "SHELL"
    with pytest.raises(ValidationError):
        ToolManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "overrides",
    [
        {"destinations": ("api.example.invalid",)},
        {"sandbox_profile": b.digest("profile")},
    ],
)
def test_features_not_allowed_in_phase_1_are_refused(overrides: dict[str, Any]) -> None:
    sealed = t.manifest(**overrides)
    registry = ToolRegistry(t.catalog(sealed))
    with pytest.raises(ToolKindNotAllowedError):
        registry.register(sealed, artifact_hash=t.ARTIFACT)


# ---------------------------------------------------------------- manifest integrity


def test_tampered_manifest_is_detected() -> None:
    data = json.loads(dump_json(t.manifest()))
    data["timeout_ms"] = 600_000
    with pytest.raises(ValidationError) as excinfo:
        ToolManifest.model_validate_json(json.dumps(data))
    assert "manifest_hash_mismatch" in {e["type"] for e in excinfo.value.errors()}


def test_manifest_hash_is_deterministic_and_covers_every_field() -> None:
    assert t.manifest().manifest_hash == t.manifest().manifest_hash
    hashes = {
        t.manifest().manifest_hash,
        t.manifest(version="1.0.1").manifest_hash,
        t.manifest(max_output_bytes=69_999).manifest_hash,
        t.manifest(effect_class=ToolEffectClass.IDEMPOTENT).manifest_hash,
    }
    assert len(hashes) == 4


def test_read_only_tool_cannot_declare_writes() -> None:
    with pytest.raises(ValidationError):
        t.body(resources=t.WRITE_BODY["resources"])
    with pytest.raises(ValidationError):
        t.body(required_permissions=t.WRITE_BODY["required_permissions"])


@pytest.mark.parametrize("pattern", ["/etc/passwd", "../outside", "a/../../b", "", "a b"])
def test_resource_patterns_must_be_relative_and_contained(pattern: str) -> None:
    with pytest.raises(ValidationError):
        t.body(resources=(ResourceSpec(path_pattern=pattern, access=ResourceAccess.READ),))


def test_registrations_are_immutable() -> None:
    registration = _registry(t.manifest()).get("read_file", "1.0.0")
    with pytest.raises(ValidationError):
        registration.artifact_hash = b.digest("x")  # type: ignore[misc]  # runtime check of frozen
    with pytest.raises(ValidationError):
        registration.manifest.timeout_ms = 1  # type: ignore[misc]  # runtime check of frozen


# ---------------------------------------------------------------- external guarantee


@pytest.mark.parametrize(
    "declared",
    [ExternalGuarantee.NONE, ExternalGuarantee.IDEMPOTENCY_KEY, ExternalGuarantee.CONDITIONAL],
)
def test_unverified_external_guarantee_is_treated_as_none(declared: ExternalGuarantee) -> None:
    registry = _registry(t.manifest(external_guarantee=declared))
    registration = registry.get("read_file", "1.0.0")
    assert registration.manifest.external_guarantee is declared
    assert registration.effective_external_guarantee is ExternalGuarantee.NONE
    proposal = _propose(registry, '{"path":"workspace/a.txt"}')
    assert proposal.effective_external_guarantee is ExternalGuarantee.NONE


# ---------------------------------------------------------------- input and output


def test_valid_input_gives_a_deterministic_proposal() -> None:
    registry = _registry(t.manifest())
    a = _propose(registry, '{"max_bytes":10,"path":"workspace/a.txt"}')
    c = _propose(registry, '{"max_bytes":10,"path":"workspace/a.txt"}')
    assert a.input_hash == c.input_hash
    assert a.canonical_input == '{"max_bytes":10,"path":"workspace/a.txt"}'
    assert a.manifest_hash == registry.get("read_file", "1.0.0").manifest.manifest_hash
    assert a.artifact_hash == t.ARTIFACT
    assert a.requires_approval is False


@pytest.mark.parametrize(
    ("arguments", "issue"),
    [
        ("{}", "missing"),
        ('{"path":1}', "string_type"),
        ('{"path":"a","max_bytes":"10"}', "int_type"),
        ('{"path":"a","max_bytes":true}', "int_type"),
        ('{"path":"a","max_bytes":70000}', "less_than_equal"),
        ('{"path":"' + "x" * 257 + '"}', "string_too_long"),
        ('{"path":"a","extra":1}', "extra_forbidden"),
        ('{"path":"a","path":"b"}', "json_duplicate_key"),
        ("[1]", "object_required"),
        ('{"path":"a","max_bytes":1.5}', "int_type"),
    ],
)
def test_invalid_input_is_rejected_with_issue_types(arguments: str, issue: str) -> None:
    registry = _registry(t.manifest())
    with pytest.raises(ToolInputError) as excinfo:
        _propose(registry, arguments)
    assert issue in {i.type for i in excinfo.value.issues}


def test_input_size_limit_is_enforced() -> None:
    registry = _registry(
        t.manifest(input_schema=t.body().input_schema.model_copy(update={"max_bytes": 20}))
    )
    with pytest.raises(ToolInputError) as excinfo:
        _propose(registry, '{"path":"workspace/long-name.txt"}')
    assert "too_large" in {i.type for i in excinfo.value.issues}


def test_input_errors_do_not_echo_names_or_values() -> None:
    registry = _registry(t.manifest())
    with pytest.raises(ToolInputError) as excinfo:
        _propose(registry, json.dumps({"path": MARKER, MARKER: MARKER, "max_bytes": MARKER}))
    text = str(excinfo.value) + repr(excinfo.value.issues)
    assert MARKER not in text
    assert excinfo.value.__cause__ is None


def test_output_is_validated_against_schema_and_size() -> None:
    registry = _registry(t.manifest())
    registration = registry.get("read_file", "1.0.0")
    ok = registry.validate_output(registration, '{"content":"synthetic","truncated":false}')
    assert ok.output_hash.startswith("sha256:")
    with pytest.raises(ToolOutputError):
        registry.validate_output(registration, '{"content":"x"}')
    with pytest.raises(ToolOutputError):
        registry.validate_output(registration, '{"content":"x","truncated":false,"leak":1}')
    small = t.manifest(max_output_bytes=10)
    small_registry = _registry(small)
    with pytest.raises(ToolOutputError) as excinfo:
        small_registry.validate_output(
            small_registry.get("read_file", "1.0.0"), '{"content":"synthetic","truncated":false}'
        )
    assert "too_large" in {i.type for i in excinfo.value.issues}


# ---------------------------------------------------------------- proposals are not permits


def test_write_tool_proposal_requires_approval_and_is_not_a_permit() -> None:
    write = t.manifest(**t.WRITE_BODY)
    registry = _registry(write)
    proposal = _propose(registry, '{"path":"workspace/a.txt"}', tool_id="write_file")
    assert proposal.requires_approval is True
    assert proposal.effect_class is ToolEffectClass.IRREVERSIBLE
    public = {n for n in dir(proposal) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("execute", "run", "call", "permit", "consume"))}
    assert "permit_id" not in ToolProposal.model_fields


def test_registry_exposes_no_executable_code() -> None:
    registry = _registry(t.manifest())
    registration = registry.get("read_file", "1.0.0")
    for value in (*registration.model_dump().values(), *vars(registry).values()):
        assert not callable(value)


def test_model_tool_request_alone_cannot_register_or_authorize() -> None:
    registry = _registry(t.manifest())
    # A model asks for an unregistered tool: refused, nothing registered.
    request = ToolUseBlock(type="tool_use", id="call_1", name="install_tool", arguments="{}")
    with pytest.raises(UnknownToolError):
        _propose(registry, request.arguments, tool_id=request.name)
    assert not hasattr(registry, "register_from_model")
    with pytest.raises(UnknownToolError):
        registry.get("install_tool", "1.0.0")
    # A model request for a registered tool yields only a proposal (data).
    request = ToolUseBlock(
        type="tool_use", id="call_2", name="read_file", arguments='{"path":"workspace/a.txt"}'
    )
    proposal = _propose(registry, request.arguments, tool_id=request.name)
    assert isinstance(proposal, ToolProposal)
