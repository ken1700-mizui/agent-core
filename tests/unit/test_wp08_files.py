"""WP-08: the signed manifest (pure part), the minimal ApplyPlan (``WriteChange``)
and the execution ticket, through the existing execution gate.

The broker (managed area, root and manifest through fds, component walk, lock,
S19) is tested in ``test_wp08_workspace.py``; reads and writes of real files in
``test_wp08_read.py`` and ``test_wp08_write.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.runtime.execution_gate import SyntheticResult
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
    PermitRejectedError,
    SafetyError,
    WorkspaceRejectedError,
)
from agent_core.types.files import (
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
