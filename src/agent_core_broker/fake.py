"""Deterministic fake sandbox backend (WP2-02; B1A-03). Runs nothing.

It only keeps, in memory, what a sandbox would report for a fixed set of
programs, so the broker's contract can be tested without Docker. Its state is
lost when the broker stops, like containers whose broker died; the execution
record is what survives.

Programs (``program_ref(name)``):

- ``exit0``, ``exit1``: finish at once with exit 0 or 1;
- ``hang``: runs until stopped; a stop is confirmed;
- ``stubborn``: runs until stopped, but a stop is never confirmed;
- ``leaky``, ``leaky_hang``: like ``exit0`` and ``hang``, but without evidence
  that effects stayed inside the sandbox (containment UNKNOWN).

Wall times are the fixed simulated values of the table, not measurements.
Test hooks: ``refuse_next_program`` (the next start is refused as an unknown
program) and ``start_delay_s`` (a slow backend call).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import Containment, SandboxState
from agent_core.types.values import sha256_digest
from agent_core_broker.backend import Observation


@dataclass(frozen=True)
class FakeProgram:
    name: str
    exit_code: int | None  # None: runs until stopped
    wall_ms: int
    stoppable: bool = True
    contained: bool = True


FAKE_PROGRAMS = {
    p.name: p
    for p in (
        FakeProgram("exit0", 0, 7),
        FakeProgram("exit1", 1, 5),
        FakeProgram("hang", None, 0),
        FakeProgram("stubborn", None, 0, stoppable=False),
        FakeProgram("leaky", 0, 7, contained=False),
        FakeProgram("leaky_hang", None, 0, contained=False),
    )
}


def program_ref(name: str) -> str:
    return sha256_digest(f"agent-core/fake-program/{name}".encode())


_BY_REF = {program_ref(name): program for name, program in FAKE_PROGRAMS.items()}


@dataclass
class FakeContainer:
    program: FakeProgram
    state: SandboxState
    output_ref: str


@dataclass
class FakeSandbox:
    containers: dict[ReceiptId, FakeContainer] = field(default_factory=dict)
    runs: dict[ReceiptId, int] = field(default_factory=dict)
    refuse_next_program: bool = False
    start_delay_s: float = 0.0

    def knows_program(self, ref: str) -> bool:
        if self.refuse_next_program:
            self.refuse_next_program = False
            return False
        return ref in _BY_REF

    def containment(self, ref: str) -> Containment:
        program = _BY_REF.get(ref)
        contained = program is not None and program.contained
        return Containment.CONTAINED if contained else Containment.UNKNOWN

    def program_wall_ms(self, name: str) -> int:
        return FAKE_PROGRAMS[name].wall_ms

    def start(self, receipt_id: ReceiptId, ref: str, deadline_ms: int) -> Containment:
        """Run (here: pretend to run) and say whether effects stay inside."""
        del deadline_ms  # P2 enforces it (tick); the fake has no clock of its own
        if self.start_delay_s:
            time.sleep(self.start_delay_s)
        program = _BY_REF[ref]
        self.runs[receipt_id] = self.runs.get(receipt_id, 0) + 1
        state = SandboxState.RUNNING if program.exit_code is None else SandboxState.FINISHED
        output = sha256_digest(f"fake-output/{program.name}/{receipt_id}".encode())
        self.containers[receipt_id] = FakeContainer(program, state, output)
        return self.containment(ref)

    def stop(self, receipt_id: ReceiptId, grace_ms: int) -> bool:
        """True when nothing runs any more (confirmed)."""
        del grace_ms
        container = self.containers[receipt_id]
        if container.state is SandboxState.RUNNING and container.program.stoppable:
            container.state = SandboxState.STOPPED
        return container.state is not SandboxState.RUNNING

    def observe(self, receipt_id: ReceiptId) -> Observation | None:
        container = self.containers.get(receipt_id)
        if container is None:
            return None
        return Observation(
            state=container.state,
            exit_code=container.program.exit_code,
            output_ref=container.output_ref,
            wall_ms=container.program.wall_ms,
        )

    def reap(self, receipt_id: ReceiptId) -> None:
        self.containers.pop(receipt_id, None)
