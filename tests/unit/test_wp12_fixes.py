"""WP-12 limited fixes R01-R05 and WP15-R07 (regression tests, written first).

R01 the recovery's targets (never the current owner's live work; explicit Runs;
no reentry); R02 one-transaction reconciliation bound to the Attempt, its
receipt, owner epoch, PEP and the trusted billing; R03 unsettled costs and end
states; R04 the recovery's time (the ledger's high-water); R05 projection repair
from the journal only; R07 the verification evidence per criterion, checked by
the completion transaction.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.runtime import recovery as rc
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.verification import FileCriterion
from agent_core.safety import reports
from agent_core.types.cost import ReservationState
from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import PepInstanceId
from agent_core.types.model import CostStatus, UsageStatus
from agent_core.types.records import AttemptState, RunStatus
from agent_core.types.safety import StopScopeType
from agent_core.types.transitions import (
    ReconciledOutcome,
    ReconciliationEvidence,
    RunTransition,
    VerificationEvidence,
)
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import ledger_world as lw
from support import loop_world as lp
from support import runtime_world as w

A = AttemptState
R = RunStatus
RS = ReservationState
DRAFT = "synthetic report draft\n"
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)


def _deps(st: lw.Setup, **extra: Any) -> rc.RecoveryDeps:
    return rc.RecoveryDeps(
        sm=st.sm,
        ledger=st.journal,
        owner=st.owner,
        clock=st.clock,
        report_key=st.keys.permit,
        journal=st.journal,
        **extra,
    )


def _started(st: lw.Setup) -> Any:
    step = lw.step_in(st)
    lw.caps(st, lw.scopes_of(st.sm, step))
    attempt = lw.consume(st, lw.issued(st, step))
    gate = lw.gate(st)
    return gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]


def _consumed(st: lw.Setup) -> Any:
    step = lw.step_in(st)
    lw.caps(st, lw.scopes_of(st.sm, step))
    return lw.consume(st, lw.issued(st, step))


def _all(st: lw.Setup) -> list[Any]:
    return [r.run_id for r in st.sm.state.runs.values()]


def _view(st: lw.Setup, attempt: Any) -> Any:
    return st.journal.reservation(str(attempt.reservation_id))


def _known_usage(cost: int = 0) -> Any:
    return lw.zero_usage().model_copy(
        update={
            "cost": Budget(currency=Currency.XTU, micro_units=cost),
            "cost_status": CostStatus.FINAL,
        }
    )


def _partial() -> Any:
    from agent_core.types.model import Usage

    return Usage(
        status=UsageStatus.PARTIAL,
        input_tokens=0,
        output_tokens=None,
        total_tokens=None,
        cost=None,
        cost_status=CostStatus.UNKNOWN,
        synthetic=True,
    )


def _report(st: lw.Setup, attempt: Any, outcome: str = "COMPLETED", usage: Any = None) -> Any:
    current = st.sm.state.attempts[attempt.attempt_id]
    result = SyntheticResult(outcome=outcome, usage=usage or _known_usage(), final=True)  # type: ignore[arg-type]
    return lw.gate(st).execution_report(current, result)


# ================================================================ R01 targets


def test_r01_a_run_of_a_live_session_is_never_recovered(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    run_id = st.sm.state.steps[attempt.step_id].run_id
    deps = _deps(st, active=lambda r: r == run_id)
    with pytest.raises(rc.RecoveryError, match="RUN_ACTIVE"):
        rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,))
    assert st.sm.state.attempts[attempt.attempt_id].state is A.DISPATCH_STARTED
    assert st.sm.state.runs[run_id].status is R.RUNNING


def test_r01_the_current_owners_authority_is_not_taken_as_revoked(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _consumed(st)  # consumed in this owner epoch, the receipt still live
    run_id = st.sm.state.steps[attempt.step_id].run_id
    rc.recover(_deps(st), deadlines=DEADLINES, run_ids=(run_id,))
    after = st.sm.state.attempts[attempt.attempt_id]
    # No restart revoked its receipt: "never started" is not proven; kept unknown.
    assert after.state is A.OUTCOME_UNKNOWN
    assert _view(st, after).state is RS.UNKNOWN


def test_r01_only_the_named_runs_are_recovered(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    one = _started(st)
    two = _started(st)
    st2 = st.reopen()
    first = st2.sm.state.steps[one.step_id].run_id
    rc.recover(_deps(st2), deadlines=DEADLINES, run_ids=(first,))
    assert st2.sm.state.runs[first].status is R.RECONCILIATION_REQUIRED
    other = st2.sm.state.steps[two.step_id].run_id
    assert st2.sm.state.runs[other].status is R.RUNNING  # not named: untouched


def test_r01_recovery_is_not_reentrant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    st = lw.setup(tmp_path)
    _started(st)
    st2 = st.reopen()
    deps = _deps(st2)
    inner: list[Exception] = []
    real = rc._recover_attempts

    def again(*args: Any) -> bool:
        try:
            rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
        except rc.RecoveryError as exc:
            inner.append(exc)
        return real(*args)

    monkeypatch.setattr(rc, "_recover_attempts", again)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    assert [str(e) for e in inner] == ["RECOVERY_BUSY"]


# ================================================================ R02 reconciliation


def test_r02_the_outcome_and_the_cost_are_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt)
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    real = st2.journal.append_with_ledger

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("synthetic crash in the reconciliation")

    monkeypatch.setattr(st2.journal, "append_with_ledger", crash)
    with pytest.raises(sqlite3.OperationalError):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    monkeypatch.setattr(st2.journal, "append_with_ledger", real)
    st3 = st2.reopen()
    # Nothing half done: neither the cost nor the outcome was recorded.
    assert st3.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert _view(st3, attempt).state is RS.UNKNOWN


def test_r02_a_settled_outcome_is_not_changed_by_other_evidence(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    done, failed = _report(st, attempt), _report(st, attempt, "FAILED_DEFINITE")
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    assert rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=done)).outcome is (
        rc.Reconciled.EXECUTED
    )
    st3 = st2.reopen()
    deps3 = _deps(st3)
    result = rc.reconcile(deps3, attempt.attempt_id, rc.ExecutionEvidence(report=failed))
    assert result.outcome is rc.Reconciled.ALREADY_SETTLED
    assert st3.sm.state.attempts[attempt.attempt_id].state is A.COMPLETED


def test_r02_a_cost_settled_apart_blocks_any_outcome(tmp_path: Path) -> None:
    from agent_core.types.ledger import Reconcile

    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt)
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    st2.owner(
        Reconcile(
            op="reconcile",
            reservation_id=attempt.reservation_id,
            actual=Budget(currency=Currency.XTU, micro_units=0),
            evidence_ref=b.digest("cost only"),
        )
    )
    with pytest.raises(AgentCoreError):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_r02_an_unknown_outcome_cannot_be_settled_without_the_ledger(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    st2 = st.reopen()
    rc.recover(_deps(st2), deadlines=DEADLINES, run_ids=_all(st2))
    evidence = ReconciliationEvidence(
        kind="reconciliation", evidence_ref=b.digest("x"), outcome=ReconciledOutcome.COMPLETED
    )
    with pytest.raises(AgentCoreError):
        st2.sm.advance_attempt(st2.sm.state.attempts[attempt.attempt_id], A.COMPLETED, evidence)


def test_r02_the_report_must_name_the_receipts_pep(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt)
    other = reports.sign(
        st.keys.permit, report.model_copy(update={"pep_instance_id": b.new_id(PepInstanceId)})
    )
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    with pytest.raises(AgentCoreError):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=other))  # type: ignore[arg-type]
    assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


@pytest.mark.parametrize("kind", ["stated_cost", "partial"])
def test_r02_the_cost_comes_from_the_trusted_billing(tmp_path: Path, kind: str) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    # A stated cost the billing does not give, or a partial bill marked final.
    usage = _known_usage(cost=999) if kind == "stated_cost" else _partial()
    report = _report(st, attempt, usage=usage)  # final=True, but not a final bill
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    with pytest.raises(AgentCoreError):
        rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert _view(st2, attempt).state is RS.UNKNOWN


# ================================================================ R03 unsettled and end states


def test_r03_an_allocation_left_over_is_voided(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    lw.caps(st, lw.scopes_of(st.sm, step))
    attempt = lw.fixed_attempt(st, step)
    aborted = lw.abort(st, attempt, release=False)  # the id stays ID_ALLOCATED
    st2 = st.reopen()
    rc.recover(_deps(st2), deadlines=DEADLINES, run_ids=_all(st2))
    assert _view(st2, aborted).state is RS.VOID


def test_r03_a_missing_reservation_record_is_refused(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _started(st)
    st2 = st.reopen()

    class Missing:
        def __init__(self, real: Any) -> None:
            self._real = real

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def reservation(self, reservation_id: str) -> Any:
            return None

    deps = rc.RecoveryDeps(
        sm=st2.sm,
        ledger=Missing(st2.journal),
        owner=st2.owner,
        clock=st2.clock,
        report_key=st2.keys.permit,
        journal=st2.journal,
    )
    with pytest.raises(rc.RecoveryError, match="RESERVATION_MISSING"):
        rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))


def test_r03_a_zero_but_unsettled_cost_is_unsettled(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    gate = lw.gate(st)
    nonfinal = _partial()
    gate.finish(attempt, SyntheticResult(outcome="COMPLETED", usage=nonfinal, final=False))
    view = _view(st, attempt)
    # A non-final report keeps the cost open (UNKNOWN), however small what is known.
    assert (view.state, view.spent_known, view.spent_micro) == (RS.UNKNOWN, False, 0)
    st2 = st.reopen()
    report = rc.recover(_deps(st2), deadlines=DEADLINES, run_ids=_all(st2))
    run = st2.sm.state.runs[st2.sm.state.steps[attempt.step_id].run_id]
    assert run.status is R.RECONCILIATION_REQUIRED  # never a normal end with it
    assert [u.attempt_id for u in report.unsettled] == [attempt.attempt_id]


def test_r03_a_terminal_runs_unknown_work_can_still_be_reconciled(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt)
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=_all(st2))
    run_id = st2.sm.state.steps[attempt.step_id].run_id
    rc.declare_unresolved(deps, run_id)
    plan = rc.assess(st2.sm.state, st2.journal)
    assert [a.attempt_id for r in plan.runs for a in r.attempts] == [attempt.attempt_id]
    assert rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report)).outcome is (
        rc.Reconciled.EXECUTED
    )
    assert st2.sm.state.runs[run_id].status is R.FAILED  # the Run's end stays


def test_r03_a_stop_is_kept_through_reconciliation(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    report = _report(st, attempt)
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,))
    assert st2.sm.state.runs[run_id].status is R.RECONCILIATION_REQUIRED
    rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,))
    assert st2.sm.state.runs[run_id].status is R.CANCELLED  # the stop's intent kept


def test_r03_changed_only_when_something_changed(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    gate = lw.gate(st)
    nonfinal = _partial()
    gate.finish(attempt, SyntheticResult(outcome="COMPLETED", usage=nonfinal, final=False))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st2 = st.reopen()
    deps = _deps(st2)
    assert rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,)).changed is True
    assert rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,)).changed is False


# ================================================================ R04 the recovery's time


def test_r04_a_clock_going_back_after_a_recovery_does_not_extend_a_deadline(
    tmp_path: Path,
) -> None:
    st = lw.setup(tmp_path)
    attempt = _started(st)
    gate = lw.gate(st)
    nonfinal = _partial()
    gate.finish(attempt, SyntheticResult(outcome="COMPLETED", usage=nonfinal, final=False))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st2 = st.reopen()
    deps = _deps(st2)
    rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,))
    assert st2.sm.state.runs[run_id].status is R.CANCELLING  # a cost not settled: waits
    start = st2.clock.now
    st2.clock.now = start + DEADLINES.cancelling_ms + 1
    rc.recover(
        deps,
        deadlines=rc.RecoveryDeadlines(recovering_ms=10**9, cancelling_ms=10**9),
        run_ids=(run_id,),
    )  # observes the time; nothing is due yet
    st2.clock.now = start  # the wall clock goes back
    report = rc.recover(deps, deadlines=DEADLINES, run_ids=(run_id,))
    assert st2.sm.state.runs[run_id].status is R.RECONCILIATION_REQUIRED
    assert report.escalated == (run_id,)


def test_r04_a_normal_recovery_is_not_an_escalation(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _started(st)
    st2 = st.reopen()
    report = rc.recover(_deps(st2), deadlines=DEADLINES, run_ids=_all(st2))
    assert report.escalated == ()
    assert report.changed is True


# ================================================================ R05 projection repair


def _broken(st: lw.Setup, sql: str) -> None:
    st.journal.close()
    conn = sqlite3.connect(st.path)
    conn.execute(sql)
    conn.commit()
    conn.close()


def _rows(st: lw.Setup, sql: str) -> list[Any]:
    conn = sqlite3.connect(st.path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_r05_a_damaged_creation_time_is_taken_from_the_journal(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _started(st)
    st.journal.close()
    expected = _rows(st, "SELECT run_id, created_at_ms FROM rt_runs ORDER BY 1")
    expected_tasks = _rows(st, "SELECT task_id, created_at_ms FROM rt_tasks ORDER BY 1")
    st.journal = SqliteJournal.open(st.path, clock=st.clock, random=OsRandomSource(), keys=st.keys)
    _broken(st, "UPDATE rt_runs SET created_at_ms = created_at_ms + 10000000")
    conn = sqlite3.connect(st.path)
    conn.execute("UPDATE rt_tasks SET created_at_ms = 0")
    conn.commit()
    conn.close()
    repaired = SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert set(repaired) == {"rt_runs", "rt_tasks"}
    assert _rows(st, "SELECT run_id, created_at_ms FROM rt_runs ORDER BY 1") == expected
    assert _rows(st, "SELECT task_id, created_at_ms FROM rt_tasks ORDER BY 1") == expected_tasks
    assert SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys) == ()


def test_r05_a_damaged_tenant_is_taken_from_the_journal(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    _started(st)
    _broken(st, "UPDATE rt_runs SET tenant_id = 'tnt_other'")
    repaired = SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)
    assert "rt_runs" in repaired
    assert {r[0] for r in _rows(st, "SELECT tenant_id FROM rt_runs")} == {str(b.TENANT)}


def test_r05_a_lost_creation_time_is_not_guessed(tmp_path: Path) -> None:
    from agent_core.types.errors import LedgerInconsistencyError

    st = lw.setup(tmp_path)
    _started(st)
    _broken(st, "DELETE FROM entity_times")  # the source of the time is gone
    with pytest.raises(LedgerInconsistencyError):
        SqliteJournal.repair_projections(st.path, clock=st.clock, keys=st.keys)


def test_r05_a_damaged_creation_time_is_found_at_open(tmp_path: Path) -> None:
    from agent_core.types.errors import LedgerInconsistencyError

    st = lw.setup(tmp_path)
    w.running_run(st.sm)
    _broken(st, "UPDATE rt_runs SET created_at_ms = created_at_ms + 10000000")
    with pytest.raises(LedgerInconsistencyError):
        SqliteJournal.open(st.path, clock=st.clock, random=OsRandomSource(), keys=st.keys)


# ================================================================ R07 verification evidence


def _drive(wd: lp.World) -> Any:
    outcome = wd.loop.run(wd.run.run_id)
    for _ in range(10):
        if outcome.status is not R.WAITING_APPROVAL or outcome.pending_action_hash is None:
            return outcome
        outcome = lp.approve_pending(wd, outcome)
    raise AssertionError("not settled")


def _completion(wd: lp.World) -> VerificationEvidence:
    for event in wd.st.journal.read_run(wd.run.run_id, after_run_seq=0, limit=1_000):
        for t in event.payload.transitions if event.payload else ():
            if isinstance(t, RunTransition) and t.after.status is R.COMPLETED:
                assert isinstance(t.evidence, VerificationEvidence)
                return t.evidence
    raise AssertionError("no completion")


def test_r07_each_criterion_names_its_verification_read(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    assert _drive(wd).status is R.COMPLETED
    [check] = _completion(wd).checks
    assert check.criterion_ref == lp.CRITERION.ref()
    assert check.path == lp.TARGET
    assert check.expected_digest == check.observed_digest == lp.CRITERION.expected_digest
    assert check.read_attempt_id is not None
    read = wd.st.sm.state.attempts[check.read_attempt_id]
    assert wd.st.sm.state.steps[read.step_id].kind.value == "VERIFICATION"


def _tamper(wd: lp.World, change: Any) -> None:
    real = wd.loop._verification_evidence

    def swap(*args: Any, **kwargs: Any) -> VerificationEvidence:
        evidence = real(*args, **kwargs)
        changed: VerificationEvidence = change(wd, evidence)
        return changed

    wd.loop._verification_evidence = swap  # type: ignore[method-assign]


def _with_check(evidence: VerificationEvidence, **update: Any) -> VerificationEvidence:
    [check] = evidence.checks
    return evidence.model_copy(update={"checks": (check.model_copy(update=update),)})


def test_r07_a_model_requested_read_is_not_verification(tmp_path: Path) -> None:
    read = lp.tool("read_file", '{"path":"workspace/out/report.txt"}', "t2")
    wd = lp.world(tmp_path, [lp.write(), read, lp.done()])

    def use_model_read(wd: lp.World, evidence: VerificationEvidence) -> VerificationEvidence:
        state = wd.st.sm.state
        reads = [
            a.attempt_id
            for a in state.attempts.values()
            if state.steps[a.step_id].kind.value == "TOOL_CALL" and a.state.value == "COMPLETED"
        ]
        return _with_check(evidence, read_attempt_id=reads[-1])

    _tamper(wd, use_model_read)
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.ABORTED_SAFETY, "COMPLETION_REFUSED")


def test_r07_a_forged_observed_digest_is_refused(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(lp.GOOD), lp.done()])

    def forge(wd: lp.World, evidence: VerificationEvidence) -> VerificationEvidence:
        other = b.digest("something else")
        return _with_check(evidence, observed_digest=other, expected_digest=other)

    _tamper(wd, forge)
    assert _drive(wd).reason == "COMPLETION_REFUSED"


def test_r07_a_criterion_must_be_its_specification(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])

    def respec(wd: lp.World, evidence: VerificationEvidence) -> VerificationEvidence:
        return _with_check(evidence, path="workspace/out/elsewhere.txt")

    _tamper(wd, respec)
    assert _drive(wd).reason == "COMPLETION_REFUSED"


def test_r07_one_read_cannot_prove_two_criteria(tmp_path: Path) -> None:
    second_path = "workspace/out/other.txt"
    second = FileCriterion(
        kind="file_content", path=second_path, expected_digest=lp.CRITERION.expected_digest
    )
    script = [lp.write(), lp.write(lp.GOOD, path=second_path, use_id="t2"), lp.done()]
    wd = lp.world(tmp_path, script, specs=(lp.CRITERION, second))

    def reuse(wd: lp.World, evidence: VerificationEvidence) -> VerificationEvidence:
        first, other_check = evidence.checks
        again = other_check.model_copy(update={"read_attempt_id": first.read_attempt_id})
        return evidence.model_copy(update={"checks": (first, again)})

    _tamper(wd, reuse)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="one criterion only"):  # refused by the type
        _drive(wd)
    assert wd.st.sm.state.runs[wd.run.run_id].status is not R.COMPLETED


def test_r07_the_committed_write_must_be_what_was_verified(tmp_path: Path) -> None:
    from agent_core.safety.workspace import file_digest

    wd = lp.world(tmp_path, [lp.write(DRAFT), lp.done()])

    class Swap:
        """After the write committed DRAFT, someone else puts GOOD there."""

        def __init__(self, real: Any) -> None:
            self._real = real
            self.n = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def prepare(self, descriptor: Any, policy: Any) -> Any:
            prepared = self._real.prepare(descriptor, policy)
            self.n += 1
            if self.n == 2:
                (wd.tree.root / lp.TARGET).write_text(lp.GOOD)
            return prepared

    deps = wd.loop._deps
    wd.loop._deps = deps.__class__(**{**deps.__dict__, "files": Swap(deps.files)})
    wd.loop._committed_writes = lambda run_id: {}  # type: ignore[method-assign]
    assert file_digest(DRAFT.encode()) != lp.CRITERION.expected_digest
    outcome = _drive(wd)
    assert outcome.reason == "COMPLETION_REFUSED"
