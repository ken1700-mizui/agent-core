"""Real process stops around permit consumption (WP-09C; C1 7.5, E 3.4, E.Z6).

The child process ends with ``os._exit`` (no cleanup, no close): a real process
stop, not an exception. The parent then reopens the database as a new owner.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_core.types.cost import ReservationState
from agent_core.types.errors import PermitRejectedError
from agent_core.types.ledger import RecoverInFlight
from agent_core.types.records import AttemptState
from agent_core.types.safety import PermitState, ReceiptState
from support import builders as b
from support import ledger_world as lw

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "permit_child.py"
TIMEOUT_SECONDS = 60
A = AttemptState
R = ReservationState


def _run_child(st: Any, mode: str, attempt: Any) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests"))
    keys = f"{st.keys.owner.reveal().hex()}\n{st.keys.permit.reveal().hex()}\n"
    return subprocess.run(
        [sys.executable, str(CHILD), mode, str(st.path), str(attempt.attempt_id), str(st.pep)],
        input=keys,
        capture_output=True,
        text=True,
        env=env,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


@pytest.fixture
def prepared(tmp_path: Path) -> Any:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.intent(st, lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000))
    head = st.journal.head(b.TENANT)
    audits = len(st.journal.audit_records())
    st.journal.close()
    return st, attempt, head, audits


def test_process_stop_right_after_consumption(prepared: Any) -> None:
    st, attempt, head, _ = prepared
    result = _run_child(st, "consume-then-exit", attempt)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "consumed" in result.stdout
    st2 = st.reopen()
    try:
        # Committed as one unit: journal state and ledger agree.
        consumed = st2.sm.state.attempts[attempt.attempt_id]
        assert consumed.state is A.PERMIT_CONSUMED
        assert st2.journal.head(b.TENANT).tenant_seq == head.tenant_seq + 2  # issue, consume
        assert st2.journal.permit_state(consumed.permit_id) is PermitState.CONSUMED
        assert st2.journal.receipt_state(consumed.receipt_id) is ReceiptState.ISSUED
        assert st2.journal.reservation(attempt.reservation_id).state is R.CONSUMED
        # The PEP that held the receipt is gone: a new PEP instance cannot use it.
        with pytest.raises(PermitRejectedError, match="PEP_MISMATCH"):
            lw.claim(st2, consumed)
        # Not known whether it was executed: held as UNKNOWN, never released.
        st2.journal.apply_ledger((RecoverInFlight(op="recover_in_flight"),))
        view = st2.journal.reservation(attempt.reservation_id)
        assert (view.state, view.hold_micro) == (R.UNKNOWN, 1_000)
        assert st2.journal.verify_tenant(b.TENANT) is not None
    finally:
        st2.journal.close()


def test_process_stop_inside_the_consumption_transaction(prepared: Any) -> None:
    st, attempt, head, audits = prepared
    result = _run_child(st, "exit-inside-consume", attempt)
    assert result.returncode == 7, result.stderr[-2000:]
    assert "in-transaction" in result.stdout
    st2 = st.reopen()
    try:
        # Nothing of the consumption survived: only the issue (committed before) is there.
        issued = st2.sm.state.attempts[attempt.attempt_id]
        assert issued.state is A.PERMIT_ISSUED
        assert st2.journal.head(b.TENANT).tenant_seq == head.tenant_seq + 1
        assert st2.journal.permit_state(issued.permit_id) is PermitState.ISSUED
        assert st2.journal.reservation(attempt.reservation_id).state is R.RESERVED
        actions = [r.action for r in st2.journal.audit_records()]
        assert len(actions) == audits + 1
        assert actions[-1] == "PERMIT_ISSUED"
        assert "PERMIT_CONSUMED" not in actions
        # The permit was issued under an earlier owner epoch: it stays unusable.
        with pytest.raises(PermitRejectedError, match="STALE_OWNER"):
            lw.consume(st2, st2.sm.state.attempts[attempt.attempt_id])
        assert st2.journal.verify_tenant(b.TENANT) is not None
    finally:
        st2.journal.close()
