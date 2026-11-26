"""One file operation through the execution gate (WP-08; C1 8.3, E.Z33).

Order:

1. ``preflight`` before the claim: a rejected work tree or the C1.S19 refusal
   ends the attempt ABORTED_BEFORE_DISPATCH through the live gate's
   not-dispatched report, which releases the hold in the same transaction.
   Nothing was started and no target was opened.
2. Otherwise the gate claims and starts; inside the callback the broker performs
   the one operation, redeeming the execution ticket. A refusal there (raised
   before any target is opened, by the broker's contract) is a definite failure;
   anything else raised leaves the outcome unknown (the gate keeps the hold).
"""

from __future__ import annotations

from typing import NamedTuple

from agent_core.ports.files import FileOperationPort
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.types.errors import (
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

_S19 = "SAFE_FILE_ACCESS_UNAVAILABLE"


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
        files.preflight(descriptor, ledger.policy(descriptor.tenant_id))
    except WorkspaceRejectedError:
        aborted = gate.abort_not_dispatched(attempt, AbortReason.WORKSPACE_REJECTED)
        return FileOperationResult(aborted, None)
    except FileOperationRefusedError as exc:
        if exc.reasons != (_S19,):
            raise
        aborted = gate.abort_not_dispatched(attempt, AbortReason.SAFE_FILE_ACCESS_UNAVAILABLE)
        return FileOperationResult(aborted, None)

    read: list[bytes] = []

    def run(
        authorization: DispatchAuthorization, ticket: ExecutionTicket | None
    ) -> SyntheticResult:
        if ticket is None:
            # Not one file target: nothing is attempted.
            return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)
        try:
            if write_content is None:
                read.append(files.read(authorization, ticket, gate.redeem))
            else:
                files.write(authorization, ticket, gate.redeem, write_content)
        except FileOperationRefusedError:
            return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)
        return SyntheticResult(outcome="COMPLETED", usage=_no_usage(), final=True)

    done = gate.execute(attempt, content=content, registration=registration, run=run)
    return FileOperationResult(done, read[0] if read else None)
