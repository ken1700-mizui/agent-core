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
    FileEffect,
    FileOperationFailedError,
    PermitRejectedError,
    SafetyError,
    WorkspaceRejectedError,
)
from agent_core.types.files import (
    ExecutionTicket,
    FileOperation,
    FileOutcome,
    TicketBinding,
)
from agent_core.types.ids import AttemptId, ReceiptId, ReservationId
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
    _WS["ref"] = tree.digest
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
        gate.execute_file(
            attempt,
            content=other,
            registration=s.WRITE_TOOL,
            prepared=_Stand(gate, lambda g, a, t: None, True),
            write_content=b"evil",
            accept_read=lambda data: True,
        )
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.ISSUED


# ================================================================ execution ticket


def _ok() -> SyntheticResult:
    return SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)


# The tree of the running test (set by the ``world`` fixture).
_WS: dict[str, str] = {}


def _read_op(path: str = "workspace/a.txt", workspace: str | None = None) -> FileOperation:
    return FileOperation(
        access=ResourceAccess.READ,
        path=path,
        change=None,
        workspace_ref=_WS["ref"] if workspace is None else workspace,
    )


def _write_op(descriptor: ActionDescriptor, **change: Any) -> FileOperation:
    assert descriptor.apply_change is not None
    bound = descriptor.apply_change.model_dump()
    bound.update(change)
    assert descriptor.workspace_ref is not None
    return FileOperation(
        access=ResourceAccess.WRITE,
        path=bound["path"],
        change=WriteChange(**bound),
        workspace_ref=descriptor.workspace_ref,
    )


def test_an_operation_is_consistent_in_itself() -> None:
    change = WriteChange(
        op=WriteOp.CREATE,
        path=NEW_PATH,
        expected_pre=None,
        content_digest=file_digest(b""),
        content_size=0,
    )
    ws = b.digest("tree")
    for bad in (
        {"access": ResourceAccess.WRITE, "path": NEW_PATH, "change": None},
        {"access": ResourceAccess.READ, "path": NEW_PATH, "change": change},
        {"access": ResourceAccess.WRITE, "path": "workspace/out/x.txt", "change": change},
    ):
        with pytest.raises(ValidationError):
            FileOperation(**bad, workspace_ref=ws)
    with pytest.raises(ValidationError):
        FileOperation.model_validate(
            {"access": ResourceAccess.READ, "path": NEW_PATH, "change": None}  # no tree
        )
    with pytest.raises(ValidationError):
        WriteChange(**{**change.model_dump(), "expected_pre": file_digest(b"x")})
    with pytest.raises(ValidationError):
        WriteChange(**{**change.model_dump(), "op": WriteOp.REPLACE})


def _bound(authorization: Any) -> TicketBinding:
    """The binding the gate gives the attempt's ticket (computed independently)."""
    descriptor = authorization.descriptor
    (target,) = descriptor.target_resources
    write = target.access is ResourceAccess.WRITE
    return TicketBinding(
        receipt_id=authorization.receipt.receipt_id,
        attempt_id=descriptor.attempt_id,
        action_hash=authorization.action_hash,
        operation=FileOperation(
            access=target.access,
            path=target.path,
            change=descriptor.apply_change if write else None,
            workspace_ref=descriptor.workspace_ref,
        ),
    )


def _outcome(authorization: Any, data: bytes = b"x", **binding: object) -> FileOutcome:
    bound = _bound(authorization).model_copy(update=binding)
    change = bound.operation.change
    if change is not None:
        return FileOutcome(
            binding=bound, result_digest=change.content_digest, result_size=change.content_size
        )
    return FileOutcome(binding=bound, result_digest=file_digest(data), result_size=len(data))


class _Stand:
    """A stand-in for the broker's prepared operation, for the gate's mechanics only:
    it runs ``body`` where the file operation would be, then returns ``result`` (an
    outcome, or raises a definite failure when ``used`` is False). It touches no file."""

    def __init__(self, gate: Any, body: Any, used: bool, result: Any = None) -> None:
        self._gate, self._body, self._used, self._result = gate, body, used, result
        self.workspace_ref = _WS.get("ref", "")

    def _run(self, authorization: Any, ticket: Any) -> Any:
        self._body(self._gate, authorization, ticket)
        if not self._used:
            raise FileOperationFailedError("SYNTHETIC_NOT_DONE", effect=FileEffect.NONE)
        if self._result is not None:
            return self._result(authorization)
        return _outcome(authorization)

    def read(self, authorization: Any, ticket: Any, redeem: Any) -> Any:
        outcome = self._run(authorization, ticket)
        return outcome, b"x"

    def write(self, authorization: Any, ticket: Any, redeem: Any, content: bytes) -> Any:
        return self._run(authorization, ticket)

    def close(self) -> None:
        return None


def _execute(
    st: Any,
    attempt: Any,
    body: Any,
    gate: Any = None,
    *,
    used: bool = True,
    result: Any = None,
) -> Any:
    """Run ``body`` where the broker's operation would run, through ``execute_file``.
    ``used=False``: the stand-in reports a definite failure."""
    gate = gate if gate is not None else lw.gate(st)
    write = st.descriptors[attempt.attempt_id].apply_change is not None
    return gate.execute_file(
        attempt,
        prepared=_Stand(gate, body, used, result),
        write_content=b"unused" if write else None,
        accept_read=lambda data: True,
        **lw.actual(st, attempt),
    )


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
    assert done.attempt.state is A.COMPLETED
    (binding,) = seen
    assert binding.receipt_id == attempt.receipt_id
    assert binding.attempt_id == attempt.attempt_id
    assert binding.action_hash == attempt.action_hash
    assert binding.operation == _read_op()


def test_there_is_no_ticket_outside_the_broker_path(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    # WP08-R06: the separate steps refuse a file operation; nothing made up is accepted.
    assert _ticket_reasons(lambda: gate.claim(attempt, **lw.actual(st, attempt))) == (
        "FILE_OPERATION_NEEDS_BROKER",
    )
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
                workspace_ref=_WS["ref"],
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

    _execute(st, attempt, body, used=False)
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

    _execute(st, attempt, body, used=False)
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
    _execute(st, attempt, lambda g, a, ticket: kept.append(ticket), gate, used=False)
    assert _ticket_reasons(lambda: gate.redeem(kept[0], _read_op())) == ("TICKET_CLOSED",)


def test_a_ticket_is_closed_when_the_callback_raises(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    kept: list[Any] = []

    def body(_gate: Any, authorization: Any, ticket: Any) -> None:
        kept.append(ticket)
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError):
        _execute(st, attempt, body, gate)
    assert _ticket_reasons(lambda: gate.redeem(kept[0], _read_op())) == ("TICKET_CLOSED",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_an_earlier_ticket_does_not_work_in_a_later_execution(world: Any) -> None:
    st, tree = world
    gate = lw.gate(st)
    first = fw.consumed(st, tree)
    kept: list[Any] = []
    _execute(st, first, lambda g, a, ticket: kept.append(ticket), gate, used=False)
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


def test_a_ticket_is_bound_to_the_tree_of_the_operation(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    other = fw.make_tree(tmp_path, st.keys.owner, "other")
    seen: list[Any] = []

    def body(gate: Any, authorization: Any, ticket: Any) -> None:
        wrong = _read_op(workspace=other.digest)
        seen.append(_ticket_reasons(lambda: gate.redeem(ticket, wrong)))

    with pytest.raises(PermitRejectedError):
        _execute(st, attempt, body)  # an outcome for an unused ticket is not trusted
    assert seen == [("TICKET_MISMATCH",)]


def test_completed_without_using_the_ticket_is_an_unknown_outcome(world: Any) -> None:
    """WP08-R04/R06: an outcome is trusted only for a ticket redeemed for it."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    with pytest.raises(PermitRejectedError) as excinfo:
        _execute(st, attempt, lambda g, a, t: None)
    assert excinfo.value.reasons == ("TICKET_NOT_USED",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000


def test_a_definite_failure_needs_no_ticket_use(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    done = _execute(st, attempt, lambda g, a, t: None, used=False)
    assert done.attempt.state is A.FAILED_DEFINITE


# ================================================================ WP08-R06: no false completion


def test_the_separate_steps_refuse_a_file_operation(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    for call in (
        lambda: gate.claim(attempt, **lw.actual(st, attempt)),
        lambda: gate.execute(attempt, run=lambda a: _ok(), **lw.actual(st, attempt)),
    ):
        assert _ticket_reasons(call) == ("FILE_OPERATION_NEEDS_BROKER",)
    # Refused before anything changed.
    assert st.sm.state.attempts[attempt.attempt_id].state is A.PERMIT_CONSUMED
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.ISSUED


@pytest.mark.parametrize("outcome", ["COMPLETED", "FAILED_DEFINITE"])
def test_finish_cannot_settle_a_started_file_operation(world: Any, outcome: str) -> None:
    """A file operation left at DISPATCH_STARTED (as after a process stop): a direct
    finish, whatever it claims, is not a broker result. It becomes unknown."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    # Simulate the earlier start of the broker path (the public steps refuse it).
    started, _ = gate._start(gate._claim(attempt, **lw.actual(st, attempt)).model_copy())
    result = SyntheticResult(outcome=outcome, usage=lw.zero_usage(), final=True)  # type: ignore[arg-type]
    assert _ticket_reasons(lambda: gate.finish(started, result)) == ("FILE_OPERATION_NEEDS_BROKER",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert st.journal.reservation(lw.rsv(attempt)).hold_micro == 1_000


def test_a_redeemed_ticket_alone_does_not_complete(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)

    def redeem_only(gate: Any, authorization: Any, ticket: Any) -> None:
        gate.redeem(ticket, _read_op())

    forged = lambda a: _outcome(a, b"y").model_copy(update={"result_digest": file_digest(b"z")})  # noqa: E731
    with pytest.raises(PermitRejectedError) as excinfo:
        _execute(st, attempt, redeem_only, result=forged)
    assert excinfo.value.reasons == ("FILE_RESULT_UNVERIFIED",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


@pytest.mark.parametrize("field", ["receipt_id", "attempt_id", "action_hash", "operation"])
def test_an_outcome_for_another_operation_is_not_trusted(world: Any, field: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    other = {
        "receipt_id": b.new_id(ReceiptId),
        "attempt_id": b.new_id(AttemptId),
        "action_hash": b.digest("other-action"),
        "operation": _read_op("workspace/out/r.txt"),
    }[field]

    def redeem_only(gate: Any, authorization: Any, ticket: Any) -> None:
        gate.redeem(ticket, _read_op())

    changes: dict[str, Any] = {field: other}
    with pytest.raises(PermitRejectedError) as excinfo:
        _execute(st, attempt, redeem_only, result=lambda a: _outcome(a, b"x", **changes))
    assert excinfo.value.reasons == ("FILE_RESULT_UNVERIFIED",)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_a_write_outcome_must_report_the_approved_content(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True)

    def redeem_only(gate: Any, authorization: Any, ticket: Any) -> None:
        gate.redeem(ticket, _write_op(authorization.descriptor))

    def wrong(a: Any) -> FileOutcome:
        return _outcome(a).model_copy(update={"result_size": 1})

    with pytest.raises(PermitRejectedError) as excinfo:
        _execute(st, attempt, redeem_only, result=wrong)
    assert excinfo.value.reasons == ("FILE_RESULT_UNVERIFIED",)


def test_a_consistent_outcome_after_the_redeem_completes(world: Any) -> None:
    """The gate checks the result against the redeemed binding. A stand-in that fakes a
    fully consistent result is accepted: code inside the process is trusted (R08)."""
    st, tree = world
    attempt = fw.consumed(st, tree)

    def redeem_only(gate: Any, authorization: Any, ticket: Any) -> None:
        gate.redeem(ticket, _read_op())

    done = _execute(st, attempt, redeem_only)
    assert done.attempt.state is A.COMPLETED
    assert done.data == b"x"
