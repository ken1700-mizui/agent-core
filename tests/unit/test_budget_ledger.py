"""Budget ledger in the Journal's SQLite database (WP-09B; D 10.2, 11; E 4, 38;
AUDIT-01; D.V10, V11; E.Z6, Z9, Z42). Synthetic data, XTU only."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_ledger
from agent_core.adapters.replay_model import REPLAY_USAGE
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    ReservationState,
)
from agent_core.types.errors import (
    BudgetBlockedError,
    BudgetCapMissingError,
    BudgetExceededError,
    CostOverflowError,
    CounterCapExceededError,
    DuplicateUsageReportError,
    JournalSchemaError,
    LedgerKeyMissingError,
    LedgerPairingError,
    OwnerAuthorizationError,
    PermitRejectedError,
    ReservationAmountError,
    ReservationExpiredError,
    ReservationMismatchError,
    ReservationReuseError,
    ReservationStateError,
    TransitionPreconditionError,
)
from agent_core.types.ids import (
    AttemptId,
    ExperimentId,
    ReservationId,
    RunGrantId,
)
from agent_core.types.ledger import (
    LedgerBatch,
    MarkUnknown,
    Reconcile,
    RecoverInFlight,
    ReleaseReservation,
    ReportUsage,
    SetBudgetCap,
    SetRunWindow,
    Unexplained,
    VoidAllocation,
)
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.records import AttemptState, RunStatus, StepStatus
from agent_core.types.values import MAX_INT64
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s

R = ReservationState
A = AttemptState


@pytest.fixture
def st(tmp_path: Path) -> Any:
    setup = lw.setup(tmp_path)
    yield setup
    if not setup.journal.closed:
        setup.journal.close()


def _ready(st: Any, **caps: int) -> tuple[Any, Any, Any]:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, **caps)
    return step, scopes, lw.fixed_attempt(st, step, scopes)


def _next_step_attempt(st: Any, step: Any, previous: Any, **caps: int) -> Any:
    """The next attempt of the same Run, in a new Step. The previous attempt ends
    first (A.I4, WP04-R03) without releasing its reservation, so its hold stays."""
    previous = st.sm.state.attempts[previous.attempt_id]
    if previous.state is not A.ABORTED_BEFORE_DISPATCH:
        lw.abort(st, previous, release=False)
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.CANCELLED)
    other = lw.step_in(st, st.sm.state.runs[step.run_id])
    scopes = lw.scopes_of(st.sm, other)
    lw.caps(st, scopes, **caps)
    return lw.fixed_attempt(st, other, scopes)


def _head(st: Any) -> Any:
    return st.journal.head(b.TENANT)


def _usage(
    micro: int | None,
    *,
    status: UsageStatus = UsageStatus.FINAL,
    tokens: tuple[int, int] = (10, 5),
) -> Usage:
    return Usage(
        status=status,
        input_tokens=tokens[0] if status is not UsageStatus.UNKNOWN else None,
        output_tokens=tokens[1] if status is not UsageStatus.UNKNOWN else None,
        total_tokens=sum(tokens) if status is UsageStatus.FINAL else None,
        cost=None if micro is None else lw.xtu(micro),
        cost_status=CostStatus.UNKNOWN if micro is None else CostStatus.FINAL,
        synthetic=True,
    )


# ---------------------------------------------------------------- AUDIT-01: allocate, then activate


def test_allocated_id_holds_nothing_and_grants_nothing(st: Any) -> None:
    _, scopes, attempt = _ready(st)
    view = st.journal.reservation(attempt.reservation_id)
    assert view.state is R.ID_ALLOCATED
    assert (view.reserved_micro, view.hold_micro, view.spent_micro) == (0, 0, 0)
    usage = st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id))
    assert (usage.hold_micro, usage.spent_micro) == (0, 0)


def test_activation_reserves_in_every_scope(st: Any) -> None:
    _, scopes, attempt = _ready(st)
    attempt = lw.reserve(st, attempt, 1_000)
    assert attempt.state is A.BUDGET_RESERVED
    view = st.journal.reservation(attempt.reservation_id)
    assert view.state is R.RESERVED
    assert (view.reserved_micro, view.hold_micro) == (1_000, 1_000)
    assert view.action_hash == attempt.action_hash
    for kind, scope_id in (
        (BudgetScopeType.STEP, scopes.step_id),
        (BudgetScopeType.RUN, scopes.run_id),
        (BudgetScopeType.TASK, scopes.task_id),
        (BudgetScopeType.USER, scopes.user_id),
        (BudgetScopeType.TENANT, scopes.tenant_id),
        (BudgetScopeType.PERIOD, scopes.period_id),
    ):
        assert st.journal.scope_usage(kind, str(scope_id)).hold_micro == 1_000


@pytest.mark.parametrize(
    ("amount", "reason"),
    [(299, "BELOW_REQUIRED"), (5_001, "ABOVE_COST_CAP")],
)
def test_amount_must_cover_the_required_and_stay_within_the_cost_cap(
    st: Any, amount: int, reason: str
) -> None:
    # The required amount is the costly tool's trusted manifest estimate (300).
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.allowed(st, lw.fixed_attempt(st, step, scopes, costly=True))
    before = _head(st)
    with pytest.raises(ReservationAmountError, match=reason):
        lw.reserve(st, attempt, amount)
    # Nothing stored: neither the journal event nor the ledger row changed.
    assert _head(st) == before
    assert st.sm.state.attempts[attempt.attempt_id].state is A.POLICY_ALLOWED
    assert st.journal.reservation(attempt.reservation_id).state is R.ID_ALLOCATED


@pytest.mark.parametrize("level", ["step", "run", "task", "user", "tenant", "period"])
def test_every_level_is_checked_in_the_same_transaction(st: Any, level: str) -> None:
    _, _, attempt = _ready(st, **{level: 999})
    with pytest.raises(BudgetExceededError) as excinfo:
        lw.reserve(st, attempt, 1_000)
    assert level.upper() in excinfo.value.reasons
    assert st.journal.reservation(attempt.reservation_id).state is R.ID_ALLOCATED


def test_experiment_scope_is_checked_when_present(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step, experiment=b.new_id(ExperimentId))
    lw.caps(st, scopes, experiment=500)
    attempt = lw.fixed_attempt(st, step, scopes)
    with pytest.raises(BudgetExceededError, match="EXPERIMENT"):
        lw.reserve(st, attempt, 1_000)


def test_a_scope_without_a_cap_is_refused(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.grant_only(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes)
    with pytest.raises(BudgetCapMissingError):
        lw.reserve(st, attempt, 1_000)


def test_budget_is_exhausted_across_attempts_and_the_last_one_is_refused(st: Any) -> None:
    step, _scopes, first = _ready(st, run=1_500)
    lw.reserve(st, first, 1_000)
    second = _next_step_attempt(st, step, first, run=1_500)
    with pytest.raises(BudgetExceededError, match="RUN"):
        lw.reserve(st, second, 1_000)


def test_a_new_run_does_not_reset_the_task_budget(st: Any) -> None:
    _step, scopes, attempt = _ready(st, task=1_500)
    attempt = lw.reserve(st, attempt, 1_000)
    # The first Run ends; its reservation is not released (no proof it was unused).
    lw.abort(st, attempt, release=False)
    lw.end_run(st, st.sm.state.runs[scopes.run_id])
    task = st.sm.state.tasks[scopes.task_id]
    run_two = st.sm.start_run(task, run_grant_ref=b.new_id(RunGrantId))
    run_two = st.sm.set_run_status(run_two, RunStatus.QUEUED)
    run_two = st.sm.set_run_status(run_two, RunStatus.RUNNING)
    step_two = lw.step_in(st, run_two)
    scopes_two = lw.scopes_of(st.sm, step_two)
    lw.caps(st, scopes_two, task=1_500)
    attempt_two = lw.fixed_attempt(st, step_two, scopes_two)
    with pytest.raises(BudgetExceededError, match="TASK"):
        lw.reserve(st, attempt_two, 1_000)


# ---------------------------------------------------------------- reuse, double reservation, moving


def test_reservation_ids_cannot_be_reused_or_moved(st: Any) -> None:
    step, _scopes, attempt = _ready(st)
    # Activating another attempt's reservation is refused (no moving between attempts).
    attempt = lw.allowed(st, attempt)
    moved = lw.activate_op(attempt, 1_000).model_copy(update={"attempt_id": b.new_id(AttemptId)})
    with pytest.raises((ReservationMismatchError, LedgerPairingError)):
        st.sm.advance_attempt(
            attempt, A.BUDGET_RESERVED, w.evidence_for(attempt, A.BUDGET_RESERVED), ledger=(moved,)
        )
    # A later attempt cannot take the same reservation ID (UNIQUE, E 38.3).
    lw.abort(st, attempt, release=False)
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.CANCELLED)
    other_step = lw.step_in(st, st.sm.state.runs[step.run_id])
    other = st.sm.create_attempt(other_step)
    other = st.sm.advance_attempt(other, A.VALIDATED, w.evidence_for(other, A.VALIDATED))
    evidence = w.evidence_for(other, A.CONTENT_FIXED, reservation_id=attempt.reservation_id)
    reuse = lw.allocate_op(other, lw.rsv(attempt))
    # The runtime refuses it first (WP-04) ...
    with pytest.raises(TransitionPreconditionError):
        st.sm.advance_attempt(other, A.CONTENT_FIXED, evidence, ledger=(reuse,))
    # ... and the ledger refuses it on its own (reservation ID and Attempt are UNIQUE).
    st.journal.close()
    raw = _Raw(st.path)
    try:
        with pytest.raises(ReservationReuseError, match="RESERVATION_ID_USED"):
            raw.op(reuse)
        second_for_attempt = reuse.model_copy(
            update={"reservation_id": b.new_id(ReservationId), "attempt_id": attempt.attempt_id}
        )
        with pytest.raises(ReservationReuseError, match="ATTEMPT_HAS_RESERVATION"):
            raw.op(second_for_attempt)
    finally:
        raw.conn.close()


def test_activation_happens_once(st: Any) -> None:
    _, _, attempt = _ready(st)
    attempt = lw.reserve(st, attempt, 1_000)
    with pytest.raises(LedgerPairingError):
        st.journal.apply_ledger((lw.activate_op(attempt, 1_000),))
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000


def test_expired_allocations_are_voided_and_never_used(st: Any) -> None:
    _, _, attempt = _ready(st)
    attempt = lw.allowed(st, attempt)
    st.clock.now += 60_001
    with pytest.raises(ReservationExpiredError):
        lw.reserve(st, attempt, 1_000)
    st.journal.apply_ledger((VoidAllocation(op="void", reservation_id=lw.rsv(attempt)),))
    assert st.journal.reservation(attempt.reservation_id).state is R.VOID
    st.clock.now -= 60_001
    with pytest.raises(ReservationStateError):
        lw.reserve(st, attempt, 1_000)


# ---------------------------------------------------------------- pairing with runtime transitions


def test_content_fixed_without_the_allocation_is_refused(st: Any) -> None:
    step = lw.step_in(st)
    attempt = st.sm.create_attempt(step)
    attempt = st.sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    before = _head(st)
    with pytest.raises(LedgerPairingError):
        st.sm.advance_attempt(attempt, A.CONTENT_FIXED, w.evidence_for(attempt, A.CONTENT_FIXED))
    assert _head(st) == before


def test_budget_reserved_without_the_activation_is_refused(st: Any) -> None:
    _, _, attempt = _ready(st)
    attempt = lw.allowed(st, attempt)
    with pytest.raises(LedgerPairingError):
        st.sm.advance_attempt(
            attempt, A.BUDGET_RESERVED, w.evidence_for(attempt, A.BUDGET_RESERVED)
        )
    assert st.journal.reservation(attempt.reservation_id).state is R.ID_ALLOCATED


def test_bound_operations_need_their_transition(st: Any) -> None:
    step, _, attempt = _ready(st)
    allowed = lw.allowed(st, attempt)
    # An activation sent alone (without its transition) is refused.
    with pytest.raises(LedgerPairingError):
        st.journal.apply_ledger((lw.activate_op(allowed, 1_000),))
    # An allocation sent with another transition is refused.
    lw.abort(st, allowed, release=False)
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.CANCELLED)
    other_step = lw.step_in(st, st.sm.state.runs[step.run_id])
    other = st.sm.create_attempt(other_step)
    with pytest.raises(LedgerPairingError):
        st.sm.advance_attempt(
            other,
            A.VALIDATED,
            w.evidence_for(other, A.VALIDATED),
            ledger=(lw.allocate_op(other, b.new_id(ReservationId)),),
        )


def test_evidence_and_operation_must_name_the_same_reservation(st: Any) -> None:
    step = lw.step_in(st)
    attempt = st.sm.create_attempt(step)
    attempt = st.sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    evidence = w.evidence_for(attempt, A.CONTENT_FIXED, reservation_id=b.new_id(ReservationId))
    with pytest.raises(LedgerPairingError):
        st.sm.advance_attempt(
            attempt,
            A.CONTENT_FIXED,
            evidence,
            ledger=(lw.allocate_op(attempt, b.new_id(ReservationId)),),
        )


# ---------------------------------------------------------------- owner operations


def test_owner_operations_need_the_owner_mac(st: Any) -> None:
    op = SetBudgetCap(
        op="set_budget_cap", scope_type=BudgetScopeType.RUN, scope_id="x", cap=lw.xtu(1)
    )
    batch = LedgerBatch(ops=(op,))
    with pytest.raises(OwnerAuthorizationError):
        st.journal.apply_owner_ledger(batch, owner_mac="0" * 64)
    with pytest.raises(LedgerPairingError):
        st.journal.apply_ledger((op,))
    assert st.journal.scope_usage(BudgetScopeType.RUN, "x").cap_micro is None


def test_without_keys_owner_operations_are_refused(tmp_path: Path) -> None:
    journal = lw.open_journal(tmp_path / "j.db", lw.ManualClock(), None)
    try:
        op = SetBudgetCap(
            op="set_budget_cap", scope_type=BudgetScopeType.RUN, scope_id="x", cap=lw.xtu(1)
        )
        with pytest.raises(LedgerKeyMissingError):
            journal.apply_owner_ledger(LedgerBatch(ops=(op,)), owner_mac="0" * 64)
    finally:
        journal.close()


# ---------------------------------------------------------------- non-monetary limits


@pytest.mark.parametrize(
    ("counter", "cap"),
    [("tool_calls", 1), ("work_units", 1)],
)
def test_counters_stop_new_reservations(st: Any, counter: str, cap: int) -> None:
    step, scopes, first = _ready(st, **{counter: cap})
    lw.reserve(st, first, 100)
    second = _next_step_attempt(st, step, first, **{counter: cap})
    with pytest.raises(CounterCapExceededError, match=counter.upper()):
        lw.reserve(st, second, 100)
    view = st.journal.counter(BudgetScopeType.RUN, str(scopes.run_id), CounterKind[counter.upper()])
    assert (view.used, view.cap) == (1, cap)


def test_retries_are_counted_per_run(st: Any) -> None:
    step, scopes, first = _ready(st, retries=0)
    first = lw.reserve(st, first, 100)
    lw.abort(st, first, release=True)
    retry = lw.fixed_attempt(st, step, scopes)
    with pytest.raises(CounterCapExceededError, match="RETRIES"):
        lw.reserve(st, retry, 100)


def test_elapsed_time_limit(st: Any) -> None:
    _, scopes, attempt = _ready(st)
    st.owner(SetRunWindow(op="set_run_window", run_id=scopes.run_id, max_elapsed_ms=1))
    st.clock.now += 2
    with pytest.raises(CounterCapExceededError, match="ELAPSED"):
        lw.reserve(st, attempt, 100)


# ---------------------------------------------------------------- integer arithmetic


def test_overflowing_totals_are_refused_and_nothing_is_stored(st: Any) -> None:
    step, _scopes, first = _ready(st, micro=MAX_INT64)
    first = lw.reserve(st, first, 5_000, cap=5_000)
    # Raise the stored hold to near the 64-bit limit in this test DB, so the next
    # total would overflow (the API cannot produce such a hold).
    second = _next_step_attempt(st, step, first, micro=MAX_INT64)
    st.journal.close()
    conn = sqlite3.connect(st.path)
    # Row and its last ledger entry together, so the open-time check (WP09-R07)
    # accepts the database and the activation's own overflow check is reached.
    for table, key in (("reservations", "reservation_id"), ("ledger_entries", "reservation_id")):
        conn.execute(
            f"UPDATE {table} SET hold_micro = ? WHERE {key} = ?",  # noqa: S608 (test constant)
            (MAX_INT64 - 10, str(first.reservation_id)),
        )
    conn.commit()
    conn.close()
    st2 = st.reopen()
    try:
        attempt = st2.sm.state.attempts[second.attempt_id]
        with pytest.raises(CostOverflowError):
            lw.reserve(st2, attempt, 100)
        assert st2.journal.reservation(second.reservation_id).state is R.ID_ALLOCATED
    finally:
        st2.journal.close()


# ---------------------------------------------------------------- after consumption (cursor level)


class _Raw:
    """The ledger functions on a raw connection (unit level; WP-09C consumes through
    the journal). Each ``tx`` is one BEGIN IMMEDIATE transaction."""

    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path, isolation_level=None)

    def tx(self, fn: Any) -> Any:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(self.conn.cursor())
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return result

    def op(self, op: Any, now: int = s.NOW) -> Any:
        return self.tx(lambda cur: sqlite_ledger.apply_op(cur, op, now_ms=now))

    def view(self, rsv: Any) -> Any:
        return sqlite_ledger.read_reservation(self.conn.cursor(), rsv)


# ---------------------------------------------------------------- after consumption (trusted path)
# Usage and releases come only from the gate's MAC-signed reports (WP09-R04), so
# these tests drive the journal and the gate instead of raw ledger rows.


def _started(st: Any, *, model: bool = False, costly: bool = False, amount: int = 1_000) -> Any:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, micro=100_000)
    attempt = lw.fixed_attempt(st, step, scopes, model=model, costly=costly)
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, amount))))
    gate = lw.gate(st)
    started, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    return gate, started, scopes


def _result(usage: Usage, final: bool = True) -> SyntheticResult:
    return SyntheticResult(outcome="COMPLETED", usage=usage, final=final)


def test_final_known_usage_releases_the_rest_and_keeps_the_spent_cost(st: Any) -> None:
    gate, attempt, _ = _started(st, costly=True)
    gate.finish(attempt, _result(_usage(300)))
    view = st.journal.reservation(attempt.reservation_id)
    assert view.state is R.USAGE_REPORTED
    assert (view.hold_micro, view.spent_micro) == (0, 300)


@pytest.mark.parametrize("status", [UsageStatus.UNKNOWN, UsageStatus.PARTIAL])
def test_unknown_or_partial_usage_keeps_the_hold(st: Any, status: UsageStatus) -> None:
    gate, attempt, _ = _started(st, model=True, amount=5_000)
    gate.finish(attempt, _result(_usage(None, status=status)))
    view = st.journal.reservation(attempt.reservation_id)
    assert view.state is R.UNKNOWN
    assert view.hold_micro == 5_000
    assert view.spent_known is False


def test_not_final_usage_is_not_a_final_cost(st: Any) -> None:
    gate, attempt, _ = _started(st, costly=True)
    gate.finish(attempt, _result(_usage(300), final=False))
    view = st.journal.reservation(attempt.reservation_id)
    assert view.state is R.UNKNOWN
    assert view.hold_micro == 1_000
    assert view.spent_lower_micro == 300


def test_duplicate_usage_reports_are_refused(st: Any) -> None:
    gate, attempt, _ = _started(st, costly=True)
    report = gate.execution_report(attempt, _result(_usage(300)))
    op = ReportUsage(
        op="report_usage",
        reservation_id=attempt.reservation_id,
        attempt_id=attempt.attempt_id,
        usage_event_id="u1",
        report=report,
    )
    st.journal.apply_ledger((op,))
    with pytest.raises((DuplicateUsageReportError, ReservationStateError)):
        st.journal.apply_ledger((op,))
    assert st.journal.reservation(attempt.reservation_id).spent_micro == 300


def test_replayed_calls_report_this_calls_zero_usage(st: Any) -> None:
    gate, attempt, _ = _started(st, model=True, amount=5_000)
    gate.finish(attempt, _result(REPLAY_USAGE))
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro, view.spent_micro) == (R.USAGE_REPORTED, 0, 0)
    assert "source_usage" not in ReportUsage.model_fields


def test_overrun_is_recorded_in_full_and_stops_new_reservations(st: Any) -> None:
    gate, attempt, scopes = _started(st, model=True, amount=5_000)
    # 2,000 input and 1,000 output tokens at the test prices: 2,000 + 4,000 = 6,000.
    gate.finish(attempt, _result(_usage(None, tokens=(2_000, 1_000))))
    view = st.journal.reservation(attempt.reservation_id)
    assert view.spent_micro == 6_000  # the actual amount, beyond the reservation
    kinds = [e.kind for e in st.journal.ledger_entries(attempt.reservation_id)]
    assert "OVERRUN" in kinds
    another = lw.next_attempt(st, st.sm.state.steps[scopes.step_id], attempt, micro=100_000)
    with pytest.raises(BudgetBlockedError):
        lw.reserve(st, another, 100)


def test_unknown_outcome_holds_until_reconciled(st: Any) -> None:
    _, attempt, _ = _started(st)
    st.journal.apply_ledger(
        (
            MarkUnknown(
                op="mark_unknown",
                reservation_id=attempt.reservation_id,
                attempt_id=attempt.attempt_id,
            ),
        )
    )
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000
    st.owner(
        Reconcile(
            op="reconcile",
            reservation_id=attempt.reservation_id,
            actual=lw.xtu(250),
            evidence_ref=b.digest("synthetic-reconciliation"),
        )
    )
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro, view.spent_micro) == (R.RECONCILED, 0, 250)


def test_consumed_is_released_only_with_a_not_dispatched_report(st: Any) -> None:
    attempt = lw.consume(st, lw.issued(st, lw.step_in(st)))
    gate = lw.gate(st)
    aborted = gate.abort_not_dispatched(attempt)
    view = st.journal.reservation(attempt.reservation_id)
    assert aborted.state is A.ABORTED_BEFORE_DISPATCH
    assert (view.state, view.hold_micro, view.spent_micro) == (R.RELEASED, 0, 0)


def test_dispatched_reservations_are_never_released(st: Any) -> None:
    gate, attempt, _ = _started(st)
    with pytest.raises(PermitRejectedError, match="ALREADY_STARTED"):
        gate.not_dispatched_report(attempt)
    assert st.journal.reservation(attempt.reservation_id).hold_micro == 1_000


def test_unexplained_differences_block_new_spending(st: Any) -> None:
    _, attempt, scopes = _started(st)
    st.owner(
        Unexplained(
            op="unexplained",
            reservation_id=attempt.reservation_id,
            delta_micro=5,
            evidence_ref=b.digest("diff"),
        )
    )
    usage = st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id))
    assert usage.blocked
    assert usage.spent_micro == 5


def test_restart_keeps_holds_and_recovers_in_flight_as_unknown(st: Any) -> None:
    attempt = lw.consume(st, lw.issued(st, lw.step_in(st)))
    scopes_task = st.sm.state.runs[st.sm.state.steps[attempt.step_id].run_id].task_id
    st2 = st.reopen()
    try:
        assert st2.journal.reservation(attempt.reservation_id).state is R.CONSUMED
        st2.journal.apply_ledger((RecoverInFlight(op="recover_in_flight"),))
        view = st2.journal.reservation(attempt.reservation_id)
        assert (view.state, view.hold_micro) == (R.UNKNOWN, 1_000)
        assert st2.journal.scope_usage(BudgetScopeType.TASK, str(scopes_task)).hold_micro == 1_000
    finally:
        st2.journal.close()


def test_released_reservation_keeps_spent_and_is_not_released_twice(st: Any) -> None:
    _, _, attempt = _ready(st)
    attempt = lw.reserve(st, attempt, 1_000)
    released = lw.abort(st, attempt, release=True)
    view = st.journal.reservation(attempt.reservation_id)
    assert released.state is A.ABORTED_BEFORE_DISPATCH
    assert (view.state, view.hold_micro, view.spent_micro) == (R.RELEASED, 0, 0)
    release = ReleaseReservation(
        op="release", reservation_id=lw.rsv(attempt), attempt_id=attempt.attempt_id, report=None
    )
    with pytest.raises(LedgerPairingError):
        st.journal.apply_ledger((release,))


# ---------------------------------------------------------------- concurrency on real SQLite


def test_concurrent_activations_on_separate_connections_serialize(st: Any) -> None:
    """Two Tasks race for the last tenant budget on two connections. BEGIN IMMEDIATE
    serializes them; exactly one is activated (cursor-level ledger, real SQLite locks)."""
    attempts = []
    for _ in range(2):
        step = lw.step_in(st)
        scopes = lw.scopes_of(st.sm, step)
        lw.caps(st, scopes, tenant=1_500)
        attempts.append(lw.allowed(st, lw.fixed_attempt(st, step, scopes)))
    st.journal.close()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def race(attempt: Any) -> None:
        raw = _Raw(st.path)
        raw.conn.execute("PRAGMA busy_timeout = 5000")
        barrier.wait()
        try:
            raw.op(lw.activate_op(attempt, 1_000))
            results.append("ok")
        except BudgetExceededError:
            results.append("refused")
        finally:
            raw.conn.close()

    threads = [threading.Thread(target=race, args=(a,)) for a in attempts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == ["ok", "refused"]
    raw = _Raw(st.path)
    holds = sorted(raw.view(a.reservation_id).hold_micro for a in attempts)
    raw.conn.close()
    assert holds == [0, 1_000]


def test_old_journal_schema_is_refused_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    journal = lw.open_journal(path, lw.ManualClock(), None)
    journal.close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    with pytest.raises(JournalSchemaError):
        lw.open_journal(path, lw.ManualClock(), None)
