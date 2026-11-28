"""One sandbox operation through the execution gate and the P2 broker (WP2-02).

Order (Phase 1 gate path, unchanged; C1 2.1, sandbox design 7.6):

1. ``prepare`` through P2 before the claim. If P2 cannot be used or refuses,
   the attempt ends ABORTED_BEFORE_DISPATCH (``SANDBOX_UNAVAILABLE``) through
   the live gate's not-dispatched report, which releases the hold. Nothing ran.
2. The gate claims and starts (the Receipt is used once). In the callback, P2
   is asked to start with that Receipt, then polled until nothing runs; at the
   end of the wait it is asked to stop. The result is collected. Anything else
   (P2 lost, a refused or duplicate start, an unconfirmed stop, a mismatched
   answer) raises: the gate records the outcome as unknown and keeps the hold.
   Nothing is retried.
3. A finished execution: exit 0 is COMPLETED, any other exit FAILED_DEFINITE.
   A confirmed stop (deadline or end of wait) is FAILED_DEFINITE. The result
   carries a measured ``wall_ms`` (Journal v4): P2's for a finished execution,
   otherwise P1's own monotonic time from the start request to the confirmed
   stop. No time is made up.
4. P2 is asked to reap. A failure there does not change the recorded outcome;
   it is returned (``reap_error``) for the caller to report.

The fake backend runs nothing; no untrusted code is run by this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from agent_core.ports.sandbox import SandboxPort
from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import RequestId
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.provenance import OutcomeKind, OutcomeProvenanceFields
from agent_core.types.records import Attempt
from agent_core.types.safety import ActionContent, DispatchAuthorization
from agent_core.types.sandbox import (
    NOTHING_RUNS,
    CollectRequest,
    PrepareRequest,
    ReapRequest,
    SandboxState,
    StartRequest,
    StartStatus,
    StatusRequest,
    StopRequest,
)
from agent_core.types.tools import ToolRegistration
from agent_core.types.transitions import AbortReason
from agent_core.types.values import sha256_digest


@dataclass(frozen=True)
class SandboxProgram:
    sandbox_profile_hash: str
    image_digest: str
    workspace_ref: str
    program_ref: str
    deadline_ms: int
    grace_ms: int


class SandboxOperationResult(NamedTuple):
    attempt: Attempt
    # Set when P2 could not reap after the outcome was recorded.
    reap_error: str | None


class SandboxOutcomeUnknownError(SandboxUnavailableError):
    """The execution's outcome cannot be confirmed (the gate keeps the hold)."""


def _no_usage() -> Usage:
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=None,
        cost_status=CostStatus.UNKNOWN,
        synthetic=True,
    )


def run_sandbox_operation(
    *,
    gate: SyntheticExecutionGate,
    sandbox: SandboxPort,
    attempt: Attempt,
    content: ActionContent,
    registration: ToolRegistration | None,
    program: SandboxProgram,
    new_request_id: Callable[[], RequestId],
    max_polls: int = 50,
    wait: Callable[[], None] = lambda: None,
    monotonic_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
) -> SandboxOperationResult:
    if attempt.receipt_id is None:
        raise ValueError("a sandbox operation starts from a consumed permit")
    try:
        prepared = sandbox.prepare(
            PrepareRequest(
                request_id=new_request_id(),
                attempt_id=attempt.attempt_id,
                sandbox_profile_hash=program.sandbox_profile_hash,
                image_digest=program.image_digest,
                workspace_ref=program.workspace_ref,
            )
        ).prepared
    except SandboxUnavailableError:
        prepared = False  # recorded as the abort reason below
    if not prepared:
        aborted = gate.abort_not_dispatched(attempt, AbortReason.SANDBOX_UNAVAILABLE)
        return SandboxOperationResult(aborted, None)

    measured: dict[str, int | str] = {}

    def run(authorization: DispatchAuthorization) -> SyntheticResult:
        receipt = authorization.receipt
        began = monotonic_ms()
        started = sandbox.start(
            StartRequest(
                request_id=new_request_id(),
                attempt_id=attempt.attempt_id,
                permit_id=receipt.permit_id,
                receipt_id=receipt.receipt_id,
                owner_epoch=receipt.owner_epoch,
                sandbox_profile_hash=program.sandbox_profile_hash,
                program_ref=program.program_ref,
                deadline_ms=program.deadline_ms,
            )
        )
        if started.status is not StartStatus.STARTED:
            raise SandboxOutcomeUnknownError(
                "P2 did not start this Receipt now", sent=True, refusal=started.status.value
            )
        state = started.state
        polls = 0
        while state not in NOTHING_RUNS and polls < max_polls:
            wait()
            polls += 1
            state = sandbox.status(
                StatusRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                )
            ).state
        if state not in NOTHING_RUNS:
            stopped = sandbox.stop(
                StopRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                    grace_ms=program.grace_ms,
                )
            )
            if not stopped.stop_confirmed:
                raise SandboxOutcomeUnknownError("the stop was not confirmed", sent=True)
            state = stopped.state
        measured["receipt_id"] = receipt.receipt_id
        if state is SandboxState.FINISHED:
            collected = sandbox.collect(
                CollectRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                )
            )
            measured["wall_ms"] = collected.wall_ms
            measured["exit_ref"] = sha256_digest(
                f"exit:{collected.exit_code}:{collected.output_ref}".encode()
            )
            return SyntheticResult(
                outcome="COMPLETED" if collected.exit_code == 0 else "FAILED_DEFINITE",
                usage=_no_usage(),
                final=True,
                result_digest=collected.output_ref,
            )
        if state is SandboxState.STOPPED:
            measured["wall_ms"] = max(0, monotonic_ms() - began)  # P1's measurement
            measured["exit_ref"] = sha256_digest(f"stopped:{receipt.receipt_id}".encode())
            return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)
        raise SandboxOutcomeUnknownError(f"P2 reports {state.value}", sent=True)

    def outcome(result: SyntheticResult) -> OutcomeProvenanceFields:
        kind = (
            OutcomeKind.COMPLETED if result.outcome == "COMPLETED" else OutcomeKind.FAILED_DEFINITE
        )
        return OutcomeProvenanceFields(
            result=kind,
            wall_ms=int(measured["wall_ms"]),
            exit_ref=str(measured["exit_ref"]),
            verification_ref=None,
        )

    done = gate.execute(
        attempt, content=content, registration=registration, run=run, outcome=outcome
    )
    reap_error: str | None = None
    try:
        sandbox.reap(
            ReapRequest(
                request_id=new_request_id(),
                attempt_id=attempt.attempt_id,
                receipt_id=attempt.receipt_id,
            )
        )
    except SandboxUnavailableError as exc:
        reap_error = f"{exc.code}: {exc}"
    return SandboxOperationResult(done, reap_error)
