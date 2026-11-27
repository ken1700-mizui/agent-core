"""QR-01 (independent quality review, HIGH): an unknown outcome's cost is never
settled alone (regression tests, written first).

The outcome of an OUTCOME_UNKNOWN Attempt and its cost are settled together, in
one journal transaction, by ``ReconcileOutcome`` (WP12-R02). The owner's
cost-only ``Reconcile`` stays for an Attempt whose outcome is settled (for
example a COMPLETED model call whose bill was not final), never for one whose
outcome is unknown. A state that an earlier version left (a cost settled apart
from an unknown outcome) is reported, never repaired.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_ledger
from agent_core.runtime import recovery as rc
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.types.cost import BudgetScopeType, ReservationState
from agent_core.types.errors import (
    AgentCoreError,
    JournalStorageError,
    ReservationAmountError,
    ReservationStateError,
)
from agent_core.types.ledger import Reconcile
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.records import AttemptState, RunStatus
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import ledger_world as lw

A = AttemptState
R = RunStatus
RS = ReservationState
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)


def _deps(st: lw.Setup) -> rc.RecoveryDeps:
    return rc.RecoveryDeps(
        sm=st.sm,
        ledger=st.journal,
        owner=st.owner,
        clock=st.clock,
        report_key=st.keys.permit,
        journal=st.journal,
    )


def _all(st: lw.Setup) -> list[Any]:
    return [r.run_id for r in st.sm.state.runs.values()]


def _view(st: lw.Setup, attempt: Any) -> Any:
    return st.journal.reservation(str(attempt.reservation_id))


def _entries(st: lw.Setup, attempt: Any) -> list[str]:
    return [e.kind for e in st.journal.ledger_entries(str(attempt.reservation_id))]


def _usage(
    *, status: UsageStatus = UsageStatus.FINAL, tokens: tuple[int, int] | None = (10, 5)
) -> Usage:
    return Usage(
        status=status,
        input_tokens=tokens[0] if tokens is not None else None,
        output_tokens=tokens[1] if tokens is not None else None,
        total_tokens=sum(tokens) if tokens is not None and status is UsageStatus.FINAL else None,
        cost=None,
        cost_status=CostStatus.UNKNOWN,  # the cost comes from the billing, not the report
        synthetic=True,
    )


def _tool_zero() -> Usage:
    usage: Usage = lw.zero_usage().model_copy(
        update={
            "cost": Budget(currency=Currency.XTU, micro_units=0),
            "cost_status": CostStatus.FINAL,
        }
    )
    return usage


def _started(st: lw.Setup, *, model: bool = False) -> Any:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, micro=100_000)
    attempt = lw.fixed_attempt(st, step, scopes, model=model)
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 5_000))))
    gate = lw.gate(st)
    return gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]


def _report(
    st: lw.Setup, attempt: Any, usage: Usage, *, final: bool = True, digest: str | None = None
) -> Any:
    current = st.sm.state.attempts[attempt.attempt_id]
    result = SyntheticResult(outcome="COMPLETED", usage=usage, final=final, result_digest=digest)
    return lw.gate(st).execution_report(current, result)


def _unknown(
    tmp_path: Path, digest: str | None = None
) -> tuple[lw.Setup, rc.RecoveryDeps, Any, Any]:
    """A started tool call, its signed final report kept, then a restart: the
    recovery keeps the Attempt OUTCOME_UNKNOWN and its whole hold."""
    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt, _tool_zero(), digest=digest)
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    return st2, deps, attempt, report


def _cost_only(st: lw.Setup, attempt: Any, micro: int = 0) -> None:
    st.owner(
        Reconcile(
            op="reconcile",
            reservation_id=attempt.reservation_id,
            actual=Budget(currency=Currency.XTU, micro_units=micro),
            evidence_ref=b.digest("synthetic cost only"),
        )
    )


# ---------------------------------------------------------------- 1, 2: refused, hold kept


def test_a_cost_only_reconciliation_of_an_unknown_outcome_is_refused(tmp_path: Path) -> None:
    st, _, attempt, _ = _unknown(tmp_path)
    with pytest.raises(ReservationStateError, match="OUTCOME_NOT_SETTLED"):
        _cost_only(st, attempt)


def test_the_refusal_changes_nothing_and_keeps_the_hold(tmp_path: Path) -> None:
    st, _, attempt, _ = _unknown(tmp_path)
    before, entries = _view(st, attempt), _entries(st, attempt)
    assert before.hold_micro == 5_000
    with pytest.raises(AgentCoreError):
        _cost_only(st, attempt)
    st2 = st.reopen()  # nothing half written either
    after = _view(st2, attempt)
    assert (after.state, after.hold_micro, after.spent_known) == (RS.UNKNOWN, 5_000, False)
    assert _entries(st2, attempt) == entries
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


# ---------------------------------------------------------------- 3, 4: atomic, idempotent


def test_the_formal_evidence_settles_the_outcome_and_the_cost_together(tmp_path: Path) -> None:
    st, deps, attempt, report = _unknown(tmp_path)
    result = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert result.outcome is rc.Reconciled.EXECUTED
    view = _view(st, attempt)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.COMPLETED
    assert (view.state, view.hold_micro, view.spent_known) == (RS.RECONCILED, 0, True)
    assert _entries(st, attempt).count("RECONCILIATION") == 1


def test_reconciling_again_never_counts_the_cost_twice(tmp_path: Path) -> None:
    st, deps, attempt, report = _unknown(tmp_path)
    rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    spent = st.journal.scope_usage(BudgetScopeType.RUN, str(run_id)).spent_micro
    again = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert again.outcome is rc.Reconciled.ALREADY_SETTLED
    with pytest.raises(AgentCoreError):
        _cost_only(st, attempt)  # a settled cost is not settled again either
    st2 = st.reopen()
    deps2 = _deps(st2)
    rc.recover(deps2, deadlines=DEADLINES, run_ids=_all(st2))  # ends the settled Run
    assert rc.recover(deps2, deadlines=DEADLINES, run_ids=_all(st2)).changed is False
    assert st2.journal.scope_usage(BudgetScopeType.RUN, str(run_id)).spent_micro == spent
    assert _entries(st2, attempt).count("RECONCILIATION") == 1
    assert _view(st2, attempt).hold_micro == 0


# ---------------------------------------------------------------- 5: a crash inside the transaction


RESULT = b.digest("synthetic operation result")


def _results(path: Path, attempt: Any) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT result_digest FROM operation_results WHERE attempt_id = ?",
            (str(attempt.attempt_id),),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def test_a_crash_inside_the_reconciliation_leaves_no_half_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RR-01: the report carries a result digest, so the transaction really writes
    the result row before the crash; the rollback must take it back."""
    st, deps, attempt, report = _unknown(tmp_path, digest=RESULT)
    assert report.result_digest == RESULT
    written: list[str] = []

    def crash(cur: sqlite3.Cursor, *args: Any, **kwargs: Any) -> Any:
        # Inside the transaction, after the ledger wrote the result row.
        written.extend(
            r[0]
            for r in cur.execute(
                "SELECT result_digest FROM operation_results WHERE attempt_id = ?",
                (str(attempt.attempt_id),),
            )
        )
        raise sqlite3.OperationalError("synthetic crash inside the reconciliation")

    monkeypatch.setattr(sqlite_ledger, "_record_cost", crash)
    with pytest.raises(JournalStorageError):  # the journal fails closed, rolled back
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    monkeypatch.undo()
    assert written == [RESULT]  # the row existed inside the transaction
    st2 = st.reopen()
    view = _view(st2, attempt)
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert (view.state, view.hold_micro, view.spent_known) == (RS.UNKNOWN, 5_000, False)
    assert _results(st2.path, attempt) == []  # rolled back with the rest
    deps2 = _deps(st2)
    done = rc.reconcile(deps2, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert done.outcome is rc.Reconciled.EXECUTED
    assert _view(st2, attempt).hold_micro == 0
    assert _results(st2.path, attempt) == [RESULT]  # written once, with the right digest


# ---------------------------------------------------------------- 6: the lower bound


def test_a_report_below_the_known_lower_bound_is_refused(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st, model=True)
    # A partial report: 1,000 input and 500 output tokens are known (at the test
    # prices 1,000 + 2,000 = 3,000), the bill is not final: a lower bound of 3,000.
    lw.report_with(st, attempt, _report(st, attempt, _usage(tokens=(1_000, 500)), final=False))
    assert _view(st, attempt).spent_lower_micro == 3_000
    # A final report of fewer tokens (10 + 20 = 30) contradicts the lower bound.
    low = _report(st, attempt, _usage(tokens=(10, 5)))
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    with pytest.raises(ReservationAmountError, match="BELOW_LOWER_BOUND"):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=low))
    view = _view(st2, attempt)
    assert (view.state, view.hold_micro) == (RS.UNKNOWN, 5_000)
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


# ---------------------------------------------------------------- the normal cost-only path


def test_a_settled_outcomes_unfinished_bill_is_still_reconciled_by_cost(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st, model=True)
    # The call completed; its tokens are unknown, so its cost is not final.
    done = lw.report(st, attempt, _usage(status=UsageStatus.UNKNOWN, tokens=None))
    assert done.state is A.COMPLETED
    assert _view(st, attempt).state is RS.UNKNOWN
    _cost_only(st, attempt, 250)
    view = _view(st, attempt)
    assert (view.state, view.hold_micro, view.spent_micro) == (RS.RECONCILED, 0, 250)
    with pytest.raises(AgentCoreError):
        _cost_only(st, attempt, 250)  # once only


def test_a_cost_only_reconciliation_below_the_lower_bound_is_refused(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st, model=True)
    lw.report(st, attempt, _usage(status=UsageStatus.PARTIAL, tokens=(1_000, 500)), final=False)
    with pytest.raises(ReservationAmountError, match="BELOW_LOWER_BOUND"):
        _cost_only(st, attempt, 2_999)
    assert _view(st, attempt).hold_micro == 5_000


# ---------------------------------------------------------------- left by an earlier version


def test_a_cost_settled_apart_by_an_earlier_version_is_reported_not_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, _, attempt, report = _unknown(tmp_path)
    # Simulate the earlier version, which settled the cost alone.
    monkeypatch.setattr(sqlite_ledger, "_refuse_cost_only", lambda cur, row: None)
    _cost_only(st, attempt)
    monkeypatch.undo()
    st2 = st.reopen()
    deps = _deps(st2)
    plan = rc.assess(st2.sm.state, st2.journal)
    assert plan.conflicts == (attempt.attempt_id,)
    with pytest.raises(rc.RecoveryError, match="COST_SETTLED_APART"):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    with pytest.raises(rc.RecoveryError, match="COST_SETTLED_APART"):
        rc.reconcile(deps, attempt.attempt_id, None)
    report_after = rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    assert report_after.conflicts == (attempt.attempt_id,)
    # Kept as it is, for the owner: no outcome, no second cost.
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert _view(st2, attempt).state is RS.RECONCILED
    assert _entries(st2, attempt).count("RECONCILIATION") == 1
    run_id = st2.sm.state.steps[attempt.step_id].run_id
    assert st2.sm.state.runs[run_id].status is R.RECONCILIATION_REQUIRED
