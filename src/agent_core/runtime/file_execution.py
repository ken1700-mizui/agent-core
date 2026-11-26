"""One file operation through the execution gate (WP-08; ADR-0008; C1 8.3, E.Z33).

Order:

1. ``prepare`` before the claim. A rejected tree, the S19 refusal or a busy tree
   ends the attempt ABORTED_BEFORE_DISPATCH through the live gate's
   not-dispatched report, which releases the hold in the same transaction.
   Nothing was started and no target was opened.
2. Otherwise the gate claims and starts; inside the callback the prepared
   operation performs the one operation, redeeming the execution ticket.
   - ``FileOperationFailedError`` with effect NONE: a definite failure (the
     broker's own record says no target changed).
   - effect UNKNOWN, or any other exception: the outcome is unknown; the gate
     keeps the hold. Nothing is retried and nothing is restored.
3. The prepared operation is closed (fds and the writer lock) in every case.
"""

from __future__ import annotations

from typing import NamedTuple

from agent_core.ports.files import FileOperationPort, PreparedFileOperation
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.types.errors import (
    FileEffect,
    FileOperationFailedError,
    FileOperationRefusedError,
    PermitRejectedError,
    WorkspaceRejectedError,
)
from agent_core.types.files import ExecutionTicket
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.records import Attempt
from agent_core.types.safety import ActionContent, DispatchAuthorization, PermitReason
from agent_core.types.tools import ToolRegistration
from agent_core.types.transitions import AbortReason

# Refusals of ``prepare`` that end the attempt before dispatch, and their reasons.
_BEFORE_DISPATCH: dict[tuple[str, ...], AbortReason] = {
    ("SAFE_FILE_ACCESS_UNAVAILABLE",): AbortReason.SAFE_FILE_ACCESS_UNAVAILABLE,
    ("WORKSPACE_BUSY",): AbortReason.WORKSPACE_BUSY,
}


class FileOperationResult(NamedTuple):
    attempt: Attempt
    data: bytes | None


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


def _failed() -> SyntheticResult:
    return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)


def run_file_operation(
    *,
    gate: SyntheticExecutionGate,
    ledger: LedgerPort,
    files: FileOperationPort,
    attempt: Attempt,
    content: ActionContent,
    registration: ToolRegistration | None,
    write_content: bytes | None,
) -> FileOperationResult:
    descriptor = ledger.recorded_descriptor(attempt.attempt_id)
    if descriptor is None:
        raise PermitRejectedError(PermitReason.DECISION_MISSING)
    if (descriptor.apply_change is not None) != (write_content is not None):
        raise FileOperationRefusedError("WRITE_CONTENT_DOES_NOT_FIT")
    try:
        prepared = files.prepare(descriptor, ledger.policy(descriptor.tenant_id))
    except WorkspaceRejectedError:
        aborted = gate.abort_not_dispatched(attempt, AbortReason.WORKSPACE_REJECTED)
        return FileOperationResult(aborted, None)
    except FileOperationRefusedError as exc:
        reason = _BEFORE_DISPATCH.get(exc.reasons)
        if reason is None:
            raise
        return FileOperationResult(gate.abort_not_dispatched(attempt, reason), None)
    try:
        return _execute(gate, prepared, attempt, content, registration, write_content)
    finally:
        prepared.close()


def _execute(
    gate: SyntheticExecutionGate,
    prepared: PreparedFileOperation,
    attempt: Attempt,
    content: ActionContent,
    registration: ToolRegistration | None,
    write_content: bytes | None,
) -> FileOperationResult:
    read: list[bytes] = []

    def run(
        authorization: DispatchAuthorization, ticket: ExecutionTicket | None
    ) -> SyntheticResult:
        if ticket is None:
            # Not one file target: nothing is attempted.
            return _failed()
        try:
            if write_content is None:
                read.append(prepared.read(authorization, ticket, gate.redeem))
            else:
                prepared.write(authorization, ticket, gate.redeem, write_content)
        except FileOperationFailedError as exc:
            if exc.effect is FileEffect.NONE:
                return _failed()
            raise  # a target may have changed: unknown, never retried
        return SyntheticResult(outcome="COMPLETED", usage=_no_usage(), final=True)

    done = gate.execute(attempt, content=content, registration=registration, run=run)
    return FileOperationResult(done, read[0] if read else None)
