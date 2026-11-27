"""P-7 M1: the per-Run index of unresolved Attempts (tests first).

``RuntimeState.unresolved_attempts`` holds, for each Run with at least one
Attempt outside ``FINAL_ATTEMPT_STATES``, the number of such Attempts. It is
derived data, never a source of truth: it must equal a count over the Attempts
after every transition (including OUTCOME_UNKNOWN, a late reconciliation and a
recovery), be the same after a Rebuild as after live execution, and an
inconsistency is an error, never a normal state. The WP04-R03 checks keep their
meaning; only the way they are answered changes.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime import state_machine as smod
from agent_core.runtime.state_machine import (
    FINAL_ATTEMPT_STATES,
    RuntimeState,
    apply_event,
    check_indexes,
    rebuild_with_head,
)
from agent_core.types.errors import (
    StateIndexError,
    StateRebuildError,
    TransitionPreconditionError,
)
from agent_core.types.events import Event
from agent_core.types.records import AttemptState, RunStatus, StepKind, StepStatus, TaskStatus
from agent_core.types.transitions import Termination, TerminationReason
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w

A = AttemptState
R = RunStatus


def _count(state: RuntimeState) -> dict[Any, int]:
    """The index recomputed by a full scan (the reference)."""
    counts: Counter[Any] = Counter()
    for attempt in state.attempts.values():
        if attempt.state not in FINAL_ATTEMPT_STATES:
            counts[state.steps[attempt.step_id].run_id] += 1
    return dict(counts)


def _events(journal: Any) -> list[Event]:
    events: list[Event] = []
    while page := journal.read_tenant(b.TENANT, after_tenant_seq=len(events), limit=500):
        events.extend(page)
    return events


@pytest.mark.parametrize("name", sorted(iw.SCENARIOS))
def test_the_index_matches_a_full_count_after_every_event(name: str, tmp_path: Path) -> None:
    st = iw.SCENARIOS[name](lw.setup(tmp_path))
    state = RuntimeState.empty()
    for event in _events(st.journal):
        state = apply_event(state, event)
        assert dict(state.unresolved_attempts) == _count(state)
        assert all(v > 0 for v in state.unresolved_attempts.values())


@pytest.mark.parametrize("name", sorted(iw.SCENARIOS))
def test_the_index_after_a_rebuild_is_the_index_of_live_execution(
    name: str, tmp_path: Path
) -> None:
    st = iw.SCENARIOS[name](lw.setup(tmp_path))
    live = st.sm.state.unresolved_attempts
    assert dict(live) == _count(st.sm.state)
    assert dict(st.reopen().sm.state.unresolved_attempts) == dict(live)


def test_unknown_and_late_reconciliation_move_the_count(tmp_path: Path) -> None:
    (tmp_path / "u").mkdir()
    unknown = iw.unknown_outcome(lw.setup(tmp_path / "u"))
    assert sum(unknown.sm.state.unresolved_attempts.values()) == 1  # OUTCOME_UNKNOWN is open
    (tmp_path / "l").mkdir()
    late = iw.late_reconciliation(lw.setup(tmp_path / "l"))
    assert dict(late.sm.state.unresolved_attempts) == {}  # settled by the reconciliation


# ---------------------------------------------------------------- the rules keep their meaning


def _with_open_attempt() -> tuple[Any, Any, Any]:
    sm = w.machine()
    task = w.task(sm)
    run = w.running_run(sm, task)
    step = w.step_in_progress(sm, run)
    attempt = sm.create_attempt(step)
    return sm, run, attempt


def test_a_new_step_is_refused_while_an_attempt_is_open() -> None:
    sm, run, _attempt = _with_open_attempt()
    assert sm.state.unresolved_attempts == {run.run_id: 1}
    with pytest.raises(TransitionPreconditionError, match="WP04-R03"):
        sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)


def test_a_new_run_is_refused_while_an_unknown_outcome_remains() -> None:
    sm = w.machine()
    task = w.task(sm)
    run = w.running_run(sm, task)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    termination = Termination(
        kind="termination", reason=TerminationReason.INTERNAL_ERROR, evidence_ref=b.digest("t")
    )
    sm.set_run_status(sm.state.runs[run.run_id], R.FAILED, termination)
    assert sm.state.unresolved_attempts == {run.run_id: 1}  # a terminal Run does not settle it
    with pytest.raises(TransitionPreconditionError, match="WP04-R03"):
        w.running_run(sm, sm.state.tasks[task.task_id])
    with pytest.raises(TransitionPreconditionError, match="unresolved"):
        sm.set_task_status(sm.state.tasks[task.task_id], TaskStatus.CANCELLED)


def test_a_settled_attempt_lets_the_run_go_on() -> None:
    sm, run, attempt = _with_open_attempt()
    sm.advance_attempt(
        attempt, A.ABORTED_BEFORE_DISPATCH, w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH)
    )
    assert sm.state.unresolved_attempts == {}
    step = sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
    assert sm.state.steps[step.step_id].status is StepStatus.PLANNED


# ---------------------------------------------------------------- an inconsistency is an error


def test_a_count_below_zero_is_an_index_error() -> None:
    sm, run, attempt = _with_open_attempt()
    broken = sm.state._replace(unresolved_attempts=smod._frozen({}))
    current = broken.attempts[attempt.attempt_id]
    transition = smod.plan_attempt_status(
        broken,
        current,
        A.ABORTED_BEFORE_DISPATCH,
        w.evidence_for(current, A.ABORTED_BEFORE_DISPATCH),
    )
    with pytest.raises(StateIndexError):
        smod.apply_transition(broken, transition)
    del run


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(lambda run: {run: 2}, id="too-high"),
        pytest.param(lambda run: {}, id="missing"),
        pytest.param(lambda run: {run: 0}, id="zero-kept"),
        pytest.param(lambda run: {run: 1, b.new_id(type(run)): 1}, id="unknown-run"),
    ],
)
def test_check_indexes_refuses_any_difference(index: Any) -> None:
    sm, run, _attempt = _with_open_attempt()
    check_indexes(sm.state)  # the real one is consistent
    broken = sm.state._replace(unresolved_attempts=smod._frozen(index(run.run_id)))
    with pytest.raises(StateIndexError):
        check_indexes(broken)


def test_a_rebuild_checks_the_index_it_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = iw.shape_c(lw.setup(tmp_path), runs=1)
    events = _events(st.journal)
    real = smod._install

    def skew(writer: Any, transition: Any) -> None:
        real(writer, transition)
        for run_id, count in dict(writer.current("unresolved_attempts")).items():
            writer.put("unresolved_attempts", run_id, count + 1)

    monkeypatch.setattr(smod, "_install", skew)
    with pytest.raises((StateIndexError, StateRebuildError)):
        rebuild_with_head(events)


def test_the_runtime_refuses_to_go_on_after_an_index_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sm, _run, attempt = _with_open_attempt()

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise StateIndexError("synthetic: the index is inconsistent")

    monkeypatch.setattr("agent_core.runtime.service.apply_payload", broken)
    with pytest.raises(StateIndexError):
        sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    monkeypatch.undo()
    from agent_core.types.errors import RuntimeReloadRequiredError

    with pytest.raises(RuntimeReloadRequiredError):
        sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
