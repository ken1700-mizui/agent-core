"""WP-15 stage 2: the agent loop and its verification (single runtime, single
agent, synthetic data, the Fake model, a simple task).

Every model call and every tool call is its own Step and Attempt through the
authorization path (decision, approval, reservation, intent, permit, receipt,
execution gate). File operations go through ``execute_file`` only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.runtime.agent_loop import LoopLimits
from agent_core.runtime.verification import CriteriaCatalog, FileCriterion
from agent_core.safety.workspace import file_digest
from agent_core.types.cost import BudgetScopeType, ReservationState
from agent_core.types.errors import BudgetCapMissingError
from agent_core.types.ledger import SetStepCapDefault
from agent_core.types.records import AttemptState, RunStatus, StepKind, StepStatus
from agent_core.types.transitions import RunTransition, VerificationEvidence
from support import builders as b
from support import ledger_world as lw
from support import loop_world as lp

A = AttemptState


def _transitions(wd: lp.World) -> list[Any]:
    found: list[Any] = []
    for event in wd.st.journal.read_run(wd.run.run_id, after_run_seq=0, limit=1_000):
        if event.payload is not None:
            found.extend(event.payload.transitions)
    return found


def _attempts(wd: lp.World) -> list[Any]:
    state = wd.st.sm.state
    return [
        state.attempts[a]
        for s in state.run_steps[wd.run.run_id]
        for a in state.step_attempts.get(s, ())
    ]


def _kinds(wd: lp.World) -> list[StepKind]:
    state = wd.st.sm.state
    return [state.steps[s].kind for s in state.run_steps[wd.run.run_id]][1:]  # after the setup step


# ================================================================ the normal path


def test_a_simple_task_runs_to_completion_through_the_safe_paths(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    first = wd.loop.run(wd.run.run_id)
    # write_file is IRREVERSIBLE: the loop waits for the owner, and gives control back.
    assert first.status is RunStatus.WAITING_APPROVAL
    assert first.pending_action_hash is not None
    assert not (wd.tree.root / lp.TARGET).exists()
    assert wd.loop.run(wd.run.run_id).status is RunStatus.WAITING_APPROVAL  # no approval yet
    done = lp.approve_pending(wd, first)
    assert done.status is RunStatus.COMPLETED
    assert (wd.tree.root / lp.TARGET).read_text() == lp.GOOD
    # One model call per Attempt, one tool call per Attempt, then the verification read.
    assert _kinds(wd) == [
        StepKind.MODEL_CALL,
        StepKind.TOOL_CALL,
        StepKind.MODEL_CALL,
        StepKind.VERIFICATION,
        StepKind.VERIFICATION,  # the confirmation read (WP15-R03)
    ]
    attempts = _attempts(wd)[1:]
    assert all(a.state is A.COMPLETED for a in attempts)
    assert len({a.permit_id for a in attempts}) == len(attempts)
    statuses = [t.after.status for t in _transitions(wd) if isinstance(t, RunTransition)]
    assert RunStatus.VERIFYING in statuses
    assert statuses[-1] is RunStatus.COMPLETED
    verification = [
        t.evidence
        for t in _transitions(wd)
        if isinstance(t, RunTransition) and isinstance(t.evidence, VerificationEvidence)
    ][-1]
    assert verification.passed
    assert [c.criterion_ref for c in verification.criteria] == [lp.CRITERION.ref()]


def test_the_model_sees_its_tool_result_as_data_and_the_task_as_protected(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    lp.approve_pending(wd, wd.loop.run(wd.run.run_id))
    second = wd.model.requests[1]
    text = "\n".join(b.text for m in second.messages for b in m.content if hasattr(b, "text"))
    assert "<<<DATA section=HISTORY tier=D1 " in text  # the write's result
    assert lp.GOAL in text  # the goal, as a ProtectedFact


def test_every_model_request_is_a_verified_context(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    lp.approve_pending(wd, wd.loop.run(wd.run.run_id))
    for request in wd.model.requests:
        decisions = [
            wd.st.journal.recorded_descriptor(a.attempt_id)
            for a in _attempts(wd)
            if a.attempt_id == request.attempt_id
        ]
        assert decisions
        assert decisions[0] is not None
        assert decisions[0].request_hash == request.request_hash


def test_all_reservations_are_settled_at_completion(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    lp.approve_pending(wd, wd.loop.run(wd.run.run_id))
    for attempt in _attempts(wd)[1:]:
        view = wd.st.journal.reservation(lw.rsv(attempt))
        assert view is not None
        assert view.hold_micro == 0
        assert view.state is not ReservationState.UNKNOWN
    usage = wd.st.journal.scope_usage(BudgetScopeType.RUN, str(wd.run.run_id))
    assert usage.spent_micro > 0  # the two model calls are billed


# ================================================================ planning: simple only


def test_a_complex_task_is_refused_without_a_model_call(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.done()], complexity="complex")
    outcome = wd.loop.run(wd.run.run_id)
    assert outcome.status is RunStatus.FAILED
    assert outcome.reason == "PLANNING_UNSUPPORTED"
    assert wd.model.requests == []


def test_a_task_without_a_profile_is_refused(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.done()])
    wd.loop._config = wd.config.__class__(**{**wd.config.__dict__, "tasks": ()})
    assert wd.loop.run(wd.run.run_id).reason == "PLANNING_UNSUPPORTED"


# ================================================================ owner's step cap default


def test_a_new_step_needs_an_owner_cap(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    st.sm.set_step_status(step, StepStatus.CANCELLED)
    other = lw.step_in(st, st.sm.state.runs[step.run_id])
    attempt = lw.fixed_attempt(st, other, lw.scopes_of(st.sm, other))
    with pytest.raises(BudgetCapMissingError):
        lw.reserve(st, attempt)
    st.owner(SetStepCapDefault(op="set_step_cap_default", run_id=other.run_id, cap=lw.xtu(10_000)))
    assert lw.reserve(st, st.sm.state.attempts[attempt.attempt_id]).state is A.BUDGET_RESERVED
    st.journal.close()


# ================================================================ criteria


def test_a_criterion_is_bound_to_its_digest() -> None:
    spec = lp.CRITERION
    assert spec.ref() == b.digest(spec.text())
    catalog = CriteriaCatalog(criteria=(spec,))
    assert catalog.spec_for(spec.ref()) == spec
    assert catalog.spec_for(b.digest("other")) is None
    with pytest.raises(ValidationError):
        FileCriterion(kind="file_content", path="/abs", expected_digest=file_digest(b"x"))


def test_the_limits_are_explicit() -> None:
    with pytest.raises(ValidationError):
        LoopLimits(
            max_repairs=-1, max_model_calls=1, max_tool_calls=1, max_iterations=1, max_elapsed_ms=1
        )
