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
   ``WriteChange``: content digest and size, expected state). A mismatch uses it
   up. When the callback returns or raises, the ticket is closed.
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
from typing import Literal

from agent_core.ports.clock import Clock
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety import reports
from agent_core.types.base import FrozenModel
from agent_core.types.errors import PermitRejectedError
from agent_core.types.files import ExecutionTicket, FileOperation, TicketBinding
from agent_core.types.ids import PepInstanceId
from agent_core.types.ledger import (
    ClaimReceipt,
    LedgerBatch,
    MarkActive,
    MarkUnknown,
    ReleaseReservation,
    ReportUsage,
)
from agent_core.types.model import Usage
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
from agent_core.types.tools import ResourceAccess, ToolRegistration
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


class _OpenTicket:
    __slots__ = ("binding", "ticket", "used")

    def __init__(self, ticket: ExecutionTicket, binding: TicketBinding) -> None:
        self.ticket = ticket
        self.binding = binding
        self.used = False


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
        self._open: _OpenTicket | None = None
        self._issued: weakref.WeakSet[ExecutionTicket] = weakref.WeakSet()

    @property
    def pep_instance_id(self) -> PepInstanceId:
        return self._pep

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise PermitRejectedError(PermitReason.GATE_BUSY)
        try:
            yield
        finally:
            self._lock.release()

    # ------------------------------------------------------------ steps

    def claim(
        self, attempt: Attempt, *, content: ActionContent, registration: ToolRegistration | None
    ) -> Attempt:
        with self._exclusive():
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
        with self._exclusive():
            return self._finish(attempt, result)

    def _finish(self, attempt: Attempt, result: SyntheticResult) -> Attempt:
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
        return self._runtime.advance_attempt(attempt, target, evidence, ledger=(op,))

    def execute(
        self,
        attempt: Attempt,
        *,
        content: ActionContent,
        registration: ToolRegistration | None,
        run: Callable[[DispatchAuthorization, ExecutionTicket | None], SyntheticResult],
    ) -> Attempt:
        """Claim, start, run the callback once, record the outcome. The callback gets
        the execution ticket for its one file operation (None without one target)."""
        with self._exclusive():
            claimed = self._claim(attempt, content, registration)
            started, authorization = self._start(claimed)
            try:
                ticket = self._open_ticket(authorization)
                try:
                    result = run(authorization, ticket)
                finally:
                    self._close_ticket()
            except BaseException:
                self._unknown(started)
                raise
            return self._finish(started, result)

    # ------------------------------------------------------------ tickets (WP-08)

    def _open_ticket(self, authorization: DispatchAuthorization) -> ExecutionTicket | None:
        """Issued after the start was committed (the receipt is USED)."""
        descriptor = authorization.descriptor
        if len(descriptor.target_resources) != 1:
            return None
        target = descriptor.target_resources[0]
        change = descriptor.apply_change if target.access is ResourceAccess.WRITE else None
        binding = TicketBinding(
            receipt_id=authorization.receipt.receipt_id,
            attempt_id=descriptor.attempt_id,
            action_hash=authorization.action_hash,
            operation=FileOperation(access=target.access, path=target.path, change=change),
        )
        ticket = ExecutionTicket()
        with self._ticket_lock:
            self._open = _OpenTicket(ticket, binding)
            self._issued.add(ticket)
        return ticket

    def _close_ticket(self) -> None:
        with self._ticket_lock:
            self._open = None

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


def _ref(step: str, receipt: Receipt) -> str:
    return sha256_digest(f"gate:{step}:{receipt.receipt_id}".encode())
