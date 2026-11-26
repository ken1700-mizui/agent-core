"""WP-08 (in progress): file operations in the managed synthetic work tree.

Safe path resolution is not available in this environment (no openat2, and the
dir_fd walk is not adopted), so C1.S19 applies: every read and write is refused
before any target file is opened. What is tested here, through the existing
execution gate:

- the signed workspace manifest (HMAC-SHA-256 with a key derived for this
  purpose), bound to the tree's root and to the policy's trusted digest;
- the minimal ApplyPlan (``WriteChange``) bound into the approved operation;
- the execution ticket: issued after the start is committed, used once inside
  the callback for the matched operation only;
- the S19 refusal: no side effect on the tree, and the Attempt, reservation and
  receipt end in the designed states.

Not verified here: a normal read or write of a real file, and stopping the
process during a real file operation (they need safe path resolution).
"""

from __future__ import annotations

import builtins
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.adapters import file_broker
from agent_core.adapters.file_broker import SyntheticFileBroker, safe_resolution_available
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.file_execution import run_file_operation
from agent_core.safety.descriptor import write_change
from agent_core.safety.workspace import (
    file_digest,
    manifest_digest,
    manifest_key,
    verify_manifest,
)
from agent_core.types.cost import ReservationState
from agent_core.types.errors import (
    ActionDescriptorError,
    FileOperationRefusedError,
    PermitRejectedError,
    SafetyError,
    WorkspaceRejectedError,
)
from agent_core.types.files import (
    MANIFEST_FILE_NAME,
    ExecutionTicket,
    FileOperation,
)
from agent_core.types.ids import ReservationId
from agent_core.types.records import AttemptState
from agent_core.types.safety import (
    ActionDescriptor,
    ReceiptState,
    ToolContent,
    WriteChange,
    WriteOp,
)
from agent_core.types.serialization import dump_json, parse_strict_json
from agent_core.types.tools import (
    ApplyField,
    FieldSpec,
    FieldType,
    ObjectSchema,
    ResourceAccess,
)
from agent_core.types.transitions import AbortReason
from support import builders as b
from support import files_world as fw
from support import ledger_world as lw
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = ReservationState
NEW_PATH = "workspace/out/new.txt"


@pytest.fixture
def world(tmp_path: Path) -> Any:
    st, tree = fw.world(tmp_path)
    yield st, tree
    if not st.journal.closed:
        st.journal.close()


def _broker(st: Any, tree: fw.Tree, root: Path | None = None) -> SyntheticFileBroker:
    return SyntheticFileBroker(
        root=tree.root if root is None else root, key=manifest_key(st.keys.owner)
    )


def _descriptor(st: Any, attempt: Any) -> ActionDescriptor:
    recorded: ActionDescriptor | None = st.journal.recorded_descriptor(attempt.attempt_id)
    assert recorded is not None
    return recorded


def _policy(tree: fw.Tree, **overrides: Any) -> Any:
    values: dict[str, Any] = {"trusted_synthetic_workspaces": (tree.digest,)}
    values.update(overrides)
    return s.policy(**values)


def _rejected(call: Any, reason: str) -> None:
    with pytest.raises(WorkspaceRejectedError) as excinfo:
        call()
    assert reason in excinfo.value.reasons


# ================================================================ workspace manifest


def test_the_manifest_key_is_derived_for_its_purpose(tmp_path: Path) -> None:
    keys = lw.new_keys()
    derived = manifest_key(keys.owner)
    assert derived.reveal() not in (keys.owner.reveal(), keys.permit.reveal())
    assert manifest_key(keys.owner).reveal() == derived.reveal()
    assert manifest_key(lw.new_keys().owner).reveal() != derived.reveal()
    good = fw.make_tree(tmp_path, keys.owner)
    assert verify_manifest(derived, good.signed) == good.signed.manifest
    # Signed with the owner key itself, or with the permit key: not a manifest MAC.
    for i, key in enumerate((keys.owner, keys.permit)):
        forged = fw.make_tree(tmp_path, keys.owner, f"forged{i}", key=key)
        _rejected(lambda f=forged: verify_manifest(derived, f.signed), "MANIFEST_MAC_INVALID")


@pytest.mark.parametrize(
    "change",
    [
        {"root_inode": 1},
        {"root_path": "/elsewhere"},
        {"workspace_id": "other_tree"},
        {"entries": ()},
        {"created_at_ms": 1},
    ],
)
def test_a_changed_manifest_is_refused(tmp_path: Path, change: dict[str, Any]) -> None:
    keys = lw.new_keys()
    tree = fw.make_tree(tmp_path, keys.owner)
    changed = tree.signed.model_copy(
        update={"manifest": tree.signed.manifest.model_copy(update=change)}
    )
    _rejected(lambda: verify_manifest(manifest_key(keys.owner), changed), "MANIFEST_MAC_INVALID")
    assert manifest_digest(changed.manifest) != tree.digest


def test_the_manifest_is_synthetic_and_names_an_absolute_root(tmp_path: Path) -> None:
    keys = lw.new_keys()
    tree = fw.make_tree(tmp_path, keys.owner)
    data = parse_strict_json(dump_json(tree.signed.manifest))
    assert isinstance(data, dict)
    for bad in ({"synthetic": False}, {"root_path": "relative/root"}, {"manifest_version": 2}):
        with pytest.raises(ValidationError):
            type(tree.signed.manifest).model_validate_json(json.dumps({**data, **bad}))


def test_the_digest_of_a_manifest_is_the_workspace_ref(tmp_path: Path) -> None:
    keys = lw.new_keys()
    first = fw.make_tree(tmp_path, keys.owner, "first")
    second = fw.make_tree(tmp_path, keys.owner, "second")
    assert first.digest.startswith("sha256:")
    assert first.digest == manifest_digest(first.signed.manifest)
    assert first.digest != second.digest


def test_a_valid_tree_passes_until_the_s19_refusal(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    broker = _broker(st, tree)
    with pytest.raises(FileOperationRefusedError) as excinfo:
        broker.preflight(_descriptor(st, attempt), _policy(tree))
    assert excinfo.value.reasons == ("SAFE_FILE_ACCESS_UNAVAILABLE",)


def test_a_manifest_copied_to_another_tree_is_refused(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    other = tmp_path / "other"
    fw.write_files(other, fw.FILES)
    shutil.copyfile(tree.root / MANIFEST_FILE_NAME, other / MANIFEST_FILE_NAME)
    broker = _broker(st, tree, root=other)
    _rejected(lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "ROOT_MISMATCH")


def test_a_copy_moved_into_the_original_path_is_refused(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    moved = tmp_path / "moved"
    tree.root.rename(moved)
    shutil.copytree(moved, tree.root)  # same path, same bytes, same manifest
    tree.root.chmod(0o700)
    broker = _broker(st, tree)
    _rejected(
        lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)),
        "ROOT_IDENTITY_MISMATCH",
    )


def test_a_tree_without_a_manifest_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / MANIFEST_FILE_NAME).unlink()
    broker = _broker(st, tree)
    _rejected(
        lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "MANIFEST_UNREADABLE"
    )


def test_a_manifest_behind_a_link_is_refused(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    kept = tmp_path / "kept.json"
    (tree.root / MANIFEST_FILE_NAME).rename(kept)
    (tree.root / MANIFEST_FILE_NAME).symlink_to(kept)
    broker = _broker(st, tree)
    _rejected(
        lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "MANIFEST_UNREADABLE"
    )
    (tree.root / MANIFEST_FILE_NAME).unlink()
    os.link(kept, tree.root / MANIFEST_FILE_NAME)  # a second hard link
    _rejected(
        lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "MANIFEST_UNREADABLE"
    )


def test_a_root_behind_a_link_is_refused(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    link = tmp_path / "link"
    link.symlink_to(tree.root)
    broker = _broker(st, tree, root=link)
    _rejected(lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "ROOT_UNUSABLE")


def test_the_root_must_be_private(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    tree.root.chmod(0o750)
    broker = _broker(st, tree)
    _rejected(lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "ROOT_PERMISSIONS")


def test_the_descriptor_must_name_this_manifest(world: Any, tmp_path: Path) -> None:
    st, tree = world
    other = fw.make_tree(tmp_path, st.keys.owner, "other")
    attempt = fw.consumed(st, tree)
    broker = _broker(st, other)
    _rejected(
        lambda: broker.preflight(_descriptor(st, attempt), _policy(tree)), "WORKSPACE_MISMATCH"
    )


@pytest.mark.parametrize("case", ["untrusted", "no-policy", "other-version"])
def test_the_policy_must_trust_the_manifest_now(world: Any, case: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    broker = _broker(st, tree)
    policy = {
        "untrusted": s.policy(trusted_synthetic_workspaces=()),
        "no-policy": None,
        "other-version": _policy(tree, policy_version="policy-2"),
    }[case]
    _rejected(lambda: broker.preflight(_descriptor(st, attempt), policy), "WORKSPACE_NOT_TRUSTED")


def test_the_pdp_denies_an_untrusted_manifest_digest(tmp_path: Path) -> None:
    st, _ = fw.world(tmp_path)
    try:
        other = fw.make_tree(tmp_path, st.keys.owner, "other")
        with pytest.raises(SafetyError) as excinfo:
            fw.consumed(st, other)
        assert "UNTRUSTED_WORKSPACE" in excinfo.value.reasons
    finally:
        st.journal.close()


# ================================================================ ApplyPlan (WriteChange)


def _write_fields(*extra: FieldSpec, content: bool = True, expected: bool = True) -> ObjectSchema:
    fields = [
        FieldSpec(
            name="path",
            type=FieldType.STRING,
            required=True,
            max_length=256,
            resource=ResourceAccess.WRITE,
        )
    ]
    if content:
        fields.append(
            FieldSpec(
                name="content",
                type=FieldType.STRING,
                required=True,
                max_length=65_536,
                apply=ApplyField.CONTENT,
            )
        )
    if expected:
        fields.append(
            FieldSpec(
                name="expected",
                type=FieldType.STRING,
                required=True,
                max_length=71,
                apply=ApplyField.EXPECTED_STATE,
            )
        )
    return ObjectSchema(fields=(*fields, *extra), max_bytes=70_000)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(lambda: _write_fields(content=False), id="no-content"),
        pytest.param(lambda: _write_fields(expected=False), id="no-expected-state"),
        pytest.param(
            lambda: _write_fields(
                FieldSpec(
                    name="more",
                    type=FieldType.STRING,
                    required=True,
                    max_length=10,
                    apply=ApplyField.CONTENT,
                )
            ),
            id="two-contents",
        ),
        pytest.param(
            lambda: _write_fields(
                FieldSpec(
                    name="second",
                    type=FieldType.STRING,
                    required=True,
                    max_length=256,
                    resource=ResourceAccess.WRITE,
                )
            ),
            id="two-write-targets",
        ),
    ],
)
def test_a_write_tool_declares_one_target_content_and_expected_state(schema: Any) -> None:
    with pytest.raises(ValidationError):
        tw.body(**{**tw.WRITE_BODY, "input_schema": schema()})


def test_apply_fields_belong_to_write_tools_only() -> None:
    read_schema = ObjectSchema(
        fields=(
            *tw.body().input_schema.fields,
            FieldSpec(
                name="content",
                type=FieldType.STRING,
                required=True,
                max_length=10,
                apply=ApplyField.CONTENT,
            ),
        ),
        max_bytes=1_024,
    )
    with pytest.raises(ValidationError):
        tw.body(input_schema=read_schema)
    for bad in (
        {"type": FieldType.INTEGER, "max_length": None},
        {"required": False},
    ):
        values: dict[str, Any] = {
            "name": "content",
            "type": FieldType.STRING,
            "required": True,
            "max_length": 10,
            "apply": ApplyField.CONTENT,
        }
        values.update(bad)
        with pytest.raises(ValidationError):
            FieldSpec(**values)
    with pytest.raises(ValidationError):
        FieldSpec(
            name="path",
            type=FieldType.STRING,
            required=True,
            max_length=10,
            resource=ResourceAccess.WRITE,
            apply=ApplyField.CONTENT,
        )


def test_the_write_change_is_derived_from_the_arguments() -> None:
    at = s.ref()
    created = s.write_descriptor(at, canonical_input=fw.WRITE_INPUT)
    data = fw.NEW_CONTENT.encode()
    assert created.descriptor_version == 3
    assert created.apply_change == WriteChange(
        op=WriteOp.CREATE,
        path=NEW_PATH,
        expected_pre=None,
        content_digest=file_digest(data),
        content_size=len(data),
    )
    old = file_digest(fw.FILES["workspace/out/r.txt"])
    replaced = s.write_descriptor(
        at, canonical_input=tw.write_input("workspace/out/r.txt", content="x", expected=old)
    )
    assert replaced.apply_change is not None
    assert (replaced.apply_change.op, replaced.apply_change.expected_pre) == (WriteOp.REPLACE, old)
    assert s.read_descriptor(at).apply_change is None


@pytest.mark.parametrize(
    "changed",
    [
        tw.write_input(NEW_PATH, content="other content"),
        tw.write_input(NEW_PATH, content=fw.NEW_CONTENT, expected=file_digest(b"x")),
        tw.write_input("workspace/out/other.txt", content=fw.NEW_CONTENT),
    ],
)
def test_the_write_change_is_part_of_the_action_hash(changed: str) -> None:
    at = s.ref()
    rsv = b.new_id(ReservationId)
    base = s.write_descriptor(at, canonical_input=fw.WRITE_INPUT, reservation_id=rsv)
    other = s.write_descriptor(at, canonical_input=changed, reservation_id=rsv)
    assert other.apply_change != base.apply_change
    assert other.action_hash() != base.action_hash()


@pytest.mark.parametrize("expected", ["maybe", "sha256:abc", "", "ABSENT"])
def test_an_invalid_expected_state_is_refused(expected: str) -> None:
    with pytest.raises(ActionDescriptorError) as excinfo:
        s.write_descriptor(
            s.ref(), canonical_input=tw.write_input(NEW_PATH, content="x", expected=expected)
        )
    assert excinfo.value.reasons == ("EXPECTED_STATE_INVALID",)


def test_write_change_needs_the_registration_of_a_write_tool() -> None:
    parsed = parse_strict_json(fw.WRITE_INPUT.encode())
    assert isinstance(parsed, dict)
    assert write_change(s.READ_TOOL, {"path": "workspace/a.txt"}) is None
    assert write_change(s.WRITE_TOOL, parsed) is not None


def test_a_descriptor_cannot_carry_a_change_for_another_target() -> None:
    at = s.ref()
    write = s.write_descriptor(at, canonical_input=fw.WRITE_INPUT)
    assert write.apply_change is not None
    data = write.model_dump()
    moved = write.apply_change.model_copy(update={"path": "workspace/out/x.txt"}).model_dump()
    for bad in ({"apply_change": None}, {"apply_change": moved}):
        with pytest.raises(ValidationError):
            ActionDescriptor.model_validate({**data, **bad})
    read = s.read_descriptor(at).model_dump()
    with pytest.raises(ValidationError):
        ActionDescriptor.model_validate({**read, "apply_change": write.apply_change.model_dump()})


def test_substituted_content_is_refused_before_the_start(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True)
    gate = lw.gate(st)
    other = ToolContent(kind="tool", canonical_input=tw.write_input(NEW_PATH, content="evil"))
    with pytest.raises(PermitRejectedError, match="DESCRIPTOR_NOT_FROM_CONTENT"):
        gate.claim(attempt, content=other, registration=s.WRITE_TOOL)
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.ISSUED


# ================================================================ execution ticket


def _ok() -> SyntheticResult:
    return SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)


def _read_op(path: str = "workspace/a.txt") -> FileOperation:
    return FileOperation(access=ResourceAccess.READ, path=path, change=None)


def _write_op(descriptor: ActionDescriptor, **change: Any) -> FileOperation:
    assert descriptor.apply_change is not None
    bound = descriptor.apply_change.model_dump()
    bound.update(change)
    return FileOperation(
        access=ResourceAccess.WRITE, path=bound["path"], change=WriteChange(**bound)
    )


def test_an_operation_is_consistent_in_itself() -> None:
    change = WriteChange(
        op=WriteOp.CREATE,
        path=NEW_PATH,
        expected_pre=None,
        content_digest=file_digest(b""),
        content_size=0,
    )
    for bad in (
        {"access": ResourceAccess.WRITE, "path": NEW_PATH, "change": None},
        {"access": ResourceAccess.READ, "path": NEW_PATH, "change": change},
        {"access": ResourceAccess.WRITE, "path": "workspace/out/x.txt", "change": change},
    ):
        with pytest.raises(ValidationError):
            FileOperation(**bad)
    with pytest.raises(ValidationError):
        WriteChange(**{**change.model_dump(), "expected_pre": file_digest(b"x")})
    with pytest.raises(ValidationError):
        WriteChange(**{**change.model_dump(), "op": WriteOp.REPLACE})


def _execute(st: Any, attempt: Any, body: Any, gate: Any = None) -> Any:
    gate = gate if gate is not None else lw.gate(st)

    def run(authorization: Any, ticket: Any) -> SyntheticResult:
        body(gate, authorization, ticket)
        return _ok()

    return gate.execute(attempt, run=run, **lw.actual(st, attempt))


def _ticket_reasons(call: Any) -> tuple[str, ...]:
    with pytest.raises(PermitRejectedError) as excinfo:
        call()
    return excinfo.value.reasons


def test_the_ticket_is_issued_after_the_start_is_committed(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    seen: list[Any] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        assert isinstance(ticket, ExecutionTicket)
        assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
        assert st.sm.state.attempts[attempt.attempt_id].state is A.DISPATCH_STARTED
        seen.append(gate.redeem(ticket, _read_op()))

    done = _execute(st, attempt, body)
    assert done.state is A.COMPLETED
    (binding,) = seen
    assert binding.receipt_id == attempt.receipt_id
    assert binding.attempt_id == attempt.attempt_id
    assert binding.action_hash == attempt.action_hash
    assert binding.operation == _read_op()


def test_there_is_no_ticket_outside_the_callback(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    claimed = gate.claim(attempt, **lw.actual(st, attempt))
    started, authorization = gate.start(claimed)
    # The separate steps give no ticket; nothing made up is accepted.
    assert not any(isinstance(v, ExecutionTicket) for v in (started, authorization))
    assert _ticket_reasons(lambda: gate.redeem(ExecutionTicket(), _read_op())) == (
        "TICKET_UNKNOWN",
    )


def test_a_ticket_is_used_once(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    seen: list[tuple[str, ...]] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        gate.redeem(ticket, _read_op())
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, _read_op())))

    _execute(st, attempt, body)
    assert seen == [("TICKET_USED",)]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda d: _read_op("workspace/out/r.txt"), id="other-path"),
        pytest.param(
            lambda d: FileOperation(
                access=ResourceAccess.WRITE,
                path="workspace/a.txt",
                change=WriteChange(
                    op=WriteOp.CREATE,
                    path="workspace/a.txt",
                    expected_pre=None,
                    content_digest=file_digest(b""),
                    content_size=0,
                ),
            ),
            id="other-access",
        ),
    ],
)
def test_a_read_ticket_cannot_serve_another_operation(world: Any, operation: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    seen: list[tuple[str, ...]] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, operation(None))))
        # A mismatch burns the ticket: the matching operation is refused afterwards.
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, _read_op())))

    _execute(st, attempt, body)
    assert seen == [("TICKET_MISMATCH",), ("TICKET_USED",)]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"content_digest": file_digest(b"substituted")}, id="content"),
        pytest.param({"content_size": 1}, id="size"),
        pytest.param({"expected_pre": file_digest(b"x"), "op": WriteOp.REPLACE}, id="state"),
        pytest.param({"path": "workspace/out/x.txt"}, id="change-path"),
    ],
)
def test_a_write_ticket_binds_the_content_and_the_expected_state(
    world: Any, change: dict[str, Any]
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True)
    seen: list[Any] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        descriptor = authorization.descriptor
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, _write_op(descriptor, **change))))
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, _write_op(descriptor))))

    _execute(st, attempt, body)
    assert seen == [("TICKET_MISMATCH",), ("TICKET_USED",)]


def test_a_write_ticket_serves_its_own_change(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True)
    seen: list[Any] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        seen.append(gate.redeem(ticket, _write_op(authorization.descriptor)))

    _execute(st, attempt, body)
    (binding,) = seen
    data = fw.NEW_CONTENT.encode()
    assert binding.operation.change.content_digest == file_digest(data)
    assert binding.operation.path == NEW_PATH


def test_a_ticket_cannot_be_used_after_the_callback(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    kept: list[Any] = []
    gate = lw.gate(st)
    _execute(st, attempt, lambda g, a, ticket: kept.append(ticket), gate)
    assert _ticket_reasons(lambda: gate.redeem(kept[0], _read_op())) == ("TICKET_CLOSED",)


def test_a_ticket_is_closed_when_the_callback_raises(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    kept: list[Any] = []

    def run(authorization: Any, ticket: Any) -> SyntheticResult:
        kept.append(ticket)
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError):
        gate.execute(attempt, run=run, **lw.actual(st, attempt))
    assert _ticket_reasons(lambda: gate.redeem(kept[0], _read_op())) == ("TICKET_CLOSED",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_an_earlier_ticket_does_not_work_in_a_later_execution(world: Any) -> None:
    st, tree = world
    gate = lw.gate(st)
    first = fw.consumed(st, tree)
    kept: list[Any] = []
    _execute(st, first, lambda g, a, ticket: kept.append(ticket), gate)
    later = fw.consumed(st, tree)
    seen: list[Any] = []

    def body(g: Any, authorization: Any, ticket: Any) -> None:
        seen.append(_ticket_reasons(lambda: g.redeem(kept[0], _read_op())))
        seen.append(g.redeem(ticket, _read_op()).attempt_id)

    _execute(st, later, body, gate)
    assert seen == [("TICKET_CLOSED",), later.attempt_id]


def test_a_ticket_from_another_gate_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    other = lw.gate(st)
    seen: list[Any] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        seen.append(_ticket_reasons(lambda: other.redeem(ticket, _read_op())))
        seen.append(gate.redeem(ticket, _read_op()).attempt_id)

    _execute(st, attempt, body)
    assert seen == [("TICKET_UNKNOWN",), attempt.attempt_id]


# ================================================================ S19 through the gate


class _Spy:
    """Records every path the process opens or changes while installed."""

    NAMES = (
        "open",
        "rename",
        "replace",
        "link",
        "symlink",
        "unlink",
        "remove",
        "rmdir",
        "mkdir",
        "makedirs",
        "truncate",
        "chmod",
        "chown",
        "utime",
        "mkfifo",
        "mknod",
    )

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str]] = []
        for name in self.NAMES:
            original = getattr(os, name)
            monkeypatch.setattr(os, name, self._wrap(name, original))
        original_open = builtins.open
        monkeypatch.setattr(builtins, "open", self._wrap("builtins.open", original_open))

    def _wrap(self, name: str, original: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args:
                self.calls.append(
                    (name, os.fsdecode(args[0]) if not isinstance(args[0], int) else str(args[0]))
                )
            return original(*args, **kwargs)

        return wrapper

    def touched(self, root: Path) -> set[tuple[str, str]]:
        prefix = str(root)
        return {(n, p) for n, p in self.calls if p.startswith(prefix)}


def _run(st: Any, tree: fw.Tree, attempt: Any, files: Any, write: bytes | None) -> Any:
    return run_file_operation(
        gate=lw.gate(st),
        ledger=st.journal,
        files=files,
        attempt=attempt,
        write_content=write,
        **lw.actual(st, attempt),
    ).attempt


def _not_dispatched(st: Any, attempt: Any, done: Any, reason: AbortReason) -> None:
    assert done.state is A.ABORTED_BEFORE_DISPATCH
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro, view.spent_micro) == (R.RELEASED, 0, 0)
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.NOT_DISPATCHED
    states = [t.after.state for t in fw.history(st, attempt)]
    assert A.DISPATCH_AUTHORIZED not in states
    assert A.DISPATCH_STARTED not in states
    last = fw.history(st, attempt)[-1]
    assert last.evidence.reason is reason


@pytest.mark.parametrize(
    ("write", "canonical_input"),
    [
        pytest.param(False, fw.READ_INPUT, id="read"),
        pytest.param(True, fw.WRITE_INPUT, id="create"),
        pytest.param(
            True,
            tw.write_input(
                "workspace/out/r.txt",
                content="synthetic replaced\n",
                expected=file_digest(fw.FILES["workspace/out/r.txt"]),
            ),
            id="replace",
        ),
    ],
)
def test_s19_refuses_before_any_target_is_opened(
    world: Any, monkeypatch: pytest.MonkeyPatch, write: bool, canonical_input: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=write, canonical_input=canonical_input)
    before = fw.snapshot(tree.root)
    content = parse_strict_json(canonical_input.encode())
    assert isinstance(content, dict)
    payload = str(content["content"]).encode() if write else None
    spy = _Spy(monkeypatch)
    done = _run(st, tree, attempt, _broker(st, tree), payload)
    monkeypatch.undo()
    _not_dispatched(st, attempt, done, AbortReason.SAFE_FILE_ACCESS_UNAVAILABLE)
    # Only the root and the manifest were opened; nothing was created or changed.
    assert spy.touched(tree.root) == {
        ("open", str(tree.root)),
        ("open", str(tree.root / MANIFEST_FILE_NAME)),
    }
    assert fw.snapshot(tree.root) == before
    assert not (tree.root / NEW_PATH).exists()


def test_a_rejected_manifest_aborts_before_dispatch(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / MANIFEST_FILE_NAME).write_bytes(b"{}")
    before = fw.snapshot(tree.root)
    done = _run(st, tree, attempt, _broker(st, tree), None)
    _not_dispatched(st, attempt, done, AbortReason.WORKSPACE_REJECTED)
    assert fw.snapshot(tree.root) == before


class _PreflightPassed:
    """Lets an operation reach the callback; the real broker then decides there."""

    def __init__(self, broker: SyntheticFileBroker) -> None:
        self._broker = broker

    def preflight(self, descriptor: Any, policy: Any) -> None:
        return None

    def read(self, authorization: Any, ticket: Any, redeem: Any) -> bytes:
        return self._broker.read(authorization, ticket, redeem)

    def write(self, authorization: Any, ticket: Any, redeem: Any, content: bytes) -> None:
        self._broker.write(authorization, ticket, redeem, content)


@pytest.mark.parametrize("write", [False, True])
def test_s19_inside_the_callback_fails_definitely_without_side_effects(
    world: Any, monkeypatch: pytest.MonkeyPatch, write: bool
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=write)
    before = fw.snapshot(tree.root)
    spy = _Spy(monkeypatch)
    done = _run(
        st,
        tree,
        attempt,
        _PreflightPassed(_broker(st, tree)),
        fw.NEW_CONTENT.encode() if write else None,
    )
    monkeypatch.undo()
    assert done.state is A.FAILED_DEFINITE
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.hold_micro, view.spent_micro) == (0, 0)
    assert spy.touched(tree.root) == set()
    assert fw.snapshot(tree.root) == before


def test_a_file_operation_without_safe_resolution_is_never_started(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True)
    done = _run(st, tree, attempt, _broker(st, tree), b"different bytes")
    # Refused before the start whatever the content: no ticket is ever issued.
    assert done.state is A.ABORTED_BEFORE_DISPATCH


def test_safe_resolution_is_not_available_here() -> None:
    assert file_broker.SAFE_RESOLUTION_ADOPTED is False
    assert safe_resolution_available() is False
