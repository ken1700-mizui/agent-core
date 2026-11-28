"""Sandbox request and result types (WP2-01 port types; used by WP2-02).

The ``SandboxPort`` is P1's view of the P2 broker (C1 2.1; sandbox design 7.6,
7.6.1). Only identifiers, digests and small numbers: no file content, no
environment value, no credential.

- ``start`` carries the consumed Receipt: P2 starts at most once per Receipt.
- A stop request is not a confirmed stop: ``stop_confirmed`` only with a state
  in which nothing runs any more.
- An exit code exists only for a finished execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import AttemptId, PermitId, ReceiptId, RequestId
from agent_core.types.values import Count, Digest, Epoch

# Candidate values (sandbox design 7.3); not guarantees until tested.
MAX_DEADLINE_MS = 3_600_000
MAX_GRACE_MS = 60_000
DeadlineMs = Annotated[int, Field(strict=True, ge=1, le=MAX_DEADLINE_MS)]
GraceMs = Annotated[int, Field(strict=True, ge=0, le=MAX_GRACE_MS)]
ExitCode = Annotated[int, Field(strict=True, ge=0, le=255)]


class SandboxState(StrEnum):
    """What P2's execution record says about one Receipt."""

    NOT_SEEN = "NOT_SEEN"  # no start was accepted for it
    ACCEPTED = "ACCEPTED"  # recorded before anything ran; the run may or may not have begun
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    STOPPED = "STOPPED"  # stopped and confirmed: nothing runs
    REAPED = "REAPED"  # resources removed; the record stays


NOTHING_RUNS = frozenset({SandboxState.FINISHED, SandboxState.STOPPED, SandboxState.REAPED})


class StartStatus(StrEnum):
    STARTED = "STARTED"
    DUPLICATE = "DUPLICATE"  # the Receipt was already used: nothing new ran
    REFUSED = "REFUSED"


class PrepareRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    sandbox_profile_hash: Digest
    image_digest: Digest
    workspace_ref: Digest


class PrepareResult(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    prepared: bool


class StartRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    permit_id: PermitId
    receipt_id: ReceiptId
    owner_epoch: Epoch
    sandbox_profile_hash: Digest
    # The fixed program to run (WP2-03/04: the project's own diagnostics only).
    program_ref: Digest
    # Wall-clock limit kept by P2 (its own monotonic clock), sandbox design 7.4.1.
    deadline_ms: DeadlineMs


class StartResult(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId
    status: StartStatus
    state: SandboxState


class StatusRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId


class StatusResult(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId
    state: SandboxState
    exit_code: ExitCode | None

    @model_validator(mode="after")
    def _exit(self) -> Self:
        finished = self.state in (SandboxState.FINISHED, SandboxState.REAPED)
        if self.state is SandboxState.FINISHED and self.exit_code is None:
            raise ValueError("a finished execution has an exit code")
        if not finished and self.exit_code is not None:
            raise ValueError("only a finished execution has an exit code")
        return self


class StopRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId
    grace_ms: GraceMs


class StopResult(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    state: SandboxState
    stop_confirmed: bool

    @model_validator(mode="after")
    def _confirmed(self) -> Self:
        if self.stop_confirmed and self.state not in NOTHING_RUNS:
            raise ValueError("a stop is confirmed only when nothing runs")
        return self


class CollectRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId


class CollectResult(FrozenModel):
    """The execution's result: references only (the copy-out is WP2-04)."""

    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId
    exit_code: ExitCode
    output_ref: Digest
    wall_ms: Count


class ReapRequest(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    receipt_id: ReceiptId


class ReapResult(FrozenModel):
    request_id: RequestId
    attempt_id: AttemptId
    state: SandboxState
