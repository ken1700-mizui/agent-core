"""Deterministic fake sandbox backend (WP2-02). Runs nothing.

It only keeps, in memory, what a sandbox would report for a fixed set of
programs, so the broker's contract can be tested without Docker. Its state is
lost when the broker stops, like containers whose broker died; the execution
record is what survives.

Programs (``program_ref(name)``):

- ``exit0``: finishes at once with exit 0; ``exit1``: finishes with exit 1;
- ``hang``: runs until stopped; a stop is confirmed;
- ``stubborn``: runs until stopped, but a stop is never confirmed.

Wall times are the fixed simulated values of the table, not measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import SandboxState
from agent_core.types.values import sha256_digest


@dataclass(frozen=True)
class FakeProgram:
    name: str
    exit_code: int | None  # None: runs until stopped
    wall_ms: int
    stoppable: bool = True


FAKE_PROGRAMS = {
    p.name: p
    for p in (
        FakeProgram("exit0", 0, 7),
        FakeProgram("exit1", 1, 5),
        FakeProgram("hang", None, 0),
        FakeProgram("stubborn", None, 0, stoppable=False),
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

    def knows_program(self, ref: str) -> bool:
        return ref in _BY_REF

    def program_wall_ms(self, name: str) -> int:
        return FAKE_PROGRAMS[name].wall_ms

    def start(self, receipt_id: ReceiptId, ref: str) -> None:
        program = _BY_REF[ref]
        self.runs[receipt_id] = self.runs.get(receipt_id, 0) + 1
        state = SandboxState.RUNNING if program.exit_code is None else SandboxState.FINISHED
        output = sha256_digest(f"fake-output/{program.name}/{receipt_id}".encode())
        self.containers[receipt_id] = FakeContainer(program, state, output)

    def stop(self, receipt_id: ReceiptId, grace_ms: int) -> bool:
        """True when nothing runs any more (confirmed)."""
        container = self.containers[receipt_id]
        if container.state is SandboxState.RUNNING and container.program.stoppable:
            container.state = SandboxState.STOPPED
        return container.state is not SandboxState.RUNNING

    def reap(self, receipt_id: ReceiptId) -> None:
        self.containers.pop(receipt_id, None)
