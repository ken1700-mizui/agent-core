"""The sandbox tool through the real Phase 1 path (B1A-04, WP2-03/04 tests).

A synthetic ``sandbox_run`` tool whose input is the sandbox program (program,
image, profile, workspace, deadline, grace). Its pin is added to the policy, so
the approved ActionDescriptor fixes the program that P2 may run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.runtime.sandbox_execution import (
    SANDBOX_TOOL_ID,
    SandboxOperationResult,
    SandboxProgram,
    run_sandbox_operation,
)
from agent_core.safety.descriptor import pin_of
from agent_core.types.ids import ReceiptId, RequestId
from agent_core.types.records import Attempt
from agent_core.types.tools import (
    FieldSpec,
    FieldType,
    ObjectSchema,
    ToolEffectClass,
    ToolRegistration,
    canonical_json,
)
from agent_core_broker.server import Broker
from support import broker_world as bw
from support import builders as b
from support import ledger_world as lw
from support import safety_world as s
from support import tool_world as tw


def _digest_field(name: str) -> FieldSpec:
    return FieldSpec(name=name, type=FieldType.STRING, required=True, max_length=80)


SANDBOX_MANIFEST = tw.manifest(
    **tw.synthetic_body(
        SANDBOX_TOOL_ID,
        input_schema=ObjectSchema(
            fields=(
                FieldSpec(
                    name="deadline_ms", type=FieldType.INTEGER, required=True, maximum=3_600_000
                ),
                FieldSpec(name="grace_ms", type=FieldType.INTEGER, required=True, maximum=60_000),
                _digest_field("image_digest"),
                _digest_field("program_ref"),
                _digest_field("sandbox_profile_hash"),
                _digest_field("workspace_ref"),
            ),
            max_bytes=1_024,
        ),
        # Contained: no effect outside the sandbox; any workspace change is a
        # separate, separately approved file operation.
        effect_class=ToolEffectClass.READ_ONLY,
    )
)
REGISTRY = s.registry(
    s.READ_MANIFEST,
    s.WRITE_MANIFEST,
    s.COSTLY_MANIFEST,
    s.SYNTH_MANIFEST,
    s.SYNTH_COSTLY_MANIFEST,
    s.SYNTH_CHANGE_MANIFEST,
    SANDBOX_MANIFEST,
)
SANDBOX_TOOL: ToolRegistration = REGISTRY.get(SANDBOX_TOOL_ID, "1.0.0")
PINS = tuple(sorted((*s.ALL_PINS, pin_of(SANDBOX_TOOL)), key=lambda p: (p.tool_id, p.version)))


def program(name: str = "exit0", **overrides: Any) -> SandboxProgram:
    values: dict[str, Any] = {
        "sandbox_profile_hash": bw.PROFILE,
        "image_digest": b.digest("image"),
        "workspace_ref": b.digest("workspace"),
        "program_ref": bw.fake_ref(name),
        "deadline_ms": 60_000,
        "grace_ms": 100,
    }
    values.update(overrides)
    return SandboxProgram(**values)


def program_input(p: SandboxProgram) -> str:
    return canonical_json(dict(p.__dict__)).decode()


def setup(tmp_path: Path) -> tuple[lw.Setup, Broker]:
    st = lw.setup(tmp_path, policy=s.policy(tools=PINS))
    st.grant_tools = PINS
    return st, bw.make_broker(tmp_path / "p2")


def consumed(st: lw.Setup, *, program: str = "exit0", **overrides: Any) -> Attempt:
    p = globals()["program"](program, **overrides)
    step = lw.step_in(st)
    attempt = lw.issued(st, step, registration=SANDBOX_TOOL, canonical_input=program_input(p))
    return lw.consume(st, attempt)


def run(
    st: lw.Setup,
    broker: Broker,
    attempt: Attempt,
    *,
    program: SandboxProgram | None = None,
    max_polls: int = 3,
    client: Any = None,
) -> SandboxOperationResult:
    return run_sandbox_operation(
        gate=lw.gate(st),
        sandbox=client
        if client is not None
        else bw.make_client(broker, epoch=st.journal.owner_epoch),
        attempt=attempt,
        expected=program,
        new_request_id=lambda: b.new_id(RequestId),
        max_polls=max_polls,
        **lw.actual(st, attempt),
    )


def run_with(st: lw.Setup, broker: Broker, attempt: Attempt, **kw: Any) -> SandboxOperationResult:
    """``run`` with any of the runner's monitor arguments (WP2-04)."""
    return run_sandbox_operation(
        gate=lw.gate(st),
        sandbox=kw.pop("client", None) or bw.make_client(broker, epoch=st.journal.owner_epoch),
        attempt=attempt,
        new_request_id=lambda: b.new_id(RequestId),
        **lw.actual(st, attempt),
        **kw,
    )


def receipt_of(attempt: Attempt) -> ReceiptId:
    if attempt.receipt_id is None:
        raise AssertionError("the attempt has no receipt yet")
    return attempt.receipt_id
