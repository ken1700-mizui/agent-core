"""Permits, receipts, stop and revocation (WP-09C; C1 7.4, 7.5, 11.4; E 3.2-3.4, 38;
C1.S4, S20, S22; E.Z6, Z42, Z43; AUDIT-01). Synthetic, in-process PEP only."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_ledger
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.types.cost import ReservationState
from agent_core.types.errors import (
    ApprovalRejectedError,
    AuditRecursionError,
    AuditWriteError,
    BudgetExceededError,
    CostError,
    IllegalTransitionError,
    JournalWriteOutcomeUnknownError,
    LedgerPairingError,
    OwnerAuthorizationError,
    PermitRejectedError,
    PolicyDeniedError,
    ReservationStateError,
    RuntimeReloadRequiredError,
    SafetyError,
    TransitionPreconditionError,
)
from agent_core.types.ids import AttemptId, PepInstanceId, PermitId, TenantId
from agent_core.types.ledger import (
    DenyResource,
    IssuePermit,
    LedgerBatch,
    RecordApproval,
    RecoverInFlight,
    ReleaseReservation,
    ResumeScope,
    RevokeAccess,
    RevokeApproval,
    StopScope,
)
from agent_core.types.records import AttemptState
from agent_core.types.safety import (
    Consumer,
    PermitState,
    ReceiptState,
    SignedApproval,
    StopScopeType,
    StopStatus,
)
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s

A = AttemptState
R = ReservationState


@pytest.fixture
def st(tmp_path: Path) -> Any:
    setup = lw.setup(tmp_path)
    yield setup
    if not setup.journal.closed:
        setup.journal.close()


def _step(st: Any) -> Any:
    return lw.step_in(st)


def _reasons(error: Any) -> set[str]:
    return set(error.value.reasons)


def _stop(st: Any, kind: StopScopeType, scope_id: str) -> None:
    st.owner(StopScope(op="stop_scope", scope_type=kind, scope_id=scope_id))


# ---------------------------------------------------------------- the normal path


def test_issue_consume_claim_and_start(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    permit = st.journal.permit(attempt.permit_id)
    assert permit.body.action_hash == attempt.action_hash
    assert permit.body.reservation_id == attempt.reservation_id
    assert permit.body.owner_epoch == st.journal.owner_epoch
    assert st.journal.permit_state(attempt.permit_id) is PermitState.ISSUED
    attempt = lw.consume(st, attempt)
    assert attempt.state is A.PERMIT_CONSUMED
    assert st.journal.permit_state(attempt.permit_id) is PermitState.CONSUMED
    assert st.journal.reservation(attempt.reservation_id).state is R.CONSUMED
    receipt = st.journal.receipt(attempt.receipt_id)
    assert (receipt.permit_id, receipt.pep_instance_id) == (attempt.permit_id, st.pep)
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.ISSUED
    gate = lw.gate(st)
    attempt = gate.claim(attempt, **lw.actual(st, attempt))
    # The start is committed by the gate; the authorization it returns is information.
    attempt, authorization = gate.start(attempt)
    assert authorization.descriptor.action_hash() == attempt.action_hash
    assert attempt.state is A.DISPATCH_STARTED
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    assert st.journal.reservation(attempt.reservation_id).state is R.ACTIVE
    actions = [r.action for r in st.journal.audit_records()]
    assert {"PERMIT_ISSUED", "PERMIT_CONSUMED", "RECEIPT_CLAIMED"} <= set(actions)


def test_permit_and_audit_carry_no_key_or_body(st: Any) -> None:
    attempt = lw.consume(st, lw.issued(st, _step(st)))
    st.journal.close()
    data = st.path.read_bytes()
    for key in (st.keys.permit, st.keys.owner):
        assert key.reveal() not in data
        assert key.reveal().hex().encode() not in data
    assert repr(st.keys) == "<LedgerKeys>"
    assert attempt.permit_id is not None


# ---------------------------------------------------------------- approvals


def test_an_approval_is_required_for_a_write(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    assert attempt.approval_required is True
    # The runtime cannot skip APPROVED, and the ledger refuses to reserve without it.
    with pytest.raises(TransitionPreconditionError):
        st.sm.advance_attempt(
            attempt,
            A.BUDGET_RESERVED,
            w.evidence_for(attempt, A.BUDGET_RESERVED),
            ledger=(lw.activate_op(attempt, 1_000),),
        )
    pending = st.sm.advance_attempt(attempt, A.APPROVAL_PENDING)
    from agent_core.types.ids import ApprovalId
    from agent_core.types.ledger import ConfirmApproval

    unknown = ConfirmApproval(
        op="confirm_approval", attempt_id=pending.attempt_id, approval_id=b.new_id(ApprovalId)
    )
    with pytest.raises(PermitRejectedError) as excinfo:
        st.sm.advance_attempt(
            pending, A.APPROVED, w.evidence_for(pending, A.APPROVED), ledger=(unknown,)
        )
    assert "APPROVAL_MISSING" in _reasons(excinfo)
    approved = lw.approved(st, pending)
    assert approved.state is A.APPROVED


def test_forged_approvals_are_not_recorded(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    forged = SignedApproval(approval=s.approval(lw.action_of(attempt)), owner_mac="0" * 64)
    with pytest.raises(ApprovalRejectedError):
        st.journal.apply_ledger((RecordApproval(op="record_approval", signed=forged),))
    other_key = OwnerApprovalChannel(s.new_key()).approve(s.approval(lw.action_of(attempt)))
    with pytest.raises(ApprovalRejectedError):
        st.journal.apply_ledger((RecordApproval(op="record_approval", signed=other_key),))


def test_an_approval_for_other_content_does_not_apply(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    other = s.write_descriptor(
        lw.ref_of(st, attempt), reservation_id=attempt.reservation_id, cost_cap=lw.xtu(4_999)
    )
    signed = st.channel.approve(s.approval(other.action_hash()))
    st.journal.apply_ledger((RecordApproval(op="record_approval", signed=signed),))
    with pytest.raises(PermitRejectedError, match="APPROVAL_INVALID"):
        lw.approved(st, attempt, signed.approval.approval_id)


def test_an_expired_or_revoked_approval_stops_consumption(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    approval_id = lw.record_approval(st, attempt, expires_at_ms=s.NOW + 20_000)
    attempt = lw.approved(st, attempt, approval_id)
    attempt = lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 1_000)), ttl_ms=30_000)
    st.clock.now += 25_000
    with pytest.raises(PermitRejectedError, match="APPROVAL_INVALID"):
        lw.consume(st, attempt)
    st.clock.now -= 25_000
    st.owner(RevokeApproval(op="revoke_approval", approval_id=approval_id))
    with pytest.raises(PermitRejectedError):
        lw.consume(st, attempt)
    assert st.journal.permit_state(attempt.permit_id) is not PermitState.CONSUMED


def test_approval_use_is_counted_at_consumption(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    approval_id = lw.record_approval(st, attempt)
    attempt = lw.approved(st, attempt, approval_id)
    attempt = lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 1_000)))
    assert st.journal.approval_uses(approval_id) == 0
    lw.consume(st, attempt)
    assert st.journal.approval_uses(approval_id) == 1


# ---------------------------------------------------------------- tampering


def test_a_changed_operation_cannot_consume(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    with pytest.raises(PermitRejectedError, match="ACTION_MISMATCH"):
        lw.consume(st, attempt, actual_action_hash=b.digest("another operation"))
    assert st.journal.permit_state(attempt.permit_id) is PermitState.ISSUED


def test_a_descriptor_that_is_not_the_fixed_one_is_not_decided(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes)
    fixed = st.descriptors[attempt.attempt_id]
    tampered = fixed.model_copy(update={"cost_cap": lw.xtu(4_000)})
    # The runtime refuses evidence for another action (WP-04) ...
    st.descriptors[attempt.attempt_id] = tampered
    with pytest.raises(TransitionPreconditionError):
        lw.allowed(st, attempt)
    # ... and the ledger refuses a descriptor that is not the fixed one, even with
    # evidence for the right action.
    evidence = w.evidence_for(attempt, A.POLICY_ALLOWED)
    op = lw.decision_op(st, attempt, approval_required=False)
    with pytest.raises(LedgerPairingError, match="DESCRIPTOR_DIFFERS"):
        st.sm.advance_attempt(attempt, A.POLICY_ALLOWED, evidence, ledger=(op,))
    assert fixed.action_hash() == attempt.action_hash


@pytest.mark.parametrize(
    "change",
    [
        {"mac": "f" * 64},
        {"body": {"expires_at_ms": s.NOW + 10**6}},
        {"body": {"consumer": Consumer.GATEWAY}},
    ],
)
def test_a_tampered_permit_is_refused(st: Any, change: dict[str, Any]) -> None:
    attempt = lw.issued(st, _step(st))
    permit = st.journal.permit(attempt.permit_id)
    if "body" in change:
        permit = permit.model_copy(update={"body": permit.body.model_copy(update=change["body"])})
    else:
        permit = permit.model_copy(update=change)
    with pytest.raises(PermitRejectedError) as excinfo:
        lw.consume(st, attempt, permit=permit)
    assert _reasons(excinfo) & {"MAC_INVALID", "PERMIT_DIFFERS"}
    assert st.journal.permit_state(attempt.permit_id) is PermitState.ISSUED


def test_another_consumer_or_another_attempt_is_refused(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    with pytest.raises(PermitRejectedError, match="CONSUMER_MISMATCH"):
        lw.consume(st, attempt, consumer=Consumer.GATEWAY)
    # A permit of another attempt (another run) presented for this attempt.
    other = lw.issued(st, _step(st))
    foreign = st.journal.permit(other.permit_id)
    with pytest.raises((PermitRejectedError, LedgerPairingError)):
        lw.consume(st, attempt, permit=foreign)
    assert st.journal.permit_state(other.permit_id) is PermitState.ISSUED


def test_permits_cannot_outlive_the_policy_limit(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.intent(st, lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000))
    with pytest.raises(PermitRejectedError, match="TTL_TOO_LONG"):
        lw.issue(st, attempt, ttl_ms=600_000)


def test_an_expired_permit_is_refused(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    st.clock.now += 30_000
    with pytest.raises(PermitRejectedError, match="EXPIRED"):
        lw.consume(st, attempt)


# ---------------------------------------------------------------- AUDIT-01


def test_an_allocated_id_alone_cannot_get_a_permit(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes))
    # The runtime refuses to skip BUDGET_RESERVED, and the ledger refuses on its own.
    with pytest.raises((TransitionPreconditionError, IllegalTransitionError)):
        lw.intent(st, attempt)
    st.journal.close()
    raw = sqlite3.connect(st.path, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        with pytest.raises(PermitRejectedError, match="RESERVATION_INVALID"):
            sqlite_ledger.apply_op(
                raw.cursor(),
                IssuePermit(
                    op="issue_permit",
                    attempt_id=attempt.attempt_id,
                    permit_id=b.new_id(PermitId),
                    consumer=Consumer.BROKER,
                    ttl_ms=30_000,
                ),
                now_ms=s.NOW,
                ctx=sqlite_ledger.LedgerContext(keys=st.keys, owner_epoch=1),
            )
        raw.execute("ROLLBACK")
    finally:
        raw.close()


def test_a_failed_reservation_means_no_execution(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, run=500)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes))
    with pytest.raises(BudgetExceededError):
        lw.reserve(st, attempt, 1_000)
    aborted = lw.abort(st, st.sm.state.attempts[attempt.attempt_id], release=False)
    assert aborted.state is A.ABORTED_BEFORE_DISPATCH
    assert aborted.permit_id is None
    assert st.journal.reservation(attempt.reservation_id).state is R.ID_ALLOCATED


@pytest.mark.parametrize("change", ["content", "cost_cap", "attempt"])
def test_changing_content_cost_or_attempt_voids_the_approval(st: Any, change: str) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, write=True))
    descriptor = st.descriptors[attempt.attempt_id]
    at = lw.ref_of(st, attempt)
    if change == "content":
        other = s.write_descriptor(
            at,
            canonical_input='{"path":"workspace/out/x.txt"}',
            reservation_id=attempt.reservation_id,
            cost_cap=lw.xtu(5_000),
        )
    elif change == "cost_cap":
        other = descriptor.model_copy(update={"cost_cap": lw.xtu(4_000)})
    else:
        other = descriptor.model_copy(update={"attempt_id": b.new_id(AttemptId)})
    assert other.action_hash() != descriptor.action_hash()
    signed = st.channel.approve(s.approval(other.action_hash()))
    st.journal.apply_ledger((RecordApproval(op="record_approval", signed=signed),))
    with pytest.raises(PermitRejectedError, match="APPROVAL_INVALID"):
        lw.approved(st, attempt, signed.approval.approval_id)


# ---------------------------------------------------------------- epochs, stop and revocation


def test_a_stale_access_epoch_is_refused(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    st.owner(RevokeAccess(op="revoke_access", tenant_id=b.TENANT))
    with pytest.raises(PermitRejectedError) as excinfo:
        lw.consume(st, attempt)
    assert _reasons(excinfo) & {"STALE_ACCESS_EPOCH", "STALE_REVOCATION"}


def test_a_denied_resource_stops_new_decisions_and_consumption(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    st.owner(DenyResource(op="deny_resource", tenant_id=b.TENANT, path="workspace/a.txt"))
    with pytest.raises(PermitRejectedError):
        lw.consume(st, attempt)
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    fresh = lw.fixed_attempt(st, step, scopes, access_epoch=1)
    with pytest.raises(PolicyDeniedError, match="RESOURCE_DENIED"):
        lw.allowed(st, fresh)


@pytest.mark.parametrize("scope", ["global", "tenant", "run"])
def test_stop_blocks_issue_consumption_and_claims(st: Any, scope: str) -> None:
    attempt = lw.issued(st, _step(st))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    kind, scope_id = {
        "global": (StopScopeType.GLOBAL, "global"),
        "tenant": (StopScopeType.TENANT, str(b.TENANT)),
        "run": (StopScopeType.RUN, str(run_id)),
    }[scope]
    consumed = lw.consume(st, attempt)
    _stop(st, kind, scope_id)
    # A permit already consumed: its receipt is revoked and cannot be claimed.
    with pytest.raises(PermitRejectedError):
        lw.claim(st, consumed)
    assert st.journal.receipt_state(consumed.receipt_id) is ReceiptState.REVOKED
    # Started before the stop: never treated as not executed; the hold stays.
    assert st.journal.reservation(consumed.reservation_id).state is R.CONSUMED
    entries = {(e.scope_type, e.scope_id): e for e in st.journal.stop_entries()}
    assert entries[(kind, scope_id)].status is StopStatus.STOPPED


def test_stop_before_issue_or_consumption(st: Any) -> None:
    step = _step(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    reserved = lw.intent(st, lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000))
    _stop(st, StopScopeType.RUN, str(scopes.run_id))
    with pytest.raises(PermitRejectedError, match="STOPPED"):
        lw.issue(st, reserved)
    other = lw.issued(st, _step(st))
    _stop(st, StopScopeType.GLOBAL, "global")
    with pytest.raises(PermitRejectedError) as excinfo:
        lw.consume(st, other)
    assert _reasons(excinfo) & {"STOPPED", "STALE_REVOCATION"}
    assert st.journal.permit_state(other.permit_id) is PermitState.REVOKED


def test_resume_is_owner_only_and_old_permits_stay_invalid(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    _stop(st, StopScopeType.GLOBAL, "global")
    seq = {(e.scope_type, e.scope_id): e for e in st.journal.stop_entries()}[
        (StopScopeType.GLOBAL, "global")
    ].stop_seq
    resume = ResumeScope(
        op="resume_scope", scope_type=StopScopeType.GLOBAL, scope_id="global", stop_seq=seq
    )
    with pytest.raises(LedgerPairingError):
        st.journal.apply_ledger((resume,))
    with pytest.raises(OwnerAuthorizationError):
        st.journal.apply_owner_ledger(LedgerBatch(ops=(resume,)), owner_mac="0" * 64)
    st.owner(resume)
    with pytest.raises(PermitRejectedError):
        lw.consume(st, attempt)


def test_a_restart_does_not_lift_a_stop(st: Any) -> None:
    _stop(st, StopScopeType.GLOBAL, "global")
    st2 = st.reopen()
    try:
        entries = {(e.scope_type, e.scope_id): e for e in st2.journal.stop_entries()}
        assert entries[(StopScopeType.GLOBAL, "global")].status is StopStatus.STOPPED
        step = lw.step_in(st2)
        scopes = lw.scopes_of(st2.sm, step)
        lw.caps(st2, scopes)
        with pytest.raises(PolicyDeniedError, match="STOPPED"):
            lw.allowed(st2, lw.fixed_attempt(st2, step, scopes, access_epoch=1))
    finally:
        st2.journal.close()


def test_restart_invalidates_old_permits_and_receipts_and_keeps_holds(st: Any) -> None:
    issued = lw.issued(st, _step(st))
    consumed = lw.consume(st, lw.issued(st, _step(st)))
    st2 = st.reopen()
    try:
        with pytest.raises(PermitRejectedError, match="STALE_OWNER"):
            lw.consume(st2, st2.sm.state.attempts[issued.attempt_id])
        with pytest.raises(PermitRejectedError, match="PEP_MISMATCH"):
            lw.claim(st2, st2.sm.state.attempts[consumed.attempt_id])
        st2.journal.apply_ledger((RecoverInFlight(op="recover_in_flight"),))
        view = st2.journal.reservation(consumed.reservation_id)
        assert (view.state, view.hold_micro) == (R.UNKNOWN, 1_000)
        # Not released: a bound release needs its transition, and UNKNOWN never releases.
        with pytest.raises((LedgerPairingError, ReservationStateError)):
            st2.journal.apply_ledger(
                (
                    ReleaseReservation(
                        op="release",
                        reservation_id=lw.rsv(consumed),
                        attempt_id=consumed.attempt_id,
                        report=None,
                    ),
                )
            )
    finally:
        st2.journal.close()


# ---------------------------------------------------------------- one use only, real SQLite races


def test_a_permit_and_a_receipt_are_used_once(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    consume = lw.consume_op(st, attempt)
    consumed = lw.consume(st, attempt, consume)
    claim = lw.claim_op(st, consumed)
    claimed = lw.claim(st, consumed, claim)
    assert claimed.state is A.DISPATCH_AUTHORIZED
    # The runtime refuses repeats first; the ledger refuses them on its own too.
    ctx = sqlite_ledger.LedgerContext(keys=st.keys, owner_epoch=st.journal.owner_epoch)
    st.journal.close()
    for op, reason in ((consume, "NOT_ISSUED"), (claim, "RECEIPT_NOT_ISSUED")):
        raw = sqlite3.connect(st.path, isolation_level=None)
        try:
            raw.execute("BEGIN IMMEDIATE")
            with pytest.raises(PermitRejectedError, match=reason):
                sqlite_ledger.apply_op(raw.cursor(), op, now_ms=st.clock.now, ctx=ctx)
            raw.execute("ROLLBACK")
        finally:
            raw.close()


def _race(path: Path, ops: list[Any], ctx: Any, now: int) -> list[str]:
    barrier = threading.Barrier(len(ops))
    results: list[str] = []

    def run(op: Any) -> None:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 5000")
        barrier.wait()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                sqlite_ledger.apply_op(conn.cursor(), op, now_ms=now, ctx=ctx)
            except (SafetyError, CostError):
                conn.execute("ROLLBACK")
                results.append("refused")
                return
            conn.execute("COMMIT")
            results.append("ok")
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(op,)) for op in ops]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return sorted(results)


def test_concurrent_consumption_of_one_permit_succeeds_once(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    ops = [lw.consume_op(st, attempt), lw.consume_op(st, attempt)]
    ctx = sqlite_ledger.LedgerContext(keys=st.keys, owner_epoch=st.journal.owner_epoch)
    st.journal.close()
    assert _race(st.path, ops, ctx, st.clock.now) == ["ok", "refused"]


def test_concurrent_receipt_claims_succeed_once(st: Any) -> None:
    attempt = lw.consume(st, lw.issued(st, _step(st)))
    ops = [lw.claim_op(st, attempt), lw.claim_op(st, attempt)]
    ctx = sqlite_ledger.LedgerContext(keys=st.keys, owner_epoch=st.journal.owner_epoch)
    st.journal.close()
    assert _race(st.path, ops, ctx, st.clock.now) == ["ok", "refused"]


def test_stop_racing_consumption_never_leaves_a_usable_receipt(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    consume = lw.consume_op(st, attempt)
    stop = StopScope(op="stop_scope", scope_type=StopScopeType.TENANT, scope_id=str(b.TENANT))
    ctx = sqlite_ledger.LedgerContext(keys=st.keys, owner_epoch=st.journal.owner_epoch)
    st.journal.close()
    _race(st.path, [consume, stop], ctx, st.clock.now)
    conn = sqlite3.connect(st.path)
    try:
        permit = conn.execute(
            "SELECT state FROM permits WHERE permit_id = ?", (str(attempt.permit_id),)
        ).fetchone()[0]
        receipts = conn.execute("SELECT state FROM receipts").fetchall()
    finally:
        conn.close()
    # Either the stop came first (consumption refused, permit revoked) or the
    # consumption came first (a pre-stop operation whose receipt is revoked).
    assert permit in ("REVOKED", "CONSUMED")
    assert all(state == "REVOKED" for (state,) in receipts)


# ---------------------------------------------------------------- failures inside the transaction


def _snapshot(st: Any, attempt: Any) -> tuple[Any, ...]:
    return (
        st.journal.head(b.TENANT),
        st.journal.permit_state(attempt.permit_id),
        st.journal.reservation(attempt.reservation_id),
        len(st.journal.audit_records()),
    )


def test_an_audit_failure_rolls_everything_back(st: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = lw.issued(st, _step(st))
    before = _snapshot(st, attempt)

    def failing(*_args: Any, **_kwargs: Any) -> None:
        raise AuditWriteError("synthetic audit failure")

    monkeypatch.setattr(sqlite_ledger, "_insert_audit", failing)
    with pytest.raises(AuditWriteError):
        lw.consume(st, attempt)
    monkeypatch.undo()
    assert _snapshot(st, attempt) == before
    assert st.sm.state.attempts[attempt.attempt_id].state is A.PERMIT_ISSUED


def test_a_failure_in_the_middle_rolls_everything_back(
    st: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = lw.issued(st, _step(st))
    before = _snapshot(st, attempt)

    def failing(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("synthetic failure after the permit row was updated")

    monkeypatch.setattr(sqlite_ledger, "consume_reservation", failing)
    with pytest.raises(RuntimeError):
        lw.consume(st, attempt)
    monkeypatch.undo()
    assert _snapshot(st, attempt) == before


def test_audit_writer_recursion_is_refused(st: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = lw.issued(st, _step(st))
    real = sqlite_ledger._chain_hash

    def reentering(cur: Any, *args: Any) -> Any:
        sqlite_ledger._audit(cur, 0, "REENTERED", {}, "ALLOW", None, None, None)
        return real(cur, *args)

    monkeypatch.setattr(sqlite_ledger, "_chain_hash", reentering)
    with pytest.raises(AuditRecursionError):
        lw.consume(st, attempt)
    monkeypatch.undo()
    assert st.journal.permit_state(attempt.permit_id) is PermitState.ISSUED


def test_commit_outcome_unknown_requires_a_reload(st: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = lw.issued(st, _step(st))

    def failing_commit() -> None:
        raise sqlite3.OperationalError("synthetic: disk I/O error at COMMIT")

    op = lw.consume_op(st, attempt)
    monkeypatch.setattr(st.journal, "_commit", failing_commit)
    with pytest.raises(JournalWriteOutcomeUnknownError):
        lw.consume(st, attempt, op)
    with pytest.raises(RuntimeReloadRequiredError):
        lw.consume(st, attempt, op)
    st2 = st.reopen()
    try:
        # The commit did not happen: ledger and journal agree (nothing consumed). The
        # unused permit of the earlier owner is revoked when the journal opens (R07).
        assert st2.journal.permit_state(attempt.permit_id) is PermitState.REVOKED
        assert st2.journal.reservation(attempt.reservation_id).state is R.RESERVED
        assert st2.sm.state.attempts[attempt.attempt_id].state is A.PERMIT_ISSUED
    finally:
        st2.journal.close()


def test_commit_that_succeeded_but_reported_failure_is_consistent(
    st: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = lw.issued(st, _step(st))
    real_commit = st.journal._commit

    def commit_then_fail() -> None:
        real_commit()
        raise sqlite3.OperationalError("synthetic: error after COMMIT")

    monkeypatch.setattr(st.journal, "_commit", commit_then_fail)
    with pytest.raises(JournalWriteOutcomeUnknownError):
        lw.consume(st, attempt)
    st2 = st.reopen()
    try:
        assert st2.journal.permit_state(attempt.permit_id) is PermitState.CONSUMED
        assert st2.sm.state.attempts[attempt.attempt_id].state is A.PERMIT_CONSUMED
        assert st2.journal.reservation(attempt.reservation_id).state is R.CONSUMED
    finally:
        st2.journal.close()


def test_without_the_permit_key_nothing_is_issued(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.intent(st, lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000))
    st.journal.close()
    journal = lw.open_journal(st.path, st.clock, None)
    try:
        sm = w.machine(journal)
        st2 = lw.Setup(
            st.path, journal, sm, st.keys, st.clock, st.channel, descriptors=st.descriptors
        )
        with pytest.raises(PermitRejectedError, match="KEY_MISSING"):
            lw.issue(st2, sm.state.attempts[attempt.attempt_id])
    finally:
        journal.close()


def test_pep_instance_must_match(st: Any) -> None:
    attempt = lw.consume(st, lw.issued(st, _step(st)))
    with pytest.raises(PermitRejectedError, match="PEP_MISMATCH"):
        lw.claim(st, attempt, lw.claim_op(st, attempt, pep_instance_id=b.new_id(PepInstanceId)))


def test_other_tenant_permits_are_refused(st: Any) -> None:
    attempt = lw.issued(st, _step(st))
    permit = st.journal.permit(attempt.permit_id)
    foreign = permit.model_copy(
        update={"body": permit.body.model_copy(update={"tenant_id": b.new_id(TenantId)})}
    )
    with pytest.raises(PermitRejectedError):
        lw.consume(st, attempt, permit=foreign)
