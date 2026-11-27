"""P-7 2nd stage: the derived index of used reservation, permit and receipt IDs (tests first).

``plan_attempt_status`` refuses an ID that another Attempt already holds
(``_unused``). The rule is kept per kind and for every Attempt, whatever its
state (completed, failed, aborted, unknown): an ID once used is never used
again. The answer now comes from ``RuntimeState.used_ids`` (derived, never a
source of truth): per kind, each used ID and the Attempt that holds it. It must
equal a scan of the Attempts after every event (the identity scenarios include
unknown outcomes, stops, recovery and late reconciliation), be the same after a
Rebuild as live, and any difference is refused (``StateIndexError``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime import state_machine as smod
from agent_core.runtime.state_machine import RuntimeState, apply_event, check_indexes
from agent_core.types.errors import StateIndexError, TransitionPreconditionError
from agent_core.types.events import Event
from agent_core.types.records import AttemptState, StepStatus
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w

A = AttemptState
KINDS = ("reservation_id", "permit_id", "receipt_id")


def _scan(state: RuntimeState) -> dict[str, dict[str, str]]:
    """The reference: every non-empty ID of every Attempt, by kind."""
    found: dict[str, dict[str, str]] = {k: {} for k in KINDS}
    for attempt in state.attempts.values():
        for kind in KINDS:
            value = getattr(attempt, kind)
            if value is not None:
                assert str(value) not in found[kind], "the reference itself found a duplicate"
                found[kind][str(value)] = str(attempt.attempt_id)
    return found


def _index(state: RuntimeState) -> dict[str, dict[str, str]]:
    return {kind: {str(k): str(v) for k, v in state.used_ids(kind).items()} for kind in KINDS}


def _events(journal: Any) -> list[Event]:
    events: list[Event] = []
    while page := journal.read_tenant(b.TENANT, after_tenant_seq=len(events), limit=500):
        events.extend(page)
    return events


@pytest.mark.parametrize("name", sorted(iw.SCENARIOS))
def test_the_index_matches_a_scan_after_every_event(name: str, tmp_path: Path) -> None:
    st = iw.SCENARIOS[name](lw.setup(tmp_path))
    state = RuntimeState.empty()
    for event in _events(st.journal):
        state = apply_event(state, event)
        assert _index(state) == _scan(state)


@pytest.mark.parametrize("name", sorted(iw.SCENARIOS))
def test_the_index_after_a_rebuild_is_the_index_of_live_execution(
    name: str, tmp_path: Path
) -> None:
    st = iw.SCENARIOS[name](lw.setup(tmp_path))
    live = _index(st.sm.state)
    assert live == _scan(st.sm.state)
    assert _index(st.reopen().sm.state) == live


# ---------------------------------------------------------------- the rule keeps its meaning


def _second_attempt(sm: Any, first: Any) -> Any:
    """A new Step with a new Attempt, the first Attempt's Step ended."""
    step = sm.state.steps[first.step_id]
    sm.set_step_status(step, StepStatus.FAILED)
    run = sm.state.runs[step.run_id]
    return sm.create_attempt(w.step_in_progress(sm, run))


@pytest.mark.parametrize("ended", [A.COMPLETED, A.FAILED_DEFINITE, A.OUTCOME_UNKNOWN])
@pytest.mark.parametrize("kind", KINDS)
def test_an_id_held_by_any_attempt_is_never_used_again(kind: str, ended: AttemptState) -> None:
    sm = w.machine()
    run = w.running_run(sm)
    first = w.dispatched_attempt(sm, w.step_in_progress(sm, run))
    first = sm.advance_attempt(first, ended, w.evidence_for(first, ended))
    if ended is A.OUTCOME_UNKNOWN:
        # An unknown outcome blocks a new Step (WP04-R03): reuse is tried on the
        # same Run's other path, the first Attempt's own Step is still open.
        with pytest.raises(TransitionPreconditionError):
            _second_attempt(sm, first)
        return
    second = _second_attempt(sm, first)
    path = w.PATH_WITHOUT_APPROVAL
    stop_at = {"reservation_id": A.CONTENT_FIXED, "permit_id": A.PERMIT_ISSUED}.get(
        kind, A.PERMIT_CONSUMED
    )
    reuse = {kind: getattr(first, kind)}
    for target in path:
        if target is stop_at:
            with pytest.raises(TransitionPreconditionError, match="already used"):
                sm.advance_attempt(second, target, w.evidence_for(second, target, **reuse))
            return
        second = sm.advance_attempt(second, target, w.evidence_for(second, target))
    pytest.fail("the reuse point was not reached")


def test_an_aborted_attempts_reservation_is_never_used_again() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    first = w.drive(sm, sm.create_attempt(step), (A.VALIDATED, A.CONTENT_FIXED))
    first = sm.advance_attempt(
        first, A.ABORTED_BEFORE_DISPATCH, w.evidence_for(first, A.ABORTED_BEFORE_DISPATCH)
    )
    second = w.drive(sm, sm.create_attempt(sm.state.steps[step.step_id]), (A.VALIDATED,))
    with pytest.raises(TransitionPreconditionError, match="already used"):
        sm.advance_attempt(
            second,
            A.CONTENT_FIXED,
            w.evidence_for(second, A.CONTENT_FIXED, reservation_id=first.reservation_id),
        )


# ---------------------------------------------------------------- an inconsistency is an error


def _with_ids() -> Any:
    sm = w.machine()
    w.dispatched_attempt(sm, w.step_in_progress(sm, w.running_run(sm)))
    return sm.state


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("damage", ["missing", "extra", "other-attempt"])
def test_check_indexes_refuses_any_difference(kind: str, damage: str) -> None:
    state = _with_ids()
    check_indexes(state)
    table = dict(state.used_ids(kind))
    ((value, holder),) = table.items()
    if damage == "missing":
        table.pop(value)
    elif damage == "extra":
        table[type(value)(str(b.new_id(type(value))))] = holder
    else:
        table[value] = type(holder)(str(b.new_id(type(holder))))
    broken = state._replace(**{smod.USED_ID_TABLES[kind]: smod._frozen(table)})
    with pytest.raises(StateIndexError):
        check_indexes(broken)


def test_installing_an_id_twice_is_an_index_error() -> None:
    state = _with_ids()
    attempt = next(iter(state.attempts.values()))
    writer = smod._CopyOnWrite(state)
    with pytest.raises(StateIndexError):
        smod._use_id(writer, "permit_id", attempt.permit_id, b.new_id(type(attempt.attempt_id)))
