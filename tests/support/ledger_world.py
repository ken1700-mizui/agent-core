"""SQLite journal + budget ledger setups for WP-09B/C tests (synthetic only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    FixedEstimate,
    ReservationScopes,
)
from agent_core.types.ids import ReservationId
from agent_core.types.ledger import (
    ActivateReservation,
    AllocateReservation,
    LedgerBatch,
    LedgerOp,
    SetBudgetCap,
    SetCounterCap,
    SetRunWindow,
)
from agent_core.types.records import Attempt, AttemptState, Run, Step
from agent_core.types.safety import ActionKind, LedgerKeys
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import runtime_world as w
from support import safety_world as s

PERIOD = "synthetic-2026-11"
CounterScope = Literal[BudgetScopeType.RUN, BudgetScopeType.TASK]
A = AttemptState


class ManualClock:
    def __init__(self, now: int = s.NOW) -> None:
        self.now = now

    def now_ms(self) -> int:
        return self.now


def xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def fixed(micro: int) -> FixedEstimate:
    return FixedEstimate(kind="fixed", amount=xtu(micro))


def new_keys() -> LedgerKeys:
    return LedgerKeys(owner=s.new_key(), permit=s.new_key())


def open_journal(path: Path, clock: Any, keys: LedgerKeys | None) -> SqliteJournal:
    return SqliteJournal.open(path, clock=clock, random=OsRandomSource(), keys=keys)


@dataclass
class Setup:
    path: Path
    journal: SqliteJournal
    sm: RuntimeStateMachine
    keys: LedgerKeys
    clock: ManualClock
    channel: OwnerApprovalChannel

    def owner(self, *ops: LedgerOp) -> None:
        batch = LedgerBatch(ops=ops)
        self.journal.apply_owner_ledger(batch, owner_mac=self.channel.sign_ledger(batch))

    def reopen(self) -> Setup:
        self.journal.close()
        journal = open_journal(self.path, self.clock, self.keys)
        return Setup(self.path, journal, w.machine(journal), self.keys, self.clock, self.channel)


def setup(tmp_path: Path, name: str = "journal.db") -> Setup:
    clock = ManualClock()
    keys = new_keys()
    path = tmp_path / name
    journal = open_journal(path, clock, keys)
    return Setup(path, journal, w.machine(journal), keys, clock, OwnerApprovalChannel(keys.owner))


def scopes_of(sm: RuntimeStateMachine, step: Step, experiment: Any = None) -> ReservationScopes:
    run = sm.state.runs[step.run_id]
    return ReservationScopes(
        tenant_id=b.TENANT,
        user_id=s.OWNER,
        task_id=run.task_id,
        run_id=run.run_id,
        step_id=step.step_id,
        period_id=PERIOD,
        experiment_id=experiment,
    )


def caps(st: Setup, scopes: ReservationScopes, micro: int = 10_000, **levels: int) -> None:
    """Every budget level, the enforced counters and the run window (explicit values)."""
    ids = {
        BudgetScopeType.STEP: str(scopes.step_id),
        BudgetScopeType.RUN: str(scopes.run_id),
        BudgetScopeType.TASK: str(scopes.task_id),
        BudgetScopeType.USER: str(scopes.user_id),
        BudgetScopeType.TENANT: str(scopes.tenant_id),
        BudgetScopeType.PERIOD: scopes.period_id,
    }
    if scopes.experiment_id is not None:
        ids[BudgetScopeType.EXPERIMENT] = str(scopes.experiment_id)
    ops: list[LedgerOp] = [
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=kind,
            scope_id=scope_id,
            cap=xtu(levels.get(kind.value.lower(), micro)),
        )
        for kind, scope_id in ids.items()
    ]
    for counter in (
        CounterKind.MODEL_CALLS,
        CounterKind.TOOL_CALLS,
        CounterKind.RETRIES,
        CounterKind.WORK_UNITS,
    ):
        counter_scopes: tuple[tuple[CounterScope, str], ...] = (
            (BudgetScopeType.RUN, str(scopes.run_id)),
            (BudgetScopeType.TASK, str(scopes.task_id)),
        )
        for kind, scope_id in counter_scopes:
            ops.append(
                SetCounterCap(
                    op="set_counter_cap",
                    scope_type=kind,
                    scope_id=scope_id,
                    counter=counter,
                    cap=levels.get(counter.value.lower(), 100),
                )
            )
    ops.append(SetRunWindow(op="set_run_window", run_id=scopes.run_id, max_elapsed_ms=3_600_000))
    st.owner(*ops)


def step_in(st: Setup, run: Run | None = None) -> Step:
    run = run if run is not None else w.running_run(st.sm)
    return w.step_in_progress(st.sm, run)


def allocate_op(
    attempt: Attempt, scopes: ReservationScopes, rsv: ReservationId
) -> AllocateReservation:
    return AllocateReservation(
        op="allocate",
        reservation_id=rsv,
        attempt_id=attempt.attempt_id,
        scopes=scopes,
        action_kind=ActionKind.TOOL_CALL,
        ttl_ms=60_000,
    )


def fixed_attempt(st: Setup, step: Step, scopes: ReservationScopes) -> Attempt:
    """PROPOSED -> VALIDATED -> CONTENT_FIXED (reservation ID allocated)."""
    attempt = st.sm.create_attempt(step)
    attempt = st.sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    rsv = b.new_id(ReservationId)
    evidence = w.evidence_for(attempt, A.CONTENT_FIXED, reservation_id=rsv)
    return st.sm.advance_attempt(
        attempt, A.CONTENT_FIXED, evidence, ledger=(allocate_op(attempt, scopes, rsv),)
    )


def activate_op(
    attempt: Attempt, amount: int, cap: int = 5_000, required: int = 100
) -> ActivateReservation:
    assert attempt.reservation_id is not None
    assert attempt.action_hash is not None
    return ActivateReservation(
        op="activate",
        reservation_id=attempt.reservation_id,
        attempt_id=attempt.attempt_id,
        action_hash=attempt.action_hash,
        estimate=fixed(required),
        amount=xtu(amount),
        cost_cap=xtu(cap),
    )


def allowed(st: Setup, attempt: Attempt) -> Attempt:
    return st.sm.advance_attempt(
        attempt, A.POLICY_ALLOWED, w.evidence_for(attempt, A.POLICY_ALLOWED)
    )


def reserve(st: Setup, attempt: Attempt, amount: int = 1_000, **kw: Any) -> Attempt:
    """POLICY_ALLOWED (no approval) -> BUDGET_RESERVED with the activation."""
    if attempt.state is A.CONTENT_FIXED:
        attempt = allowed(st, attempt)
    return st.sm.advance_attempt(
        attempt,
        A.BUDGET_RESERVED,
        w.evidence_for(attempt, A.BUDGET_RESERVED),
        ledger=(activate_op(attempt, amount, **kw),),
    )


def abort(st: Setup, attempt: Attempt, *, release: bool) -> Attempt:
    """ABORTED_BEFORE_DISPATCH, releasing the reservation only when asked."""
    from agent_core.types.ledger import ReleaseReservation

    ops: tuple[LedgerOp, ...] = ()
    if release and attempt.reservation_id is not None:
        ops = (
            ReleaseReservation(
                op="release",
                reservation_id=attempt.reservation_id,
                attempt_id=attempt.attempt_id,
                not_dispatched_ref=None,
            ),
        )
    evidence = w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH, not_dispatched_ref=None)
    return st.sm.advance_attempt(attempt, A.ABORTED_BEFORE_DISPATCH, evidence, ledger=ops)


def end_run(st: Setup, run: Run) -> Run:
    """Finish a Run as FAILED (synthetic termination) after its attempts are final."""
    from agent_core.types.records import RunStatus, StepStatus
    from agent_core.types.transitions import Termination, TerminationReason

    for step in st.sm.state.steps_of_run(run.run_id):
        if step.status is StepStatus.IN_PROGRESS:
            st.sm.set_step_status(step, StepStatus.CANCELLED)
    termination = Termination(
        kind="termination", reason=TerminationReason.OWNER_CANCELLED, evidence_ref=b.digest("end")
    )
    return st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.FAILED, termination)


def dispatched(st: Setup, step: Step) -> Attempt:
    """A dispatched attempt through the real ledger path (WP-09B/C)."""
    scopes = scopes_of(st.sm, step)
    caps(st, scopes)
    attempt = reserve(st, fixed_attempt(st, step, scopes), 1_000)
    path = (
        A.INTENT_RECORDED,
        A.PERMIT_ISSUED,
        A.PERMIT_CONSUMED,
        A.DISPATCH_AUTHORIZED,
        A.DISPATCH_STARTED,
    )
    return w.drive(st.sm, attempt, path)


def completed_scenario(st: Setup) -> Any:
    """A Task with one completed Run (synthetic), touching every entity kind."""
    from agent_core.types.records import RunStatus, StepStatus, TaskStatus

    the_task = w.task(st.sm)
    run = w.running_run(st.sm, the_task)
    step = w.step_in_progress(st.sm, run)
    attempt = dispatched(st, step)
    st.sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    verifying = st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.VERIFYING)
    st.sm.set_run_status(verifying, RunStatus.COMPLETED, w.passing_verification(the_task))
    st.sm.set_task_status(st.sm.state.tasks[the_task.task_id], TaskStatus.COMPLETED)
    return the_task


def rsv(attempt: Attempt) -> ReservationId:
    assert attempt.reservation_id is not None
    return attempt.reservation_id
