"""Synthetic in-process execution gate (WP09-R03, R04; E 3.2; C1 7.4-7.5).

The single place where a consumed permit turns into one execution:

1. ``claim``: the receipt goes ISSUED -> CLAIMED in the journal transaction of
   ``DISPATCH_AUTHORIZED``. The actual operation (its arguments, targets and
   executable) must rebuild the recorded descriptor.
2. ``start``: in the transaction of ``DISPATCH_STARTED`` the receipt becomes USED
   (once) and every live check runs again: stop, revocation, owner epoch, gate
   instance, expiry, elapsed time and approval.
3. The callback runs once, with a ``DispatchAuthorization`` that is information
   only (nothing accepts it back) and, for an operation on one file target, an
   execution ticket (WP-08). The ticket is issued after the start is committed,
   is redeemed by ``redeem`` at most once, only while the callback runs and only
   for the exact operation it was bound to (access, path and, for a write, the
   ``WriteChange``: content digest and size, expected state) in the verified
   tree (``workspace_ref``). A mismatch uses it up. When the callback returns or
   raises, the ticket is closed. A callback that reports COMPLETED without having
   redeemed its ticket is not trusted: the outcome becomes unknown
   (``TICKET_NOT_USED``). The separate ``start``/``finish`` steps never hand out a
   ticket, so no broker file operation can run through them.
4. ``finish`` records the outcome with a MAC-signed execution report; the ledger
   derives the cost itself. A callback that raises leaves the outcome unknown
   (hold kept).

Stops sent through the gate take the same lock, so a stop cannot fall between a
committed start and the callback in this process. The lock is not re-entrant: a
callback cannot start another execution or stop a scope (``GATE_BUSY``). The
journal's SQLite connection belongs to one thread, so other threads cannot use
it (they fail closed).

Not claimed: atomicity between the database and the real operation. A process
that stops right before or after the callback leaves DISPATCH_STARTED (or
PERMIT_CONSUMED), which recovery treats as an unknown outcome with the hold
kept. Real file operations (WP-08) are refused in this environment (C1.S19).
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal, NamedTuple

from agent_core.ports.clock import Clock
from agent_core.ports.files import PreparedFileOperation
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety import reports
from agent_core.safety.workspace import file_digest
from agent_core.types.base import FrozenModel
from agent_core.types.errors import FileEffect, FileOperationFailedError, PermitRejectedError
from agent_core.types.files import ExecutionTicket, FileOperation, FileOutcome, TicketBinding
from agent_core.types.ids import PepInstanceId
from agent_core.types.ledger import (
    ClaimReceipt,
    LedgerBatch,
    MarkActive,
    MarkUnknown,
    ReleaseReservation,
    ReportUsage,
)
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.provenance import OutcomeProvenanceFields
from agent_core.types.records import Attempt, AttemptState
from agent_core.types.safety import (
    ActionContent,
    DispatchAuthorization,
    ExecutionReport,
    LedgerKeys,
    NotDispatchedReport,
    PermitReason,
    Receipt,
    ReceiptState,
)
from agent_core.types.tools import ResourceAccess, ToolEffectClass, ToolRegistration
from agent_core.types.transitions import (
    AbortEvidence,
    AbortReason,
    DispatchEvidence,
    OutcomeEvidence,
)
from agent_core.types.values import sha256_digest

A = AttemptState
_PLACEHOLDER_MAC = "0" * 64


class SyntheticResult(FrozenModel):
    """What a synthetic callback reports: its outcome and this call's usage."""

    outcome: Literal["COMPLETED", "FAILED_DEFINITE"]
    usage: Usage
    final: bool
    result_digest: str | None = None


class _OpenTicket:
    __slots__ = ("binding", "redeemed", "ticket", "used")

    def __init__(self, ticket: ExecutionTicket, binding: TicketBinding) -> None:
        self.ticket = ticket
        self.binding = binding
        self.used = False  # presented once (matched or not)
        self.redeemed = False  # presented for exactly its operation


class SyntheticExecutionGate:
    def __init__(
        self,
        *,
        runtime: RuntimeStateMachine,
        ledger: LedgerPort,
        keys: LedgerKeys,
        pep_instance_id: PepInstanceId,
        clock: Clock,
    ) -> None:
        if type(keys) is not LedgerKeys:
            raise TypeError("the gate needs the ledger keys")
        self._runtime = runtime
        self._ledger = ledger
        self._keys = keys
        self._pep = pep_instance_id
        self._clock = clock
        self._lock = threading.Lock()
        self._usage_events = 0
        # WP-08 tickets: at most one open, inside the running callback.
        self._ticket_lock = threading.Lock()
        self._writes_held = False
        self._open: _OpenTicket | None = None
        self._issued: weakref.WeakSet[ExecutionTicket] = weakref.WeakSet()

    @property
    def pep_instance_id(self) -> PepInstanceId:
        return self._pep

    # ------------------------------------------------------------ the verification hold

    @property
    def writes_held(self) -> bool:
        return self._writes_held

    @contextmanager
    def verification_hold(self) -> Iterator[None]:
        """WP15-R06: while held, this gate (the Runtime's one PEP for tool calls)
        refuses every operation that changes something, so no cooperating writer
        can change the files between the final verification reads and the
        completion record. Reads go on. Not reentrant."""
        if self._writes_held:
            raise PermitRejectedError(PermitReason.GATE_BUSY)
        self._writes_held = True
        try:
            yield
        finally:
            self._writes_held = False

    def refuse_write_while_held(self) -> None:
        if self._writes_held:
            raise PermitRejectedError(PermitReason.GATE_BUSY)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise PermitRejectedError(PermitReason.GATE_BUSY)
        try:
            yield
        finally:
            self._lock.release()

    # ------------------------------------------------------------ steps

    def _refuse_file_operation(self, attempt: Attempt) -> None:
        """WP08-R06: an operation on a file target is settled only through
        ``execute_file`` (the broker path). The separate steps refuse it."""
        descriptor = self._ledger.recorded_descriptor(attempt.attempt_id)
        if descriptor is not None and descriptor.target_resources:
            raise PermitRejectedError(PermitReason.FILE_OPERATION_NEEDS_BROKER)

    def claim(
        self, attempt: Attempt, *, content: ActionContent, registration: ToolRegistration | None
    ) -> Attempt:
        with self._exclusive():
            self._refuse_file_operation(attempt)
            return self._claim(attempt, content, registration)

    def _claim(
        self, attempt: Attempt, content: ActionContent, registration: ToolRegistration | None
    ) -> Attempt:
        receipt = self._receipt(attempt)
        op = ClaimReceipt(
            op="claim_receipt",
            receipt=receipt,
            pep_instance_id=self._pep,
            content=content,
            registration=registration,
        )
        evidence = DispatchEvidence(
            kind="dispatch", receipt_id=receipt.receipt_id, report_ref=_ref("claim", receipt)
        )
        return self._runtime.advance_attempt(attempt, A.DISPATCH_AUTHORIZED, evidence, ledger=(op,))

    def start(self, attempt: Attempt) -> tuple[Attempt, DispatchAuthorization]:
        with self._exclusive():
            self._refuse_file_operation(attempt)
            return self._start(attempt)

    def _start(self, attempt: Attempt) -> tuple[Attempt, DispatchAuthorization]:
        receipt = self._receipt(attempt)
        assert attempt.reservation_id is not None  # noqa: S101 (set from BUDGET_RESERVED)
        op = MarkActive(
            op="mark_active",
            reservation_id=attempt.reservation_id,
            attempt_id=attempt.attempt_id,
            receipt_id=receipt.receipt_id,
            pep_instance_id=self._pep,
        )
        evidence = DispatchEvidence(
            kind="dispatch", receipt_id=receipt.receipt_id, report_ref=_ref("start", receipt)
        )
        started = self._runtime.advance_attempt(attempt, A.DISPATCH_STARTED, evidence, ledger=(op,))
        descriptor = self._ledger.recorded_descriptor(attempt.attempt_id)
        if descriptor is None:
            raise PermitRejectedError(PermitReason.DECISION_MISSING)
        return started, DispatchAuthorization(
            receipt=receipt, action_hash=descriptor.action_hash(), descriptor=descriptor
        )

    def finish(self, attempt: Attempt, result: SyntheticResult) -> Attempt:
        """Record a synthetic operation's outcome. For a file operation a direct
        report is not a broker result: it is recorded as unknown, never as done."""
        with self._exclusive():
            try:
                self._refuse_file_operation(attempt)
            except PermitRejectedError:
                if attempt.state is A.DISPATCH_STARTED:
                    self._unknown(attempt)
                raise
            return self._finish(attempt, result)

    def _finish(
        self,
        attempt: Attempt,
        result: SyntheticResult,
        provenance: OutcomeProvenanceFields | None = None,
    ) -> Attempt:
        report = self.execution_report(attempt, result)
        self._usage_events += 1
        op = ReportUsage(
            op="report_usage",
            reservation_id=report.reservation_id,
            attempt_id=attempt.attempt_id,
            usage_event_id=f"gate_{self._pep.lower().replace('-', '_')}_{self._usage_events}",
            report=report,
        )
        target = A.COMPLETED if result.outcome == "COMPLETED" else A.FAILED_DEFINITE
        evidence = OutcomeEvidence(
            kind="outcome", receipt_id=report.receipt_id, report_ref=reports.report_ref(report)
        )
        return self._runtime.advance_attempt(
            attempt, target, evidence, ledger=(op,), provenance=provenance
        )

    def execute(
        self,
        attempt: Attempt,
        *,
        content: ActionContent,
        registration: ToolRegistration | None,
        run: Callable[[DispatchAuthorization], SyntheticResult],
        outcome: Callable[[SyntheticResult], OutcomeProvenanceFields] | None = None,
    ) -> Attempt:
        """Claim, start, run a synthetic callback once, record the outcome. Not for
        file operations (``execute_file``). ``outcome`` (WP2-02) gives the Journal
        v4 record of the live result (the measured time), when there is one."""
        with self._exclusive():
            self._refuse_file_operation(attempt)
            if registration is not None and registration.manifest.effect_class not in (
                ToolEffectClass.READ_ONLY,
                ToolEffectClass.MODEL_CALL,
            ):
                self.refuse_write_while_held()
            claimed = self._claim(attempt, content, registration)
            started, authorization = self._start(claimed)
            try:
                result = run(authorization)
                provenance = None if outcome is None else outcome(result)
            except BaseException:
                self._unknown(started)
                raise
            return self._finish(started, result, provenance)

    def execute_file(
        self,
        attempt: Attempt,
        *,
        content: ActionContent,
        registration: ToolRegistration | None,
        prepared: PreparedFileOperation,
        write_content: bytes | None,
        accept_read: Callable[[bytes], bool],
    ) -> FileExecution:
        """WP08-R06: the one path that settles a file operation. The gate claims,
        starts, issues the ticket, calls the prepared broker operation itself and
        checks its result against the ticket the broker redeemed (the binding:
        receipt, attempt, action_hash, tree, path, access, change) and against
        the bytes (a read) or the approved change (a write). Only then is the
        attempt COMPLETED. A broker failure with effect NONE is a definite
        failure; anything else, or a result that does not check, is unknown."""
        with self._exclusive():
            descriptor = self._ledger.recorded_descriptor(attempt.attempt_id)
            if descriptor is None or len(descriptor.target_resources) != 1:
                raise PermitRejectedError(PermitReason.FILE_OPERATION_NEEDS_BROKER)
            if (descriptor.apply_change is not None) != (write_content is not None):
                raise PermitRejectedError(PermitReason.FILE_OPERATION_NEEDS_BROKER)
            if write_content is not None:
                self.refuse_write_while_held()
            claimed = self._claim(attempt, content, registration)
            started, authorization = self._start(claimed)
            try:
                return self._run_file(started, authorization, prepared, write_content, accept_read)
            except BaseException:
                if self._runtime.state.attempts[started.attempt_id].state is A.DISPATCH_STARTED:
                    self._unknown(started)
                raise

    def _run_file(
        self,
        started: Attempt,
        authorization: DispatchAuthorization,
        prepared: PreparedFileOperation,
        write_content: bytes | None,
        accept_read: Callable[[bytes], bool],
    ) -> FileExecution:
        ticket = self._open_ticket(authorization)
        if ticket is None:
            raise PermitRejectedError(PermitReason.FILE_OPERATION_NEEDS_BROKER)
        data: bytes | None = None
        try:
            try:
                if write_content is None:
                    outcome, data = prepared.read(authorization, ticket, self.redeem)
                else:
                    outcome = prepared.write(authorization, ticket, self.redeem, write_content)
            finally:
                binding = self._close_ticket()
        except FileOperationFailedError as exc:
            if exc.effect is not FileEffect.NONE:
                raise
            return FileExecution(self._finish(started, _definite()), None)
        if binding is None:
            raise PermitRejectedError(PermitReason.TICKET_NOT_USED)
        if not _checks(outcome, binding, data):
            raise PermitRejectedError(PermitReason.FILE_RESULT_UNVERIFIED)
        if data is not None and not accept_read(data):
            return FileExecution(self._finish(started, _definite()), None)
        # A read's bytes, checked above against the redeemed ticket, are recorded
        # by digest with the signed result (WP15-R07).
        result = _completed() if data is None else _completed(file_digest(data))
        done = self._finish(started, result)
        return FileExecution(done, data)

    # ------------------------------------------------------------ tickets (WP-08)

    def _open_ticket(self, authorization: DispatchAuthorization) -> ExecutionTicket | None:
        """Issued after the start was committed (the receipt is USED)."""
        descriptor = authorization.descriptor
        if len(descriptor.target_resources) != 1 or descriptor.workspace_ref is None:
            return None
        target = descriptor.target_resources[0]
        change = descriptor.apply_change if target.access is ResourceAccess.WRITE else None
        binding = TicketBinding(
            receipt_id=authorization.receipt.receipt_id,
            attempt_id=descriptor.attempt_id,
            action_hash=authorization.action_hash,
            operation=FileOperation(
                access=target.access,
                path=target.path,
                change=change,
                workspace_ref=descriptor.workspace_ref,
            ),
        )
        ticket = ExecutionTicket()
        with self._ticket_lock:
            self._open = _OpenTicket(ticket, binding)
            self._issued.add(ticket)
        return ticket

    def _close_ticket(self) -> TicketBinding | None:
        """Close the open ticket; its binding if it was redeemed for its operation."""
        with self._ticket_lock:
            current, self._open = self._open, None
            if current is None or not current.redeemed:
                return None
            return current.binding

    def redeem(self, ticket: ExecutionTicket, operation: FileOperation) -> TicketBinding:
        """Use the open ticket once for the operation it was bound to. Any refusal
        names a fixed reason; a mismatch uses the ticket up."""
        with self._ticket_lock:
            current = self._open
            if current is None or current.ticket is not ticket:
                if type(ticket) is ExecutionTicket and ticket in self._issued:
                    raise PermitRejectedError(PermitReason.TICKET_CLOSED)
                raise PermitRejectedError(PermitReason.TICKET_UNKNOWN)
            if current.used:
                raise PermitRejectedError(PermitReason.TICKET_USED)
            current.used = True
            if type(operation) is not FileOperation or operation != current.binding.operation:
                raise PermitRejectedError(PermitReason.TICKET_MISMATCH)
            current.redeemed = True
            return current.binding

    def _unknown(self, attempt: Attempt) -> None:
        """The callback failed after the start: the outcome is unknown (hold kept)."""
        assert attempt.reservation_id is not None  # noqa: S101 (set from BUDGET_RESERVED)
        op = MarkUnknown(
            op="mark_unknown", reservation_id=attempt.reservation_id, attempt_id=attempt.attempt_id
        )
        receipt = self._receipt(attempt)
        evidence = OutcomeEvidence(
            kind="outcome", receipt_id=receipt.receipt_id, report_ref=_ref("unknown", receipt)
        )
        self._runtime.advance_attempt(attempt, A.OUTCOME_UNKNOWN, evidence, ledger=(op,))

    def abort_not_dispatched(
        self, attempt: Attempt, reason: AbortReason = AbortReason.STOPPED
    ) -> Attempt:
        """Report, as the live gate, that a consumed permit's operation was not
        started, and release its hold in the same transaction (E.Z6, WP09-R04)."""
        with self._exclusive():
            report = self.not_dispatched_report(attempt)
            assert attempt.reservation_id is not None  # noqa: S101 (set from BUDGET_RESERVED)
            op = ReleaseReservation(
                op="release",
                reservation_id=attempt.reservation_id,
                attempt_id=attempt.attempt_id,
                report=report,
            )
            evidence = AbortEvidence(
                kind="abort", reason=reason, not_dispatched_ref=reports.report_ref(report)
            )
            return self._runtime.advance_attempt(
                attempt, A.ABORTED_BEFORE_DISPATCH, evidence, ledger=(op,)
            )

    def stop(self, signed: tuple[LedgerBatch, str]) -> None:
        """An owner stop (or other owner batch) under the gate lock."""
        batch, mac = signed
        with self._exclusive():
            self._ledger.apply_owner_ledger(batch, owner_mac=mac)

    # ------------------------------------------------------------ reports

    def not_dispatched_report(self, attempt: Attempt) -> NotDispatchedReport:
        receipt = self._receipt(attempt)
        state = self._ledger.receipt_state(receipt.receipt_id)
        if state is ReceiptState.USED:
            raise PermitRejectedError(PermitReason.ALREADY_STARTED)
        if receipt.pep_instance_id != self._pep:
            raise PermitRejectedError(PermitReason.PEP_MISMATCH)
        report = NotDispatchedReport(
            report_version=1,
            receipt_id=receipt.receipt_id,
            permit_id=receipt.permit_id,
            attempt_id=receipt.attempt_id,
            reservation_id=receipt.reservation_id,
            owner_epoch=self._ledger.owner_epoch,
            pep_instance_id=self._pep,
            reported_at_ms=self._clock.now_ms(),
            mac=_PLACEHOLDER_MAC,
        )
        signed = reports.sign(self._keys.permit, report)
        assert isinstance(signed, NotDispatchedReport)  # noqa: S101 (same type back)
        return signed

    def execution_report(self, attempt: Attempt, result: SyntheticResult) -> ExecutionReport:
        receipt = self._receipt(attempt)
        report = ExecutionReport(
            report_version=1,
            receipt_id=receipt.receipt_id,
            attempt_id=receipt.attempt_id,
            reservation_id=receipt.reservation_id,
            owner_epoch=self._ledger.owner_epoch,
            pep_instance_id=self._pep,
            outcome=result.outcome,
            usage=result.usage,
            final=result.final,
            result_digest=result.result_digest,
            mac=_PLACEHOLDER_MAC,
        )
        signed = reports.sign(self._keys.permit, report)
        assert isinstance(signed, ExecutionReport)  # noqa: S101 (same type back)
        return signed

    def _receipt(self, attempt: Attempt) -> Receipt:
        if attempt.receipt_id is None:
            raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
        receipt = self._ledger.receipt(attempt.receipt_id)
        if receipt is None:
            raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
        return receipt


class FileExecution(NamedTuple):
    attempt: Attempt
    # The bytes of a completed read (checked against the broker's result).
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


def _completed(result_digest: str | None = None) -> SyntheticResult:
    return SyntheticResult(
        outcome="COMPLETED", usage=_no_usage(), final=True, result_digest=result_digest
    )


def _definite() -> SyntheticResult:
    return SyntheticResult(outcome="FAILED_DEFINITE", usage=_no_usage(), final=True)


def _checks(outcome: object, binding: TicketBinding, data: bytes | None) -> bool:
    """The broker's result is for exactly the redeemed binding, and its digest and
    size are those of the bytes read, or of the approved change."""
    if type(outcome) is not FileOutcome or outcome.binding != binding:
        return False
    change = binding.operation.change
    if change is not None:
        return data is None and (outcome.result_digest, outcome.result_size) == (
            change.content_digest,
            change.content_size,
        )
    return (
        type(data) is bytes
        and outcome.result_size == len(data)
        and outcome.result_digest == file_digest(data)
    )


def _ref(step: str, receipt: Receipt) -> str:
    return sha256_digest(f"gate:{step}:{receipt.receipt_id}".encode())
