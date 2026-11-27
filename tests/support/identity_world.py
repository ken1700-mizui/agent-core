"""Synthetic journals and a normalized state digest for the P-7 identity tests (S1).

Each scenario writes a journal through the real Runtime path (``tests/support``
worlds: the real ``SqliteJournal``, ledger, gate and recovery). ``semantic``
reduces a ``RuntimeState`` to its meaning only (Tasks, Runs, Steps, Attempts,
the parent/child order and the per-Run sequence numbers), so a derived index
added later is not part of the comparison; ``ledger_view`` reduces the cost side
(reservations, scope usage, stops) and the journal head. The same helpers are
used by ``tools/perf/state_identity.py`` for the before/after check across
versions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

from agent_core.runtime import recovery as rc
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.state_machine import RuntimeState
from agent_core.types.cost import BudgetScopeType
from agent_core.types.records import AttemptState, StepKind, StepStatus, TaskStatus
from agent_core.types.safety import StopScopeType
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w

A = AttemptState
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)


def deps(st: lw.Setup) -> rc.RecoveryDeps:
    return rc.RecoveryDeps(
        sm=st.sm,
        ledger=st.journal,
        owner=st.owner,
        clock=st.clock,
        report_key=st.keys.permit,
        journal=st.journal,
    )


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json")


def semantic(state: RuntimeState) -> dict[str, Any]:
    """The meaning of a state, with every map in a fixed order."""

    def table(items: Any) -> list[Any]:
        return [[str(k), _dump(v)] for k, v in sorted(items.items(), key=lambda kv: str(kv[0]))]

    def order(items: Any) -> list[Any]:
        return [
            [str(k), [str(x) for x in v]]
            for k, v in sorted(items.items(), key=lambda kv: str(kv[0]))
        ]

    return {
        "tasks": table(state.tasks),
        "runs": table(state.runs),
        "steps": table(state.steps),
        "attempts": table(state.attempts),
        "run_steps": order(state.run_steps),
        "step_attempts": order(state.step_attempts),
        "run_seqs": sorted([str(k), v] for k, v in state.run_seqs.items()),
    }


def ledger_view(st: lw.Setup) -> dict[str, Any]:
    """Reservations, holds, costs, stops and the journal head."""
    state = st.sm.state
    reservations = sorted(
        {str(a.reservation_id) for a in state.attempts.values() if a.reservation_id is not None}
    )
    scopes = [(BudgetScopeType.TASK, str(t)) for t in sorted(map(str, state.tasks))]
    scopes += [(BudgetScopeType.RUN, str(r)) for r in sorted(map(str, state.runs))]
    head = st.journal.head(b.TENANT)
    return {
        "reservations": [_dump(st.journal.reservation(r)) for r in reservations],
        "scopes": [_dump(st.journal.scope_usage(kind, sid)) for kind, sid in scopes],
        "stops": sorted(json.dumps(_dump(e), sort_keys=True) for e in st.journal.stop_entries()),
        "head": None if head is None else _dump(head),
    }


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- scenarios


def shape_a(st: lw.Setup, steps: int = 40) -> lw.Setup:
    """One Run with many Steps (each created, started, cancelled)."""
    run = w.running_run(st.sm)
    for _ in range(steps):
        step = st.sm.create_step(run, StepKind.TOOL_CALL)
        step = st.sm.set_step_status(step, StepStatus.IN_PROGRESS)
        st.sm.set_step_status(step, StepStatus.CANCELLED)
    return st


def shape_b(st: lw.Setup, runs: int = 12) -> lw.Setup:
    """Many Runs with three Steps each, every Task then completed or cancelled."""
    for i in range(runs):
        task = w.task(st.sm)
        run = w.running_run(st.sm, task)
        for _ in range(3):
            step = st.sm.create_step(run, StepKind.TOOL_CALL)
            step = st.sm.set_step_status(step, StepStatus.IN_PROGRESS)
            st.sm.set_step_status(step, StepStatus.CANCELLED)
        lw.end_run(st, st.sm.state.runs[run.run_id])
        if i % 2:
            st.sm.set_task_status(st.sm.state.tasks[task.task_id], TaskStatus.CANCELLED)
    return st


def shape_c(st: lw.Setup, runs: int = 3) -> lw.Setup:
    """Runs whose Steps each run one tool Attempt through the whole permit path."""
    usage = lw.zero_usage()
    for _ in range(runs):
        run = w.running_run(st.sm)
        for _ in range(3):
            step = lw.step_in(st, run)
            attempt = lw.consume(st, lw.issued(st, step))
            gate = lw.gate(st)
            started = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]
            gate.finish(started, SyntheticResult(outcome="COMPLETED", usage=usage, final=True))
            st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    return st


def _started(st: lw.Setup) -> Any:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, micro=100_000)
    attempt = lw.fixed_attempt(st, step, scopes)
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, lw.reserve(st, attempt, 5_000))))
    gate = lw.gate(st)
    return gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]


def stop_and_recover(st: lw.Setup) -> lw.Setup:
    """A Run stopped by the owner while an Attempt holds budget, then a restart
    and the recovery (the Run ends CANCELLED, the stop is kept)."""
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.reserve(st, lw.fixed_attempt(st, step, scopes))
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.owner(lw.stop_op(StopScopeType.RUN, str(run_id)))
    st2 = st.reopen()
    rc.recover(deps(st2), deadlines=DEADLINES, run_ids=list(st2.sm.state.runs))
    return st2


def consumed_and_recover(st: lw.Setup) -> lw.Setup:
    """A crash after the permit was consumed, before the start: the recovery."""
    step = lw.step_in(st)
    lw.consume(st, lw.issued(st, step))
    st2 = st.reopen()
    rc.recover(deps(st2), deadlines=DEADLINES, run_ids=list(st2.sm.state.runs))
    return st2


def unknown_outcome(st: lw.Setup) -> lw.Setup:
    """A started call whose result is lost: after the restart and the recovery
    the Attempt stays OUTCOME_UNKNOWN with its hold."""

    def boom(_authorization: Any) -> SyntheticResult:
        raise RuntimeError("synthetic: the operation's result is lost")

    step = lw.step_in(st)
    attempt = lw.consume(st, lw.issued(st, step))
    with pytest.raises(RuntimeError):
        lw.gate(st).execute(attempt, run=boom, **lw.actual(st, attempt))
    st2 = st.reopen()
    rc.recover(deps(st2), deadlines=DEADLINES, run_ids=list(st2.sm.state.runs))
    assert any(a.state is A.OUTCOME_UNKNOWN for a in st2.sm.state.attempts.values())
    return st2


def late_reconciliation(st: lw.Setup) -> lw.Setup:
    """An unknown outcome settled after the recovery ended its Run, by the signed
    execution report (outcome and cost in one transaction)."""
    attempt = _started(st)
    current = st.sm.state.attempts[attempt.attempt_id]
    result = SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)
    report = lw.gate(st).execution_report(current, result)
    st2 = st.reopen()
    d = deps(st2)
    rc.recover(d, deadlines=DEADLINES, run_ids=list(st2.sm.state.runs))
    outcome = rc.reconcile(d, attempt.attempt_id, rc.ExecutionEvidence(report=report))
    assert outcome.outcome is rc.Reconciled.EXECUTED
    return st2


SCENARIOS: dict[str, Callable[[lw.Setup], lw.Setup]] = {
    "A": shape_a,
    "B": shape_b,
    "C": shape_c,
    "stop": stop_and_recover,
    "recovery": consumed_and_recover,
    "unknown": unknown_outcome,
    "late": late_reconciliation,
}
