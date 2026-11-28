"""What the broker needs from a sandbox backend (fake in WP2-02, Docker in WP2-03)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import Containment, SandboxState


@dataclass(frozen=True)
class Observation:
    """What the backend sees now. ``exit_code``, ``output_ref`` and ``wall_ms``
    are meaningful only for FINISHED."""

    state: SandboxState
    exit_code: int | None
    output_ref: str | None
    wall_ms: int | None


class SandboxBackend(Protocol):
    def knows_program(self, ref: str) -> bool: ...

    def start(self, receipt_id: ReceiptId, ref: str, deadline_ms: int) -> Containment: ...

    def observe(self, receipt_id: ReceiptId) -> Observation | None: ...

    def stop(self, receipt_id: ReceiptId, grace_ms: int) -> bool: ...

    def reap(self, receipt_id: ReceiptId) -> None: ...
