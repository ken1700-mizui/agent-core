"""WP-09 limited fixes, stage 3: consistency when the journal is opened (WP09-R07).

The journal refuses to open (and changes nothing) when its runtime events and
the ledger disagree. Each tamper case failed to be detected before the fix."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.types.errors import LedgerInconsistencyError, PermitRejectedError
from agent_core.types.ids import TenantId
from agent_core.types.ledger import ResumeScope, RevokeAccess
from agent_core.types.safety import PermitState, ReceiptState, StopScopeType, StopStatus
from agent_core.types.serialization import dump_json
from agent_core.types.values import NonMonetaryCaps
from support import builders as b
from support import ledger_world as lw


@pytest.fixture
def world(tmp_path: Path) -> Any:
    """A database with issued, consumed, started and finished attempts, an approval,
    a stop and a revocation; closed and ready to be tampered with."""
    st = lw.setup(tmp_path)
    issued = lw.issued(st, lw.step_in(st))
    consumed = lw.consume(st, lw.issued(st, lw.step_in(st)))
    gate = lw.gate(st)
    started = lw.consume(st, lw.issued(st, lw.step_in(st), write=True))
    started, _ = gate.start(gate.claim(started, **lw.actual(st, started)))
    finished = lw.consume(st, lw.issued(st, lw.step_in(st), costly=True))
    finished, _ = gate.start(gate.claim(finished, **lw.actual(st, finished)))
    gate.finish(finished, SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True))
    run_id = st.sm.state.steps[issued.step_id].run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st.owner(RevokeAccess(op="revoke_access", tenant_id=b.new_id(TenantId)))
    st.journal.close()
    return st, {"issued": issued, "consumed": consumed, "started": started, "finished": finished}


def _tamper(st: Any, sql: str, *params: Any) -> None:
    conn = sqlite3.connect(st.path)
    try:
        changed = conn.execute(sql, params).rowcount
        conn.commit()
    finally:
        conn.close()
    assert changed >= 1, sql


def _refused(st: Any, reason: str) -> None:
    before = st.path.read_bytes()
    with pytest.raises(LedgerInconsistencyError, match=reason):
        st.reopen()
    # Refused unchanged: opening again fails the same way, and nothing was written.
    with pytest.raises(LedgerInconsistencyError):
        st.reopen()
    assert st.path.read_bytes() == before


def test_a_consistent_database_reopens(world: Any) -> None:
    st, _ = world
    st2 = st.reopen()
    st2.journal.close()


def test_attempt_and_reservation_states_must_agree(world: Any) -> None:
    st, a = world
    _tamper(
        st,
        "UPDATE reservations SET state = 'RELEASED', hold_micro = 0 WHERE attempt_id = ?",
        str(a["consumed"].attempt_id),
    )
    _refused(st, "RESERVATION")


def test_the_mirror_must_match_the_journal(world: Any) -> None:
    st, a = world
    _tamper(
        st,
        "UPDATE rt_attempts SET state = 'COMPLETED' WHERE attempt_id = ?",
        str(a["started"].attempt_id),
    )
    _refused(st, "MIRROR")


def test_a_consumed_permit_needs_its_receipt(world: Any) -> None:
    st, a = world
    _tamper(st, "DELETE FROM receipts WHERE attempt_id = ?", str(a["consumed"].attempt_id))
    _refused(st, "PERMIT_RECEIPT")


def test_approval_uses_must_match_consumed_permits(world: Any) -> None:
    st, _ = world
    _tamper(st, "UPDATE approvals SET uses = 0")
    _refused(st, "APPROVAL_USES")


def test_reservation_amounts_must_match_their_entries(world: Any) -> None:
    st, a = world
    _tamper(
        st,
        "UPDATE reservations SET hold_micro = 1 WHERE attempt_id = ?",
        str(a["issued"].attempt_id),
    )
    _refused(st, "RESERVATION_ENTRIES")


def test_the_audit_chain_must_be_intact(world: Any) -> None:
    st, _ = world
    _tamper(st, "UPDATE audit_log SET action = 'ALTERED' WHERE audit_seq = 3")
    _refused(st, "AUDIT_CHAIN")


def test_a_removed_audit_record_is_detected(world: Any) -> None:
    st, _ = world
    _tamper(st, "DELETE FROM audit_log WHERE audit_seq = 4")
    _refused(st, "AUDIT_CHAIN")


def test_a_lifted_stop_without_a_resume_is_detected(world: Any) -> None:
    st, _ = world
    _tamper(st, "UPDATE stop_state SET status = 'RUNNING' WHERE scope_type = 'RUN'")
    _refused(st, "STOP_STATE")


def test_the_global_stop_entry_must_exist(world: Any) -> None:
    st, _ = world
    _tamper(st, "DELETE FROM stop_state WHERE scope_type = 'GLOBAL'")
    _refused(st, "STOP_STATE")


def test_the_revocation_generation_cannot_go_back(world: Any) -> None:
    st, _ = world
    _tamper(st, "UPDATE revocation SET revocation_gen = 0")
    _refused(st, "REVOCATION")


def test_old_owner_permits_and_receipts_are_unusable_after_restart(world: Any) -> None:
    st, a = world
    st2 = st.reopen()
    try:
        issued = st2.sm.state.attempts[a["issued"].attempt_id]
        consumed = st2.sm.state.attempts[a["consumed"].attempt_id]
        # Revoked when the journal opened, under the new owner epoch.
        assert st2.journal.permit_state(issued.permit_id) is PermitState.REVOKED
        assert st2.journal.receipt_state(consumed.receipt_id) is ReceiptState.REVOKED
        st2.pep = st.pep  # even with the old gate ID
        with pytest.raises(PermitRejectedError):
            lw.gate(st2).claim(consumed, **lw.actual(st2, consumed))
        actions = [r.action for r in st2.journal.audit_records()]
        assert "RESTART_REVOKED" in actions
    finally:
        st2.journal.close()


def test_unsettled_reservations_stay_held_after_restart(world: Any) -> None:
    st, a = world
    st2 = st.reopen()
    try:
        for name in ("consumed", "started"):
            view = st2.journal.reservation(a[name].reservation_id)
            assert view.hold_micro == 1_000
    finally:
        st2.journal.close()


# ------------------------------------------------ re-review: stop rows and Task limits


def _stopped_tenant(tmp_path: Path) -> Any:
    """A database whose tenant was stopped (STOP is the scope's last audit record)."""
    st = lw.setup(tmp_path, "tenant.db")
    lw.issued(st, lw.step_in(st))
    st.owner(lw.stop_op(StopScopeType.TENANT, str(b.TENANT)))
    st.journal.close()
    return st


def test_a_missing_run_stop_row_is_detected(world: Any) -> None:
    st, _ = world
    # The audit log ends the run's history with STOP; only its state row is removed.
    _tamper(st, "DELETE FROM stop_state WHERE scope_type = 'RUN'")
    _refused(st, "STOP_STATE")


def test_a_missing_tenant_stop_row_is_detected(tmp_path: Path) -> None:
    st = _stopped_tenant(tmp_path)
    _tamper(st, "DELETE FROM stop_state WHERE scope_type = 'TENANT'")
    _refused(st, "STOP_STATE")


def test_stop_then_resume_reopens(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = lw.issued(st, lw.step_in(st))
    run_id = str(st.sm.state.steps[attempt.step_id].run_id)
    for kind, scope_id in ((StopScopeType.RUN, run_id), (StopScopeType.TENANT, str(b.TENANT))):
        st.owner(lw.stop_op(kind, scope_id))
        entries = {(e.scope_type, e.scope_id): e for e in st.journal.stop_entries()}
        seq = entries[(kind, scope_id)].stop_seq
        st.owner(ResumeScope(op="resume_scope", scope_type=kind, scope_id=scope_id, stop_seq=seq))
    st2 = st.reopen()
    try:
        entries = {(e.scope_type, e.scope_id): e for e in st2.journal.stop_entries()}
        assert entries[(StopScopeType.RUN, run_id)].status is StopStatus.RUNNING
        assert entries[(StopScopeType.TENANT, str(b.TENANT))].status is StopStatus.RUNNING
    finally:
        st2.journal.close()


def test_a_changed_task_budget_is_detected(world: Any) -> None:
    st, _ = world
    # Same Task ID, same journal; only the mirrored budget differs.
    _tamper(st, "UPDATE rt_tasks SET budget_micro = budget_micro + 1")
    _refused(st, "MIRROR")


def test_changed_task_caps_are_detected(world: Any) -> None:
    st, _ = world
    conn = sqlite3.connect(st.path)
    try:
        task_id, caps = conn.execute("SELECT task_id, caps FROM rt_tasks LIMIT 1").fetchone()
    finally:
        conn.close()
    # Well-formed caps with one different limit.
    changed = NonMonetaryCaps.model_validate_json(caps).model_copy(update={"model_calls": 1})
    assert changed != NonMonetaryCaps.model_validate_json(caps)
    _tamper(
        st, "UPDATE rt_tasks SET caps = ? WHERE task_id = ?", dump_json(changed).decode(), task_id
    )
    _refused(st, "MIRROR")
