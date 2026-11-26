"""The authorization path of one Attempt (WP-15; E 3.1, E 38; WP-09).

Every model call and every tool call of the agent loop is one Attempt that goes
through each step here, in order, each in its own journal transaction with the
ledger operation the ledger checks:

    PROPOSED -> VALIDATED -> CONTENT_FIXED (reservation id allocated, descriptor
    fixed) -> POLICY_ALLOWED (the ledger re-runs the PDP) -> [APPROVAL_PENDING ->
    APPROVED (an owner's exact approval)] -> BUDGET_RESERVED (the required amount
    from the recorded basis) -> INTENT_RECORDED -> PERMIT_ISSUED -> PERMIT_CONSUMED
    (receipt) -> the execution gate.

Nothing here executes anything, and nothing here grants: the ledger decides at
every step and refuses what it does not allow. A refusal before consumption
ends the Attempt ABORTED_BEFORE_DISPATCH and releases its hold.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from agent_core.ports.clock import Clock
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety.pdp import approval_required
from agent_core.types.errors import PermitRejectedError
from agent_core.types.ids import ApprovalId, PepInstanceId, PermitId, ReceiptId, ReservationId
from agent_core.types.ledger import (
    ActivateReservation,
    AllocateReservation,
    ConfirmApproval,
    ConsumePermit,
    IssuePermit,
    RecordDecision,
    RecordIntent,
    ReleaseReservation,
)
from agent_core.types.records import Attempt, AttemptState, Step
from agent_core.types.safety import (
    ActionContent,
    ActionDescriptor,
    Consumer,
    PermitReason,
    Policy,
)
from agent_core.types.tools import ToolRegistration
from agent_core.types.transitions import (
    AbortEvidence,
    AbortReason,
    ApprovalEvidence,
    ContentFixedEvidence,
    IntentEvidence,
    PermitConsumedEvidence,
    PermitIssuedEvidence,
    PolicyDecisionEvidence,
    ReservationEvidence,
    ValidationEvidence,
)
from agent_core.types.values import Budget, Currency, sha256_digest

A = AttemptState
ALLOCATION_TTL_MS: Final = 60_000
PERMIT_TTL_MS: Final = 30_000
RECEIPT_TTL_MS: Final = 30_000
_BEFORE_CONSUMPTION: Final = frozenset(
    {
        A.PROPOSED,
        A.VALIDATED,
        A.CONTENT_FIXED,
        A.POLICY_ALLOWED,
        A.APPROVAL_PENDING,
        A.APPROVED,
        A.BUDGET_RESERVED,
        A.INTENT_RECORDED,
        A.PERMIT_ISSUED,
    }
)


def _xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


class AttemptPipeline:
    def __init__(
        self,
        *,
        sm: RuntimeStateMachine,
        ledger: LedgerPort,
        clock: Clock,
        pep_instance_id: PepInstanceId,
        new_id: Callable[[type[Any]], Any],
    ) -> None:
        self._sm = sm
        self._ledger = ledger
        self._clock = clock
        self._pep = pep_instance_id
        self._new_id = new_id

    def new_reservation_id(self) -> ReservationId:
        rid: ReservationId = self._new_id(ReservationId)
        return rid

    def start(self, step: Step, schema_ref: str) -> Attempt:
        attempt = self._sm.create_attempt(step)
        evidence = ValidationEvidence(kind="validation", schema_ref=schema_ref)
        return self._sm.advance_attempt(attempt, A.VALIDATED, evidence)

    def fix(self, attempt: Attempt, descriptor: ActionDescriptor) -> Attempt:
        op = AllocateReservation(
            op="allocate",
            reservation_id=descriptor.reservation_id,
            attempt_id=attempt.attempt_id,
            ttl_ms=ALLOCATION_TTL_MS,
        )
        evidence = ContentFixedEvidence(
            kind="content_fixed",
            action_hash=descriptor.action_hash(),
            reservation_id=descriptor.reservation_id,
        )
        return self._sm.advance_attempt(attempt, A.CONTENT_FIXED, evidence, ledger=(op,))

    def decide(
        self,
        attempt: Attempt,
        descriptor: ActionDescriptor,
        content: ActionContent,
        registration: ToolRegistration | None,
        policy: Policy,
    ) -> Attempt:
        """POLICY_ALLOWED. The ledger re-runs the PDP and refuses what it denies; the
        approval requirement stated here is the PDP's own rule."""
        needed = approval_required(policy, descriptor, registration)
        op = RecordDecision(
            op="record_decision",
            attempt_id=attempt.attempt_id,
            descriptor=descriptor,
            content=content,
            registration=registration,
            approval_required=needed,
        )
        evidence = PolicyDecisionEvidence(
            kind="policy_decision",
            decision_ref=sha256_digest(f"decision:{descriptor.action_hash()}".encode()),
            action_hash=descriptor.action_hash(),
            policy_version=policy.policy_version,
            approval_required=needed,
        )
        return self._sm.advance_attempt(attempt, A.POLICY_ALLOWED, evidence, ledger=(op,))

    def await_approval(self, attempt: Attempt) -> Attempt:
        return self._sm.advance_attempt(attempt, A.APPROVAL_PENDING)

    def approve(self, attempt: Attempt, approval_id: ApprovalId) -> Attempt:
        """APPROVED with an owner approval the ledger recorded; never issued here."""
        assert attempt.action_hash is not None  # noqa: S101
        assert attempt.reservation_id is not None  # noqa: S101
        op = ConfirmApproval(
            op="confirm_approval", attempt_id=attempt.attempt_id, approval_id=approval_id
        )
        evidence = ApprovalEvidence(
            kind="approval",
            approval_ref=sha256_digest(f"approval:{approval_id}".encode()),
            action_hash=attempt.action_hash,
            reservation_id=attempt.reservation_id,
        )
        return self._sm.advance_attempt(attempt, A.APPROVED, evidence, ledger=(op,))

    def reserve(self, attempt: Attempt, *, amount: int, cost_cap: int) -> Attempt:
        assert attempt.action_hash is not None  # noqa: S101
        assert attempt.reservation_id is not None  # noqa: S101
        op = ActivateReservation(
            op="activate",
            reservation_id=attempt.reservation_id,
            attempt_id=attempt.attempt_id,
            action_hash=attempt.action_hash,
            amount=_xtu(amount),
            cost_cap=_xtu(cost_cap),
        )
        evidence = ReservationEvidence(
            kind="reservation",
            reservation_id=attempt.reservation_id,
            action_hash=attempt.action_hash,
        )
        return self._sm.advance_attempt(attempt, A.BUDGET_RESERVED, evidence, ledger=(op,))

    def authorize(self, attempt: Attempt, consumer: Consumer) -> Attempt:
        """Intent, permit and its consumption (the receipt) for the execution gate."""
        assert attempt.action_hash is not None  # noqa: S101
        assert attempt.reservation_id is not None  # noqa: S101
        action = attempt.action_hash
        reservation_id = attempt.reservation_id
        intent_ref = sha256_digest(f"intent:{action}".encode())
        attempt = self._sm.advance_attempt(
            attempt,
            A.INTENT_RECORDED,
            IntentEvidence(kind="intent", intent_ref=intent_ref, action_hash=action),
            ledger=(
                RecordIntent(
                    op="record_intent",
                    attempt_id=attempt.attempt_id,
                    reservation_id=attempt.reservation_id,
                    action_hash=action,
                    intent_ref=intent_ref,
                ),
            ),
        )
        permit_id: PermitId = self._new_id(PermitId)
        attempt = self._sm.advance_attempt(
            attempt,
            A.PERMIT_ISSUED,
            PermitIssuedEvidence(
                kind="permit_issued",
                permit_id=permit_id,
                action_hash=action,
                reservation_id=reservation_id,
            ),
            ledger=(
                IssuePermit(
                    op="issue_permit",
                    attempt_id=attempt.attempt_id,
                    permit_id=permit_id,
                    consumer=consumer,
                    ttl_ms=PERMIT_TTL_MS,
                ),
            ),
        )
        permit = self._ledger.permit(str(permit_id))
        if permit is None:
            raise PermitRejectedError(PermitReason.UNKNOWN_PERMIT)
        receipt_id: ReceiptId = self._new_id(ReceiptId)
        return self._sm.advance_attempt(
            attempt,
            A.PERMIT_CONSUMED,
            PermitConsumedEvidence(
                kind="permit_consumed", permit_id=permit_id, receipt_id=receipt_id
            ),
            ledger=(
                ConsumePermit(
                    op="consume_permit",
                    permit=permit,
                    consumer=consumer,
                    actual_action_hash=action,
                    receipt_id=receipt_id,
                    pep_instance_id=self._pep,
                    receipt_ttl_ms=RECEIPT_TTL_MS,
                ),
            ),
        )

    def abort(self, attempt: Attempt, reason: AbortReason) -> Attempt:
        """ABORTED_BEFORE_DISPATCH before the permit is consumed; releases the hold.
        A consumed permit is reported by the live gate (``abort_not_dispatched``)."""
        current = self._sm.state.attempts[attempt.attempt_id]
        if current.state not in _BEFORE_CONSUMPTION:
            raise PermitRejectedError(PermitReason.ALREADY_STARTED)
        ops: tuple[ReleaseReservation, ...] = ()
        if current.reservation_id is not None and current.state is not A.VALIDATED:
            ops = (
                ReleaseReservation(
                    op="release",
                    reservation_id=current.reservation_id,
                    attempt_id=current.attempt_id,
                    report=None,
                ),
            )
        evidence = AbortEvidence(kind="abort", reason=reason, not_dispatched_ref=None)
        return self._sm.advance_attempt(current, A.ABORTED_BEFORE_DISPATCH, evidence, ledger=ops)
