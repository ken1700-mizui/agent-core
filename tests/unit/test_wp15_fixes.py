"""WP-15 limited fixes R01-R05 (regression tests, written before the fixes).

R01 restart and loop limits; R02 a stop while the Run waits; R03 the
verification bound to the final decision; R04 every limit before every
operation (monotonic time, ledger counts); R05 the state after a failure.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.fake_model import FakeFault, FakeFaultKind
from agent_core.runtime import agent_loop as loop_module
from agent_core.runtime.agent_loop import AgentLoop, LoopLimits
from agent_core.runtime.verification import FileCriterion
from agent_core.safety.workspace import file_digest
from agent_core.types.errors import ContextVerificationError
from agent_core.types.model import ModelRequest, ModelResponse
from agent_core.types.records import AttemptState, RunStatus, StepKind, StepStatus
from agent_core.types.safety import StopScopeType
from agent_core.types.transitions import RunTransition
from support import ledger_world as lw
from support import loop_world as lp
from support import model_world as m

A = AttemptState
R = RunStatus
DRAFT = "synthetic report draft\n"
OTHER = "workspace/out/other.txt"
OTHER_GOOD = "synthetic other v1\n"


# ---------------------------------------------------------------- helpers


def _limits(**overrides: int) -> LoopLimits:
    values = {
        "max_repairs": 3,
        "max_model_calls": 10,
        "max_tool_calls": 10,
        "max_iterations": 20,
        "max_elapsed_ms": 600_000,
    }
    values.update(overrides)
    return LoopLimits(**values)


def _drive(wd: lp.World, *, rounds: int = 10) -> Any:
    outcome = wd.loop.run(wd.run.run_id)
    for _ in range(rounds):
        if outcome.status is not R.WAITING_APPROVAL or outcome.pending_action_hash is None:
            return outcome
        outcome = lp.approve_pending(wd, outcome)
    raise AssertionError("the loop did not settle")


def _statuses(wd: lp.World) -> list[RunStatus]:
    found: list[RunStatus] = []
    for event in wd.st.journal.read_run(wd.run.run_id, after_run_seq=0, limit=1_000):
        if event.payload is not None:
            found.extend(
                t.after.status for t in event.payload.transitions if isinstance(t, RunTransition)
            )
    return found[found.index(R.RUNNING) + 1 :]


def _steps(wd: lp.World) -> list[Any]:
    state = wd.st.sm.state
    return [state.steps[s] for s in state.run_steps[wd.run.run_id]][1:]


def _attempts(wd: lp.World, kind: StepKind | None = None) -> list[Any]:
    state = wd.st.sm.state
    return [
        state.attempts[a]
        for s in _steps(wd)
        if kind is None or s.kind is kind
        for a in state.step_attempts.get(s.step_id, ())
    ]


def _no_hold(wd: lp.World) -> None:
    for attempt in _attempts(wd):
        if attempt.reservation_id is not None:
            view = wd.st.journal.reservation(lw.rsv(attempt))
            assert view is not None
            assert view.hold_micro == 0


def _stop(wd: lp.World, kind: StopScopeType = StopScopeType.RUN) -> None:
    scope = {
        StopScopeType.RUN: str(wd.run.run_id),
        StopScopeType.TENANT: str(wd.config.tenant_id),
        StopScopeType.GLOBAL: "global",
    }[kind]
    wd.st.owner(lw.stop_op(kind, scope))


def _settled_steps(wd: lp.World) -> None:
    open_steps = [s for s in _steps(wd) if s.status in (StepStatus.IN_PROGRESS, StepStatus.PLANNED)]
    assert open_steps == []


def _with_deps(wd: lp.World, **changes: Any) -> None:
    deps = wd.loop._deps
    wd.loop._deps = deps.__class__(**{**deps.__dict__, **changes})


class _Files:
    """A file port that runs ``hook(n)`` after the n-th prepare (1-based)."""

    def __init__(self, real: Any, hook: Any) -> None:
        self._real = real
        self._hook = hook
        self.prepares = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def prepare(self, descriptor: Any, policy: Any) -> Any:
        prepared = self._real.prepare(descriptor, policy)
        self.prepares += 1
        self._hook(self.prepares)
        return prepared


def _files_hook(wd_box: list[Any], hook: Any) -> Any:
    def wrap(real: Any) -> Any:
        files = _Files(real, lambda n: hook(wd_box[0], n))
        wd_box.append(files)
        return files

    return wrap


def _two_criteria() -> tuple[Any, ...]:
    other = FileCriterion(
        kind="file_content", path=OTHER, expected_digest=file_digest(OTHER_GOOD.encode())
    )
    return (lp.CRITERION, other)


# ================================================================ R01 restart and limits


def test_r01_a_new_loop_does_not_take_over_a_running_run(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done(), lp.done()])
    first = wd.loop.run(wd.run.run_id)
    lp.approve_pending(wd, first)
    fresh = AgentLoop(wd.loop._deps, wd.config)
    # The Run is over here; a Run in progress elsewhere is the next case.
    assert fresh.run(wd.run.run_id).status is R.COMPLETED


def test_r01_a_running_run_without_its_transcript_is_refused(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    # Earlier loop work: a Step with an Attempt that ended, then the process ended.
    step = lw.step_in(wd.st, wd.st.sm.state.runs[wd.run.run_id])
    attempt = lw.fixed_attempt(wd.st, step, lw.scopes_of(wd.st.sm, step))
    lw.abort(wd.st, attempt, release=False)
    wd.st.sm.set_step_status(wd.st.sm.state.steps[step.step_id], StepStatus.FAILED)
    outcome = wd.loop.run(wd.run.run_id)
    assert (outcome.status, outcome.reason) == (R.RUNNING, "SESSION_UNAVAILABLE")
    assert wd.model.requests == []
    assert len(_steps(wd)) == 1  # nothing new was started
    # Asking again neither resets the counters nor starts anything.
    assert wd.loop.run(wd.run.run_id).reason == "SESSION_UNAVAILABLE"
    assert wd.model.requests == []


def test_r01_a_restarted_loop_keeps_the_ledger_counts(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    first = wd.loop.run(wd.run.run_id)
    assert (first.model_calls, first.ledger_model_calls) == (1, 1)
    fresh = AgentLoop(wd.loop._deps, wd.config)
    outcome = fresh.run(wd.run.run_id)
    # This process made no call; the ledger keeps the Run's real count.
    assert (outcome.model_calls, outcome.ledger_model_calls) == (0, 1)
    assert outcome.reason == "SESSION_UNAVAILABLE"
    assert outcome.status is R.WAITING_APPROVAL


def test_r01_a_reentrant_call_is_refused(tmp_path: Path) -> None:
    inner: list[Any] = []

    class Reentrant:
        def __init__(self, real: lp.ScriptedModel) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            if not inner:
                inner.append(holder[0].loop.run(holder[0].run.run_id))
            return self.real.complete(request)

    holder: list[lp.World] = []
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    holder.append(wd)
    _with_deps(wd, model=Reentrant(wd.model))
    outcome = _drive(wd)
    assert outcome.status is R.COMPLETED
    assert inner[0].reason == "RUN_BUSY"
    assert len(wd.model.requests) == 2


def test_r01_a_started_attempt_without_a_session_is_never_continued(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.done()])
    run = wd.st.sm.state.runs[wd.run.run_id]
    step = lw.step_in(wd.st, run)
    started = lw.dispatched(wd.st, step)
    assert started.state is A.DISPATCH_STARTED
    outcome = wd.loop.run(wd.run.run_id)
    assert outcome.reason == "UNRESOLVED_ATTEMPT"
    assert outcome.status is R.RUNNING  # left for reconciliation (WP-12), nothing run
    assert wd.model.requests == []
    assert wd.st.sm.state.attempts[started.attempt_id].state is A.DISPATCH_STARTED


# ================================================================ R02 a stop while waiting


def test_r02_a_stop_is_seen_while_waiting_without_an_approval(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    assert wd.loop.run(wd.run.run_id).status is R.WAITING_APPROVAL
    _stop(wd)
    outcome = wd.loop.run(wd.run.run_id)  # no approval id
    assert (outcome.status, outcome.reason) == (R.CANCELLED, "STOPPED")
    assert [a.state for a in _attempts(wd, StepKind.TOOL_CALL)] == [A.ABORTED_BEFORE_DISPATCH]
    assert not (wd.tree.root / lp.TARGET).exists()
    _no_hold(wd)
    _settled_steps(wd)


@pytest.mark.parametrize("kind", [StopScopeType.TENANT, StopScopeType.GLOBAL])
def test_r02_a_wider_stop_also_ends_a_waiting_run(tmp_path: Path, kind: StopScopeType) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    wd.loop.run(wd.run.run_id)
    _stop(wd, kind)
    assert wd.loop.run(wd.run.run_id).status is R.CANCELLED


def test_r02_a_stop_ends_a_context_budget_wait(tmp_path: Path) -> None:
    small = lp.profile().model_copy(
        update={"model": m.profile(context_window=2_000, max_output=200)}
    )
    goal = "Create the synthetic report. " + "synthetic requirement detail. " * 600
    wd = lp.world(tmp_path, [lp.done()], goal=goal, prof=small)
    assert wd.loop.run(wd.run.run_id).reason == "CONTEXT_BUDGET_EXCEEDED"
    _stop(wd)
    outcome = wd.loop.run(wd.run.run_id)
    assert (outcome.status, outcome.reason) == (R.CANCELLED, "STOPPED")


def test_r02_a_stop_ends_a_budget_pause(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.done()], run=1)
    assert wd.loop.run(wd.run.run_id).status is R.PAUSED_BUDGET
    _stop(wd)
    outcome = wd.loop.run(wd.run.run_id)
    assert (outcome.status, outcome.reason) == (R.CANCELLED, "STOPPED")
    assert _statuses(wd)[-2:] == [R.CANCELLING, R.CANCELLED]


def test_r02_a_new_loop_can_still_stop_a_waiting_run(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    wd.loop.run(wd.run.run_id)
    _stop(wd)
    fresh = AgentLoop(wd.loop._deps, wd.config)
    assert fresh.run(wd.run.run_id).status is R.CANCELLED
    _no_hold(wd)


class _NonFinal:
    def __init__(self, real: lp.ScriptedModel) -> None:
        self.real = real

    def complete(self, request: ModelRequest) -> ModelResponse:
        return self.real.complete(request).model_copy(update={"final_usage": False})


def test_r02_a_stop_with_an_unsettled_cost_is_not_a_normal_cancel(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    _with_deps(wd, model=_NonFinal(wd.model))
    assert wd.loop.run(wd.run.run_id).reason == "UNSETTLED_COSTS"
    _stop(wd)
    outcome = wd.loop.run(wd.run.run_id)
    assert outcome.status is R.CANCELLING
    assert outcome.reason == "UNSETTLED_COSTS"
    assert R.CANCELLED not in _statuses(wd)


def test_r02_a_stop_never_cancels_an_unknown_outcome(tmp_path: Path) -> None:
    fault = FakeFault(kind="fault", fault=FakeFaultKind.OUTCOME_UNKNOWN, elapsed_ms=5)
    wd = lp.world(tmp_path, [fault])
    assert wd.loop.run(wd.run.run_id).status is R.RECONCILIATION_REQUIRED
    _stop(wd)
    assert wd.loop.run(wd.run.run_id).status is R.RECONCILIATION_REQUIRED
    assert R.CANCELLED not in _statuses(wd)


# ================================================================ R03 verification


def test_r03_a_change_during_the_verification_refuses_completion(tmp_path: Path) -> None:
    box: list[Any] = []

    def hook(wd: lp.World, n: int) -> None:
        if n == 3:  # the write, the first read; now the confirmation read
            (wd.tree.root / lp.TARGET).write_text(DRAFT)

    wd = lp.world(tmp_path, [lp.write(), lp.done()], wrap_files=_files_hook(box, hook))
    box.insert(0, wd)
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.FAILED, "ARTIFACT_CHANGED")
    assert R.COMPLETED not in _statuses(wd)
    _settled_steps(wd)


def test_r03_a_change_to_another_file_while_reading_is_caught(tmp_path: Path) -> None:
    box: list[Any] = []

    def hook(wd: lp.World, n: int) -> None:
        if n == 4:  # the second file's first read: the first file changes meanwhile
            (wd.tree.root / lp.TARGET).write_text(DRAFT)

    script = [
        lp.write(),
        lp.write(OTHER_GOOD, path=OTHER, use_id="t2"),
        lp.done(),
    ]
    wd = lp.world(tmp_path, script, specs=_two_criteria(), wrap_files=_files_hook(box, hook))
    box.insert(0, wd)
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "ARTIFACT_CHANGED"


def test_r03_two_files_complete_when_nothing_changes(tmp_path: Path) -> None:
    script = [lp.write(), lp.write(OTHER_GOOD, path=OTHER, use_id="t2"), lp.done()]
    wd = lp.world(tmp_path, script, specs=_two_criteria())
    outcome = _drive(wd)
    assert outcome.status is R.COMPLETED
    # Each file read twice (the check, then the confirmation before the decision).
    assert outcome.verification_reads == 4


def test_r03_a_committed_write_must_match_the_verified_file(tmp_path: Path) -> None:
    box: list[Any] = []

    def hook(wd: lp.World, n: int) -> None:
        if n == 2:  # after the write committed DRAFT, someone else puts GOOD there
            (wd.tree.root / lp.TARGET).write_text(lp.GOOD)

    wd = lp.world(tmp_path, [lp.write(DRAFT), lp.done()], wrap_files=_files_hook(box, hook))
    box.insert(0, wd)
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.FAILED, "APPLY_PLAN_MISMATCH")
    assert R.COMPLETED not in _statuses(wd)


def test_r03_a_stop_before_the_decision_refuses_completion(tmp_path: Path) -> None:
    box: list[Any] = []

    def hook(wd: lp.World, n: int) -> None:
        if n == 3:
            _stop(wd)

    wd = lp.world(tmp_path, [lp.write(), lp.done()], wrap_files=_files_hook(box, hook))
    box.insert(0, wd)
    outcome = _drive(wd)
    assert outcome.status is R.CANCELLED
    assert R.COMPLETED not in _statuses(wd)
    _no_hold(wd)


class _GrantGone:
    """The Run grant disappears once ``gone`` is set (as if revoked)."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.gone = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def run_grant(self, run_id: str) -> Any:
        return None if self.gone else self._real.run_grant(run_id)


def test_r03_the_grant_is_checked_again_at_the_decision(tmp_path: Path) -> None:
    box: list[Any] = []
    ledgers: list[_GrantGone] = []

    def hook(_wd: lp.World, n: int) -> None:
        if n == 3:
            ledgers[0].gone = True

    def wrap_ledger(real: Any) -> Any:
        ledgers.append(_GrantGone(real))
        return ledgers[0]

    wd = lp.world(
        tmp_path,
        [lp.write(), lp.done()],
        wrap_files=_files_hook(box, hook),
        wrap_ledger=wrap_ledger,
    )
    box.insert(0, wd)
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.ABORTED_SAFETY, "GRANT_MISSING")


# ================================================================ R04 limits


def test_r04_the_limits_are_checked_before_resuming_an_approved_action(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], limits=_limits(max_elapsed_ms=1_000))
    first = wd.loop.run(wd.run.run_id)
    wd.mono.now += 2_000
    outcome = lp.approve_pending(wd, first)
    assert (outcome.status, outcome.reason) == (R.FAILED, "LIMIT_EXCEEDED")
    assert not (wd.tree.root / lp.TARGET).exists()  # the approved write never ran
    _no_hold(wd)
    _settled_steps(wd)


def test_r04_a_wall_clock_going_back_does_not_extend_the_time(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], limits=_limits(max_elapsed_ms=1_000))
    first = wd.loop.run(wd.run.run_id)
    wd.mono.now += 2_000
    wd.st.clock.now -= 100_000  # back in time: no extra time for the loop
    wd.st.clock.now += 100_000
    outcome = lp.approve_pending(wd, first)
    assert outcome.reason == "LIMIT_EXCEEDED"


def test_r04_a_monotonic_clock_going_back_is_ignored(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], limits=_limits(max_elapsed_ms=1_000))
    first = wd.loop.run(wd.run.run_id)
    wd.mono.now += 2_000
    assert wd.loop.run(wd.run.run_id).status is R.WAITING_APPROVAL  # sees 2000
    wd.mono.now -= 2_000  # a broken source: the highest value seen is kept
    outcome = lp.approve_pending(wd, first)
    assert outcome.reason == "LIMIT_EXCEEDED"


def test_r04_verification_reads_are_ledger_tool_calls(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    outcome = _drive(wd)
    assert outcome.status is R.COMPLETED
    assert outcome.tool_calls == 1  # what the model asked for
    assert outcome.verification_reads == 2  # the check and the confirmation
    assert outcome.ledger_tool_calls == 3  # every tool operation the ledger counted
    assert outcome.ledger_model_calls == 2


def test_r04_the_ledger_tool_cap_also_limits_verification(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], tool_calls=2)
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.status is R.PAUSED_BUDGET
    assert outcome.ledger_tool_calls == 2
    _no_hold(wd)


def test_r04_nothing_more_runs_after_an_unsettled_cost(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    _with_deps(wd, model=_NonFinal(wd.model))
    outcome = wd.loop.run(wd.run.run_id)
    assert (outcome.status, outcome.reason) == (R.PAUSED_BUDGET, "UNSETTLED_COSTS")
    assert len(wd.model.requests) == 1
    assert StepKind.TOOL_CALL not in [s.kind for s in _steps(wd)]


# ================================================================ R05 failures


def test_r05_a_context_verification_error_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wd = lp.world(tmp_path, [lp.done()])

    def broken(*_a: Any, **_k: Any) -> None:
        raise ContextVerificationError("REBUILD_MISMATCH")

    monkeypatch.setattr(loop_module, "verify_build", broken)
    outcome = wd.loop.run(wd.run.run_id)
    assert (outcome.status, outcome.reason) == (R.ABORTED_SAFETY, "CONTEXT_INVALID")
    assert outcome.error == "ContextVerificationError"
    assert wd.model.requests == []
    assert [a.state for a in _attempts(wd)] == [A.ABORTED_BEFORE_DISPATCH]
    _settled_steps(wd)


def test_r05_a_receipt_refused_after_the_permit_was_consumed(tmp_path: Path) -> None:
    box: list[Any] = []

    def hook(wd: lp.World, n: int) -> None:
        if n == 1:  # the write's permit is consumed; a stop arrives before the claim
            _stop(wd)

    wd = lp.world(tmp_path, [lp.write(), lp.done()], wrap_files=_files_hook(box, hook))
    box.insert(0, wd)
    outcome = _drive(wd)
    assert outcome.status is R.CANCELLED
    assert not (wd.tree.root / lp.TARGET).exists()
    assert [a.state for a in _attempts(wd, StepKind.TOOL_CALL)] == [A.ABORTED_BEFORE_DISPATCH]
    _no_hold(wd)
    _settled_steps(wd)


class _BrokenPrepare:
    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def prepare(self, descriptor: Any, policy: Any) -> Any:
        raise RuntimeError("synthetic broker fault")


def test_r05_an_unexpected_broker_fault_before_the_start_is_not_run(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], wrap_files=_BrokenPrepare)
    outcome = _drive(wd)
    assert (outcome.status, outcome.reason) == (R.ABORTED_SAFETY, "NOT_DISPATCHED")
    assert outcome.error == "RuntimeError"
    assert [a.state for a in _attempts(wd, StepKind.TOOL_CALL)] == [A.ABORTED_BEFORE_DISPATCH]
    _no_hold(wd)
    _settled_steps(wd)
    # The loop does not go on after it.
    assert wd.loop.run(wd.run.run_id).status is R.ABORTED_SAFETY
    assert len(wd.model.requests) == 1


def test_r05_a_cleanup_failure_does_not_hide_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()], wrap_files=_BrokenPrepare)
    gate = wd.loop._deps.gate

    def failing(*_a: Any, **_k: Any) -> Any:
        raise sqlite3.OperationalError("synthetic journal failure")

    monkeypatch.setattr(gate, "abort_not_dispatched", failing)
    first = wd.loop.run(wd.run.run_id)
    with pytest.raises(RuntimeError) as caught:
        lp.approve_pending(wd, first)
    assert any("OperationalError" in note for note in caught.value.__notes__)
    # The loop refuses to go on from a state it could not finish recording.
    outcome = wd.loop.run(wd.run.run_id)
    assert outcome.reason == "LOOP_BROKEN"
    assert len(wd.model.requests) == 1


def test_r05_a_journal_failure_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wd = lp.world(tmp_path, [lp.done(), lp.done()])
    journal = wd.st.journal
    calls = {"n": 0}
    real = journal.append_with_ledger

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 4:
            raise sqlite3.OperationalError("synthetic disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(journal, "append_with_ledger", flaky)
    with pytest.raises(sqlite3.OperationalError):
        wd.loop.run(wd.run.run_id)
    monkeypatch.setattr(journal, "append_with_ledger", real)
    requests = len(wd.model.requests)
    assert wd.loop.run(wd.run.run_id).reason == "LOOP_BROKEN"
    assert len(wd.model.requests) == requests
