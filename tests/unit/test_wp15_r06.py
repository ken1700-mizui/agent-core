"""WP15-R06: the final verification is bound to the completion (tests first).

- Cooperating writers (every write goes through this Runtime's one gate) are
  held off from the confirmation reads until the completion is recorded.
- The completion transaction itself (the journal append with the ledger
  mirror) refuses a COMPLETED Run unless its evidence names verification reads
  that really ran for this Run under the current policy, grant and access
  epoch, with every cost settled and no stop: a made-up VerificationEvidence is
  not enough.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_core.types.errors import CompletionRefusedError, PermitRejectedError
from agent_core.types.records import RunStatus
from agent_core.types.safety import StopScopeType
from agent_core.types.transitions import (
    ApplyPlanStatus,
    CriterionResult,
    RunTransition,
    VerificationEvidence,
)
from support import ledger_world as lw
from support import loop_world as lp

R = RunStatus


def _drive(wd: lp.World) -> Any:
    outcome = wd.loop.run(wd.run.run_id)
    for _ in range(10):
        if outcome.status is not R.WAITING_APPROVAL or outcome.pending_action_hash is None:
            return outcome
        outcome = lp.approve_pending(wd, outcome)
    raise AssertionError("not settled")


def _completion(wd: lp.World) -> VerificationEvidence:
    for event in wd.st.journal.read_run(wd.run.run_id, after_run_seq=0, limit=1_000):
        if event.payload is None:
            continue
        for t in event.payload.transitions:
            if isinstance(t, RunTransition) and t.after.status is R.COMPLETED:
                assert isinstance(t.evidence, VerificationEvidence)
                return t.evidence
    raise AssertionError("no completion")


def _hook_at(wd: lp.World, status: RunStatus, action: Any) -> None:
    """Run ``action`` right after the Run enters ``status`` (before the next step)."""
    sm = wd.st.sm
    real = sm.set_run_status

    def wrapped(run: Any, target: RunStatus, evidence: Any = None) -> Any:
        result = real(run, target, evidence)
        if target is status:
            action()
        return result

    sm.set_run_status = wrapped  # type: ignore[method-assign]


def test_the_completion_names_the_confirmation_reads(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    assert _drive(wd).status is R.COMPLETED
    evidence = _completion(wd)
    state = wd.st.sm.state
    reads = [state.attempts[a] for a in evidence.verified_reads]
    assert len(reads) == 1
    assert all(a.state.value == "COMPLETED" for a in reads)
    assert state.steps[reads[0].step_id].kind.value == "VERIFICATION"


def test_writes_are_held_off_while_the_completion_is_decided(tmp_path: Path) -> None:
    seen: list[bool] = []
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    gate = wd.loop._deps.gate
    _hook_at(wd, R.VERIFYING, lambda: seen.append(gate.writes_held))
    assert _drive(wd).status is R.COMPLETED
    assert seen == [True]
    assert gate.writes_held is False  # released after the completion


def test_a_cooperating_write_is_refused_during_the_hold(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    gate = lw.gate(wd.st)
    with gate.verification_hold():
        assert gate.writes_held
        with pytest.raises(PermitRejectedError):
            gate.refuse_write_while_held()
        with pytest.raises(PermitRejectedError), gate.verification_hold():
            pass
    released: bool = gate.writes_held
    assert released is False
    gate.refuse_write_while_held()  # no hold: nothing refused


def test_a_made_up_evidence_cannot_complete_a_run(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    first = wd.loop.run(wd.run.run_id)
    assert first.status is R.WAITING_APPROVAL
    # Without any verification read of this Run, a passing evidence is refused by
    # the completion transaction, even from the runtime directly.
    sm = wd.st.sm
    run = sm.state.runs[wd.run.run_id]
    forged = VerificationEvidence(
        kind="verification",
        criteria=tuple(
            CriterionResult(criterion_ref=r, passed=True) for r in wd.task.done_criteria
        ),
        artifact_hash=lp.CRITERION.expected_digest,
        expected_artifact_hash=lp.CRITERION.expected_digest,
        apply_plan=ApplyPlanStatus.NOT_REQUIRED,
        passed=True,
        verified_reads=(),
    )
    with pytest.raises(Exception):  # noqa: B017,PT011 (refused at planning or in the journal)
        sm.set_run_status(run, R.COMPLETED, forged)
    assert sm.state.runs[wd.run.run_id].status is not R.COMPLETED


def test_a_read_that_is_not_a_verification_read_is_refused(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    loop = wd.loop
    real = loop._verification_evidence

    def swap(*args: Any, **kwargs: Any) -> VerificationEvidence:
        evidence = real(*args, **kwargs)
        state = wd.st.sm.state
        writes = [
            a.attempt_id
            for a in state.attempts.values()
            if a.action_hash and state.steps[a.step_id].kind.value == "TOOL_CALL"
        ]
        return evidence.model_copy(update={"verified_reads": tuple(writes[:1])})

    loop._verification_evidence = swap  # type: ignore[method-assign]
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.ABORTED_SAFETY, "COMPLETION_REFUSED")


def test_a_stop_between_verifying_and_completed_is_enforced_by_the_journal(
    tmp_path: Path,
) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    _hook_at(
        wd,
        R.VERIFYING,
        lambda: wd.st.owner(lw.stop_op(StopScopeType.TENANT, str(wd.config.tenant_id))),
    )
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "COMPLETION_REFUSED"


def test_an_expired_grant_at_the_decision_is_refused(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])

    def expire() -> None:
        wd.st.clock.now += 4_000_000  # past the grant's expiry (one hour)

    _hook_at(wd, R.VERIFYING, expire)
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "COMPLETION_REFUSED"


def test_an_unsettled_cost_is_refused_by_the_journal_too(tmp_path: Path) -> None:
    class NonFinalLast:
        def __init__(self, real: Any) -> None:
            self.real = real
            self.calls = 0

        def complete(self, request: Any) -> Any:
            self.calls += 1
            response = self.real.complete(request)
            return response.model_copy(update={"final_usage": self.calls < 2})

    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    deps = wd.loop._deps
    wd.loop._deps = deps.__class__(**{**deps.__dict__, "model": NonFinalLast(wd.model)})
    wd.loop._unsettled = lambda run_id: False  # type: ignore[method-assign]
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "COMPLETION_REFUSED"


def test_the_refusal_names_a_fixed_reason() -> None:
    error = CompletionRefusedError("STOPPED")
    assert "STOPPED" in str(error)


def test_a_verified_run_lets_its_task_complete(tmp_path: Path) -> None:
    from agent_core.types.records import TaskStatus

    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    assert _drive(wd).status is R.COMPLETED
    task = wd.st.sm.state.tasks[wd.task.task_id]
    assert wd.st.sm.set_task_status(task, TaskStatus.COMPLETED).status is TaskStatus.COMPLETED
