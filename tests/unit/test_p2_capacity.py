"""P-2 operating limits: levels, the journal probe and the Runtime's admission (tests first).

Three levels (NORMAL, WARNING, LIMITED) from the journal's event count and its
bytes (database file plus write-ahead log). At LIMITED, new Tasks, Runs and
Steps are refused before anything is written; finishing an existing Attempt,
stop and cancel, status, reconciliation, recovery and safety records go on. A
limit never releases a hold and never removes an event, and a level reached
stays reached after a restart. The thresholds are configuration; the values
used here are test values, not proposals.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.runtime import recovery as rc
from agent_core.runtime.capacity import CapacityGate
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.types.capacity import (
    CapacityLevel,
    CapacityLimits,
    CapacityUsage,
    NewWork,
    capacity_level,
)
from agent_core.types.errors import CapacityLimitedError
from agent_core.types.profile import PHASE1_SYNTHETIC
from agent_core.types.records import AttemptState, RunStatus, StepKind, StepStatus
from agent_core.types.safety import StopScopeType
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w

A = AttemptState
L = CapacityLevel
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)
BIG = 1 << 40


def _limits(events: int, *, warning: int | None = None, reserve: int = 0) -> CapacityLimits:
    return CapacityLimits(
        warning_events=warning if warning is not None else events,
        limit_events=events,
        warning_bytes=BIG,
        limit_bytes=BIG,
        reserve_events=reserve,
    )


# ---------------------------------------------------------------- levels


@pytest.mark.parametrize(
    ("events", "size", "new_work", "level"),
    [
        (9, 0, False, L.NORMAL),
        (10, 0, False, L.WARNING),
        (19, 0, False, L.WARNING),
        (20, 0, False, L.LIMITED),
        (17, 0, True, L.LIMITED),  # 17 + the reserve of 3 reaches the limit
        (16, 0, True, L.WARNING),
        (0, 100, False, L.WARNING),
        (0, 200, False, L.LIMITED),
        (0, 195, True, L.LIMITED),  # the byte reserve counts too
    ],
)
def test_the_level_of_a_usage(events: int, size: int, new_work: bool, level: L) -> None:
    limits = CapacityLimits(
        warning_events=10,
        limit_events=20,
        warning_bytes=100,
        limit_bytes=200,
        reserve_events=3,
        reserve_bytes=5,
    )
    usage = CapacityUsage(events=events, bytes=size)
    assert capacity_level(limits, usage, new_work=new_work) is level


def test_without_limits_the_level_is_normal() -> None:
    assert capacity_level(None, CapacityUsage(events=BIG, bytes=BIG)) is L.NORMAL


@pytest.mark.parametrize(
    "values",
    [
        {"warning_events": 11, "limit_events": 10},
        {"warning_events": 0, "limit_events": 10},
        {"warning_bytes": 300, "limit_bytes": 200},
    ],
)
def test_limits_out_of_order_are_refused(values: dict[str, int]) -> None:
    base = {"warning_events": 1, "limit_events": 10, "warning_bytes": 1, "limit_bytes": 200}
    with pytest.raises(ValueError, match="<="):
        CapacityLimits(**{**base, **values})


# ---------------------------------------------------------------- the probe


def test_the_journal_measures_its_events_and_its_files(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    w.running_run(st.sm)
    usage = st.journal.capacity_usage()
    head = st.journal.head(b.TENANT)
    assert head is not None
    assert usage.events == head.tenant_seq
    wal = Path(f"{st.path}-wal")
    expected = os.stat(st.path).st_size + (os.stat(wal).st_size if wal.exists() else 0)
    assert usage.bytes == expected


# ---------------------------------------------------------------- the Runtime


def _gated(st: lw.Setup, limits: CapacityLimits | None) -> lw.Setup:
    sm = RuntimeStateMachine.load(
        st.journal,
        ids=IdGenerator(b.SITE, SystemClock(), OsRandomSource()),
        clock=SystemClock(),
        verifier=w.AcceptSyntheticEvidence(),
        tenant_id=b.TENANT,
        engine=b.ENGINE,
        policy_version="policy-1",
        profile=PHASE1_SYNTHETIC,
        capacity=CapacityGate(limits, st.journal),
    )
    return dataclasses.replace(st, sm=sm)


def _at_limit(st: lw.Setup, *, reserve: int = 0) -> lw.Setup:
    events = st.journal.capacity_usage().events
    return _gated(st, _limits(events + reserve, reserve=reserve))


def _events(st: lw.Setup) -> int:
    return st.journal.capacity_usage().events


def test_new_tasks_runs_and_steps_are_refused_at_the_limit(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    task = w.task(st.sm)
    run = w.running_run(st.sm, w.task(st.sm))
    st = _at_limit(st)
    assert st.sm.capacity is not None
    assert st.sm.capacity.level() is L.LIMITED
    before = (_events(st), st.journal.head(b.TENANT))
    with pytest.raises(CapacityLimitedError) as refused:
        w.task(st.sm)
    assert refused.value.work == NewWork.TASK.value
    with pytest.raises(CapacityLimitedError):
        st.sm.start_run(
            st.sm.state.tasks[task.task_id], run_grant_ref=b.new_id(type(run.run_grant_ref))
        )
    with pytest.raises(CapacityLimitedError):
        st.sm.create_step(st.sm.state.runs[run.run_id], StepKind.TOOL_CALL)
    assert (_events(st), st.journal.head(b.TENANT)) == before  # nothing written


def test_below_the_limit_new_work_goes_on(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    w.task(st.sm)
    events = _events(st)
    st = _gated(st, _limits(events + 100, warning=events + 1))
    w.running_run(st.sm)  # NORMAL, then WARNING: admitted
    assert st.sm.capacity is not None
    assert st.sm.capacity.level() is L.WARNING


def test_the_reserve_of_the_new_work_is_counted(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    w.task(st.sm)
    events = _events(st)
    st = _gated(st, _limits(events + 5, warning=events, reserve=5))
    assert st.sm.capacity is not None
    assert st.sm.capacity.level() is L.WARNING  # not LIMITED yet without the reserve
    with pytest.raises(CapacityLimitedError):
        w.task(st.sm)


def _started(st: lw.Setup) -> Any:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, micro=100_000)
    attempt = lw.fixed_attempt(st, step, scopes)
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 5_000))))
    gate = lw.gate(st)
    return gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0], step


def test_an_existing_attempt_finishes_and_its_step_ends_at_the_limit(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt, step = _started(st)
    hold = st.journal.reservation(str(attempt.reservation_id))
    st = _at_limit(st)
    assert st.journal.reservation(str(attempt.reservation_id)) == hold  # the limit releases nothing
    result = SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)
    lw.gate(st).finish(st.sm.state.attempts[attempt.attempt_id], result)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.COMPLETED
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    with pytest.raises(CapacityLimitedError):  # but no next Step
        st.sm.create_step(st.sm.state.runs[step.run_id], StepKind.TOOL_CALL)


def test_stop_cancel_and_status_work_at_the_limit(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run = w.running_run(st.sm)
    st = _at_limit(st)
    st.owner(lw.stop_op(StopScopeType.RUN, str(run.run_id)))  # a safety record
    current = st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.CANCELLING)
    st.sm.set_run_status(current, RunStatus.CANCELLED)
    assert st.sm.state.runs[run.run_id].status is RunStatus.CANCELLED
    assert any(e.scope_id == str(run.run_id) for e in st.journal.stop_entries())


def test_recovery_and_reconciliation_work_at_the_limit_and_after_a_restart(
    tmp_path: Path,
) -> None:
    st = lw.setup(tmp_path)
    attempt, _step = _started(st)
    current = st.sm.state.attempts[attempt.attempt_id]
    result = SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)
    report = lw.gate(st).execution_report(current, result)
    limit = _events(st)
    again = st.reopen()
    again = _gated(again, _limits(limit))  # LIMITED after the restart too
    assert again.sm.capacity is not None
    assert again.sm.capacity.level() is L.LIMITED
    deps = rc.RecoveryDeps(
        sm=again.sm,
        ledger=again.journal,
        owner=again.owner,
        clock=again.clock,
        report_key=again.keys.permit,
        journal=again.journal,
    )
    rc.recover(deps, deadlines=DEADLINES, run_ids=list(again.sm.state.runs))
    assert again.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    outcome = rc.reconcile(deps, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert outcome.outcome is rc.Reconciled.EXECUTED
    assert again.sm.state.attempts[attempt.attempt_id].state is A.COMPLETED
    with pytest.raises(CapacityLimitedError):
        w.task(again.sm)


def test_a_limit_never_removes_an_event(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    w.running_run(st.sm)
    st = _at_limit(st)
    before = _events(st)
    for _ in range(3):
        with pytest.raises(CapacityLimitedError):
            w.task(st.sm)
    again = st.reopen()
    assert _events(again) == before
    assert len(again.sm.state.runs) == 1


def test_without_limits_the_journal_is_not_probed_for_new_work() -> None:
    class Probe:
        calls = 0

        def capacity_usage(self) -> CapacityUsage:
            self.calls += 1
            return CapacityUsage(events=0, bytes=0)

    probe = Probe()
    gate = CapacityGate(None, probe)
    assert gate.admit(NewWork.STEP) is L.NORMAL
    assert probe.calls == 0
