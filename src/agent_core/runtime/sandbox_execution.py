"""One sandbox operation through the execution gate and the P2 broker (WP2-02;
B1A-03, B1A-04).

Order (Phase 1 gate path, unchanged; C1 2.1, sandbox design 7.6):

0. The program is read from the approved content (B1A-04): the input of the
   ``sandbox_run`` tool, which the gate's claim checks against the approved
   ActionDescriptor (and so the Permit and Receipt). A caller's expectation
   (``expected``) that differs, or content that is not a sandbox program, ends
   the attempt before dispatch (``ARTIFACT_MISMATCH``).
1. ``prepare`` through P2 before the claim. If P2 cannot be used or refuses,
   the attempt ends ABORTED_BEFORE_DISPATCH (``SANDBOX_UNAVAILABLE``) through
   the live gate's not-dispatched report, which releases the hold. Nothing ran.
2. The gate claims and starts (the Receipt is used once). In the callback, P2
   is asked to start with that Receipt, then polled until nothing runs; at the
   end of the wait it is asked to stop. The result is collected.
3. What makes a result definite (B1A-03):

   - P2 refused the start, authenticated, before recording it (a refusal of
     ``PRE_ACCEPTANCE_REFUSALS``): nothing ran: FAILED_DEFINITE;
   - finished, and P2's evidence is ``CONTAINED``: exit 0 COMPLETED, other
     exits FAILED_DEFINITE;
   - stopped and confirmed, and ``CONTAINED``: FAILED_DEFINITE.

   Anything else (P2 lost, a duplicate start, a failure after the start, an
   unconfirmed stop, no containment evidence, a mismatched answer) raises: the
   gate records OUTCOME_UNKNOWN and keeps the hold. Nothing is retried. A
   confirmed stop alone is never a definite result.
4. The result carries a measured ``wall_ms`` (Journal v4): P2's for a finished
   execution, otherwise P1's own monotonic time. No time is made up.
5. P2 is asked to reap. A failure there does not change the recorded outcome;
   it is returned (``reap_error``) for the caller to report.

No untrusted code is run by this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from agent_core.ports.sandbox import SandboxPort
from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.types.errors import AgentCoreError, SandboxUnavailableError
from agent_core.types.ids import RequestId
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.provenance import OutcomeKind, OutcomeProvenanceFields
from agent_core.types.records import Attempt
from agent_core.types.safety import ActionContent, DispatchAuthorization, ToolContent
from agent_core.types.sandbox import (
    MAX_DEADLINE_MS,
    MAX_GRACE_MS,
    NOTHING_RUNS,
    CollectRequest,
    Containment,
    PrepareRequest,
    ReapRequest,
    SandboxState,
    StartRequest,
    StartStatus,
    StatusRequest,
    StopRequest,
)
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.tools import ToolRegistration
from agent_core.types.transitions import AbortReason
from agent_core.types.values import sha256_digest

SANDBOX_TOOL_ID = "sandbox_run"
_TEXT_FIELDS = ("image_digest", "program_ref", "sandbox_profile_hash", "workspace_ref")
_LIMITS = {"deadline_ms": (1, MAX_DEADLINE_MS), "grace_ms": (0, MAX_GRACE_MS)}
# Authenticated refusals P2 gives before it records a start: nothing ran.
PRE_ACCEPTANCE_REFUSALS = frozenset(
    {
        "MALFORMED",
        "AUTHENTICATION",
        "PEER",
        "STALE_EPOCH",
        "PROFILE_REFUSED",
        "PROGRAM_REFUSED",
        "RECORD_UNAVAILABLE",
        "DEADLINE_UNAVAILABLE",
    }
)


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


def sandbox_program_of(
    content: ActionContent, registration: ToolRegistration | None
) -> SandboxProgram:
    """The program the approved content names. Raises ValueError otherwise."""
    if registration is None or registration.manifest.tool_id != SANDBOX_TOOL_ID:
        raise ValueError("not the sandbox tool")
    if not isinstance(content, ToolContent):
        raise ValueError("a sandbox program is tool content")
    try:
        data = parse_strict_json(content.canonical_input)
    except StrictJsonError as exc:
        raise ValueError("the sandbox program is not strict JSON") from exc
    if not isinstance(data, dict) or set(data) != {*_TEXT_FIELDS, *_LIMITS}:
        raise ValueError("the sandbox program has other fields")
    for name in _TEXT_FIELDS:
        if not isinstance(data[name], str) or not data[name].startswith("sha256:"):
            raise ValueError(f"{name} is not a digest")
    for name, (low, high) in _LIMITS.items():
        value = data[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} is out of range")
    return SandboxProgram(**data)


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
    new_request_id: Callable[[], RequestId],
    expected: SandboxProgram | None = None,
    max_polls: int = 50,
    wait: Callable[[], None] = lambda: None,
    monotonic_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
    stopped: Callable[[], bool] = lambda: False,
    p1_margin_ms: int = 5_000,
    admission: Callable[[], None] | None = None,
) -> SandboxOperationResult:
    """P1's sandbox monitor (WP2-04) is the poll loop: one ``wait`` per sandbox
    cycle, until nothing runs, the owner stops (``stopped``), or P1's own limit
    (deadline + grace + ``p1_margin_ms``, P1's monotonic clock) passes; P2's
    deadline stop comes first. ``max_polls`` bounds the loop for tests."""
    if attempt.receipt_id is None:
        raise ValueError("a sandbox operation starts from a consumed permit")
    try:
        program = sandbox_program_of(content, registration)
    except ValueError:
        program = None  # recorded as the abort reason below
    if program is None or (expected is not None and expected != program):
        aborted = gate.abort_not_dispatched(attempt, AbortReason.ARTIFACT_MISMATCH)
        return SandboxOperationResult(aborted, None)
    if admission is not None:
        try:
            admission()  # S-R03: the monitor's liveness, before any start
        except AgentCoreError:
            aborted = gate.abort_not_dispatched(attempt, AbortReason.SANDBOX_UNAVAILABLE)
            return SandboxOperationResult(aborted, None)
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
        try:
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
                    grace_ms=program.grace_ms,
                )
            )
        except SandboxUnavailableError as exc:
            if exc.refusal in PRE_ACCEPTANCE_REFUSALS:
                measured["wall_ms"] = max(0, monotonic_ms() - began)
                measured["exit_ref"] = sha256_digest(f"not_started:{exc.refusal}".encode())
                return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)
            raise
        if started.status is not StartStatus.STARTED:
            raise SandboxOutcomeUnknownError(
                "P2 did not start this Receipt now", sent=True, refusal=started.status.value
            )
        state = started.state
        containment = Containment.UNKNOWN
        polls = 0
        limit = program.deadline_ms + program.grace_ms + p1_margin_ms

        def watching() -> bool:
            within = polls < max_polls and monotonic_ms() - began < limit
            return state not in NOTHING_RUNS and within and not stopped()

        while watching():
            wait()
            polls += 1
            status = sandbox.status(
                StatusRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                )
            )
            state, containment = status.state, status.containment
        owner_stop = stopped()
        if state not in NOTHING_RUNS or owner_stop:
            answer = sandbox.stop(
                StopRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                    grace_ms=program.grace_ms,
                )
            )
            if owner_stop:
                # P-4 item 4: after an owner stop the attempt stays unknown, with its hold.
                raise SandboxOutcomeUnknownError("stopped by the owner", sent=True)
            if not answer.stop_confirmed:
                raise SandboxOutcomeUnknownError("the stop was not confirmed", sent=True)
            state, containment = answer.state, answer.containment
        if state is SandboxState.FINISHED:
            collected = sandbox.collect(
                CollectRequest(
                    request_id=new_request_id(),
                    attempt_id=attempt.attempt_id,
                    receipt_id=receipt.receipt_id,
                )
            )
            if collected.containment is not Containment.CONTAINED:
                raise SandboxOutcomeUnknownError(
                    "no evidence that effects stayed inside", sent=True
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
        if state is SandboxState.STOPPED and containment is Containment.CONTAINED:
            measured["wall_ms"] = max(0, monotonic_ms() - began)  # P1's measurement
            measured["exit_ref"] = sha256_digest(f"stopped:{receipt.receipt_id}".encode())
            return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)
        raise SandboxOutcomeUnknownError(
            f"P2 reports {state.value} with containment {containment.value}", sent=True
        )

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
        reap_error = f"{exc.code}: {exc}"  # a never-started Receipt cannot be reaped
    return SandboxOperationResult(done, reap_error)
