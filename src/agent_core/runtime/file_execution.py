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

from collections.abc import Iterable
from typing import NamedTuple

from agent_core.ports.files import FileOperationPort, PreparedFileOperation
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.execution_gate import SyntheticExecutionGate
from agent_core.tool.registry import ToolRegistry
from agent_core.types.errors import (
    FileOperationRefusedError,
    PermitRejectedError,
    ToolOutputError,
    WorkspaceRejectedError,
)
from agent_core.types.records import Attempt, AttemptState
from agent_core.types.safety import ActionContent, PermitReason
from agent_core.types.tools import ToolRegistration, ValidatedToolOutput, canonical_json
from agent_core.types.transitions import AbortReason

# Refusals of ``prepare`` that end the attempt before dispatch, and their reasons.
_BEFORE_DISPATCH: dict[tuple[str, ...], AbortReason] = {
    ("SAFE_FILE_ACCESS_UNAVAILABLE",): AbortReason.SAFE_FILE_ACCESS_UNAVAILABLE,
    ("WORKSPACE_BUSY",): AbortReason.WORKSPACE_BUSY,
    ("ARTIFACT_MISMATCH",): AbortReason.ARTIFACT_MISMATCH,
}


class FileOperationResult(NamedTuple):
    attempt: Attempt
    # A completed read: the bytes, and the builtin read_file output validated
    # against the tool's output schema and max_output_bytes.
    data: bytes | None
    output: ValidatedToolOutput | None


def _read_output(
    registry: ToolRegistry, registration: ToolRegistration | None, data: bytes
) -> ValidatedToolOutput | None:
    """The builtin read_file output ({"content", "truncated"}), or None when the bytes
    are not UTF-8 text or do not fit the tool's output schema."""
    if registration is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    output = canonical_json({"content": text, "truncated": False}).decode()
    try:
        return registry.validate_output(registration, output)
    except ToolOutputError:
        return None


def run_file_operation(
    *,
    gate: SyntheticExecutionGate,
    ledger: LedgerPort,
    files: FileOperationPort,
    attempt: Attempt,
    content: ActionContent,
    registration: ToolRegistration | None,
    write_content: bytes | None,
    registry: ToolRegistry,
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
        return FileOperationResult(aborted, None, None)
    except FileOperationRefusedError as exc:
        reason = _BEFORE_DISPATCH.get(exc.reasons)
        if reason is None:
            raise
        return FileOperationResult(gate.abort_not_dispatched(attempt, reason), None, None)
    try:
        return _execute(gate, prepared, attempt, content, registration, write_content, registry)
    finally:
        prepared.close()


def _execute(
    gate: SyntheticExecutionGate,
    prepared: PreparedFileOperation,
    attempt: Attempt,
    content: ActionContent,
    registration: ToolRegistration | None,
    write_content: bytes | None,
    registry: ToolRegistry,
) -> FileOperationResult:
    outputs: list[ValidatedToolOutput] = []

    def accept_read(data: bytes) -> bool:
        output = _read_output(registry, registration, data)
        if output is None:
            return False  # read, nothing changed, but the result is not usable
        outputs.append(output)
        return True

    # WP08-R06: the gate calls the broker and checks its result itself.
    done = gate.execute_file(
        attempt,
        content=content,
        registration=registration,
        prepared=prepared,
        write_content=write_content,
        accept_read=accept_read,
    )
    if done.data is not None and outputs and done.attempt.state is AttemptState.COMPLETED:
        return FileOperationResult(done.attempt, done.data, outputs[0])
    return FileOperationResult(done.attempt, None, None)


_RECOVERABLE = (AttemptState.DISPATCH_STARTED, AttemptState.OUTCOME_UNKNOWN)


def recovery_receipts(
    *, ledger: LedgerPort, attempts: Iterable[Attempt], workspace_ref: str
) -> tuple[str, ...]:
    """WP08-R07: the receipts of writes on this tree whose outcome is not known
    (started, or recorded as unknown). Only their temporary files may be removed;
    the outcomes themselves stay for reconciliation (WP-12)."""
    found: list[str] = []
    for attempt in attempts:
        if attempt.state not in _RECOVERABLE or attempt.receipt_id is None:
            continue
        descriptor = ledger.recorded_descriptor(attempt.attempt_id)
        if (
            descriptor is not None
            and descriptor.apply_change is not None
            and descriptor.workspace_ref == workspace_ref
        ):
            found.append(str(attempt.receipt_id))
    return tuple(sorted(found))
