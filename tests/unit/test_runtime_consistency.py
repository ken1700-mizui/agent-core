"""WP04-R01..R04: stale runtimes, rebuild integrity, unresolved effects, cancel intent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.runtime import service as service_module
from agent_core.runtime.state_machine import rebuild
from agent_core.types.errors import (
    JournalWriteOutcomeUnknownError,
    RuntimeReloadRequiredError,
    StaleStateError,
    StateDivergenceError,
    StateRebuildError,
    TransitionPreconditionError,
)
from agent_core.types.events import Event
from agent_core.types.ids import RunGrantId
from agent_core.types.records import AttemptState, RunStatus, StepKind, StepStatus, TaskStatus
from agent_core.types.serialization import dump_json
from agent_core.types.transitions import (
    ReconciledOutcome,
    ReconciliationEvidence,
    ReconciliationResolution,
    ReconciliationTrigger,
    ReconciliationTriggerReason,
    ResolutionDecision,
    Termination,
    TerminationReason,
)
from support import builders as b
from support import runtime_world as w
from support.fakes import InMemoryJournal

A, R, S, T = AttemptState, RunStatus, StepStatus, TaskStatus


def _events(journal: Any) -> list[Event]:
    return list(journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1000))


def _termination(reason: TerminationReason = TerminationReason.INTERNAL_ERROR) -> Termination:
    return Termination(kind="termination", reason=reason, evidence_ref=b.digest("why"))


def _trigger() -> ReconciliationTrigger:
    return ReconciliationTrigger(
        kind="reconciliation_trigger",
        reason=ReconciliationTriggerReason.OUTCOME_UNKNOWN,
        evidence_ref=b.digest("trigger"),
    )


def _resolution(decision: ResolutionDecision) -> ReconciliationResolution:
    return ReconciliationResolution(
        kind="reconciliation_resolution", decision=decision, evidence_ref=b.digest("owner")
    )


def _reconciled(outcome: ReconciledOutcome) -> ReconciliationEvidence:
    return ReconciliationEvidence(
        kind="reconciliation", evidence_ref=b.digest("external"), outcome=outcome
    )


# ---------------------------------------------------------------- WP04-R01: stale runtimes


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_second_runtime_with_a_stale_state_is_refused(tmp_path: Path, backend: str) -> None:
    journal: Any
    if backend == "memory":
        journal = InMemoryJournal()
    else:
        journal = SqliteJournal.open(
            tmp_path / "journal.db", clock=SystemClock(), random=OsRandomSource()
        )
    try:
        first = w.machine(journal)
        run = w.running_run(first)
        second = w.machine(journal)
        assert second.state == first.state
        first.set_run_status(first.state.runs[run.run_id], R.WAITING_TOOL)
        stored = _events(journal)
        with pytest.raises(StaleStateError):
            second.set_run_status(second.state.runs[run.run_id], R.WAITING_APPROVAL)
        assert _events(journal) == stored
        # The stale runtime refuses every further command until it is reloaded.
        with pytest.raises(RuntimeReloadRequiredError):
            w.task(second)
        reloaded = w.machine(journal)
        assert reloaded.state == first.state
        assert reloaded.state.runs[run.run_id].status is R.WAITING_TOOL
    finally:
        if backend == "sqlite":
            journal.close()
    if backend == "sqlite":
        reopened = SqliteJournal.open(
            tmp_path / "journal.db", clock=SystemClock(), random=OsRandomSource()
        )
        try:
            assert reopened.verify_tenant(b.TENANT) is not None
            assert w.machine(reopened).state.runs[run.run_id].status is R.WAITING_TOOL
        finally:
            reopened.close()


def test_change_between_verification_and_write_is_refused() -> None:
    journal = InMemoryJournal()
    other = w.machine(journal)

    class InterferingVerifier:
        def verify(self, evidence: Any, *, subject: Any) -> None:
            # Another runtime writes while this one is verifying evidence.
            w.task(w.machine(journal))

    step = w.step_in_progress(other, w.running_run(other))
    sm = w.machine(journal, verifier=InterferingVerifier())
    attempt = sm.create_attempt(sm.state.steps[step.step_id])
    before = _events(journal)
    with pytest.raises(StaleStateError):
        sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    after = _events(journal)
    assert len(after) == len(before) + 1  # only the interfering task
    assert all(e.payload is not None for e in after)
    assert rebuild(after).attempts[attempt.attempt_id].state is A.PROPOSED


def test_unknown_write_outcome_requires_a_reload() -> None:
    journal = _FailingJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    journal.error = JournalWriteOutcomeUnknownError("synthetic unknown outcome")
    journal.fail = True
    with pytest.raises(JournalWriteOutcomeUnknownError):
        sm.set_run_status(run, R.WAITING_TOOL)
    journal.fail = False
    with pytest.raises(RuntimeReloadRequiredError):
        sm.set_run_status(run, R.WAITING_TOOL)


def test_failure_to_apply_after_commit_stops_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)

    def broken_apply(*_args: Any, **_kwargs: Any) -> Any:
        raise StateRebuildError("synthetic fold failure")

    monkeypatch.setattr(service_module, "apply_event", broken_apply)
    with pytest.raises(StateDivergenceError):
        sm.set_run_status(run, R.WAITING_TOOL)
    monkeypatch.undo()
    assert len(_events(journal)) == 5  # the event was committed
    with pytest.raises(RuntimeReloadRequiredError):
        sm.set_run_status(run, R.WAITING_TOOL)
    assert w.machine(journal).state.runs[run.run_id].status is R.WAITING_TOOL


class _FailingJournal(InMemoryJournal):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False
        self.error: Exception = RuntimeError("unset")

    def append(self, draft: Any, *, expected: Any) -> Event:
        if self.fail:
            raise self.error
        return super().append(draft, expected=expected)


# ---------------------------------------------------------------- WP04-R02: rebuild integrity


def _rewrite(event: Event, mutate: Any) -> Event:
    data = json.loads(dump_json(event))
    mutate(data)
    return Event.model_validate_json(json.dumps(data))


def test_full_rebuild_must_start_at_tenant_seq_1() -> None:
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    events = _events(journal)
    with pytest.raises(StateRebuildError):
        rebuild(events[1:])


def test_rebuild_checks_the_hash_chain() -> None:
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    events = _events(journal)
    events[2] = _rewrite(events[2], lambda d: d.update(prev_hash=b.digest("forged")))
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_run_creation_must_attach_the_run_in_the_same_event() -> None:
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    events = _events(journal)

    def drop_attach(d: dict[str, Any]) -> None:
        d["payload"]["transitions"] = [
            t for t in d["payload"]["transitions"] if t.get("action") != "ATTACH_RUN"
        ]

    events[1] = _rewrite(events[1], drop_attach)
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_run_owner_epoch_must_match_its_creating_event() -> None:
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    events = _events(journal)

    def forge_epoch(d: dict[str, Any]) -> None:
        for t in d["payload"]["transitions"]:
            if t["entity"] == "run":
                t["after"]["owner_epoch"] = 7

    events[1] = _rewrite(events[1], forge_epoch)
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_load_follows_a_journal_that_grows_while_reading() -> None:
    class GrowingJournal(InMemoryJournal):
        grown = False

        def read_tenant(self, tenant_id: Any, *, after_tenant_seq: int, limit: int) -> Any:
            page = super().read_tenant(tenant_id, after_tenant_seq=after_tenant_seq, limit=limit)
            if not self.grown:
                self.grown = True
                w.task(w.machine(_Snapshot(self)))
            return page

    journal = GrowingJournal()
    w.task(w.machine(journal))
    loaded = w.machine(journal)
    assert len(loaded.state.tasks) == 2
    assert loaded.state == rebuild(_events(journal))


class _Snapshot:
    """Read-through view of a journal that never triggers the growth hook."""

    def __init__(self, journal: InMemoryJournal) -> None:
        self._journal = journal
        self.owner_epoch = journal.owner_epoch

    def read_tenant(self, tenant_id: Any, *, after_tenant_seq: int, limit: int) -> Any:
        return InMemoryJournal.read_tenant(
            self._journal, tenant_id, after_tenant_seq=after_tenant_seq, limit=limit
        )

    def head(self, tenant_id: Any) -> Any:
        return self._journal.head(tenant_id)

    def read_run(self, run_id: Any, *, after_run_seq: int, limit: int) -> Any:
        return self._journal.read_run(run_id, after_run_seq=after_run_seq, limit=limit)

    def append(self, draft: Any, *, expected: Any) -> Event:
        return self._journal.append(draft, expected=expected)


# ---------------------------------------------------------------- WP04-R03: unresolved effects


def _unknown_in_failed_run(sm: Any) -> tuple[Any, Any, Any]:
    the_task = w.task(sm)
    run = w.running_run(sm, the_task)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    attempt = sm.advance_attempt(
        attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN)
    )
    sm.set_run_status(
        sm.state.runs[run.run_id], R.FAILED, _termination(TerminationReason.INTERNAL_ERROR)
    )
    return the_task, run, attempt


def test_failed_run_keeps_its_unknown_attempt_and_blocks_a_new_run() -> None:
    sm = w.machine()
    the_task, _, attempt = _unknown_in_failed_run(sm)
    assert sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    task_now = sm.state.tasks[the_task.task_id]
    with pytest.raises(TransitionPreconditionError):
        sm.start_run(task_now, run_grant_ref=b.new_id(RunGrantId))
    for target in (T.COMPLETED, T.CANCELLED, T.PAUSED):
        with pytest.raises(TransitionPreconditionError):
            sm.set_task_status(task_now, target)
    # Accepting a permanently unknown effect as a failure stays possible.
    assert sm.set_task_status(task_now, T.FAILED).status is T.FAILED


def test_reconciliation_in_a_terminal_run_is_recorded_and_unblocks_the_task() -> None:
    sm = w.machine()
    the_task, run, attempt = _unknown_in_failed_run(sm)
    resolved = sm.advance_attempt(attempt, A.FAILED_DEFINITE, _reconciled(ReconciledOutcome.FAILED))
    assert resolved.state is A.FAILED_DEFINITE
    assert sm.last_transition_was_late
    assert sm.state.runs[run.run_id].status is R.FAILED  # the run is not resumed
    new_run = sm.start_run(sm.state.tasks[the_task.task_id], run_grant_ref=b.new_id(RunGrantId))
    assert new_run.parent_run_id == run.run_id


def test_consumed_permit_in_a_terminal_run_can_only_be_settled() -> None:
    sm = w.machine()
    the_task = w.task(sm)
    run = w.running_run(sm, the_task)
    step = w.step_in_progress(sm, run)
    attempt = w.drive(sm, sm.create_attempt(step), w.PATH_WITHOUT_APPROVAL[:7])
    assert attempt.state is A.PERMIT_CONSUMED
    sm.set_run_status(sm.state.runs[run.run_id], R.ABORTED_SAFETY, _termination())
    with pytest.raises(TransitionPreconditionError):
        sm.start_run(sm.state.tasks[the_task.task_id], run_grant_ref=b.new_id(RunGrantId))
    with pytest.raises(TransitionPreconditionError):
        sm.advance_attempt(
            attempt, A.DISPATCH_AUTHORIZED, w.evidence_for(attempt, A.DISPATCH_AUTHORIZED)
        )
    unknown = sm.advance_attempt(
        attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN)
    )
    assert unknown.state is A.OUTCOME_UNKNOWN


def test_no_new_step_while_an_attempt_is_unresolved() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    with pytest.raises(TransitionPreconditionError):
        sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    with pytest.raises(TransitionPreconditionError):
        sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)


# ---------------------------------------------------------------- WP04-R04: cancel intent


def test_cancel_intent_survives_reconciliation_and_rebuild() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    cancelling = sm.set_run_status(sm.state.runs[run.run_id], R.CANCELLING)
    assert cancelling.cancel_requested
    unknown = sm.advance_attempt(
        attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN)
    )
    reconciling = sm.set_run_status(cancelling, R.RECONCILIATION_REQUIRED, _trigger())
    assert reconciling.cancel_requested
    assert rebuild(_events(journal)).runs[run.run_id].cancel_requested

    # A late result is recorded but never used for progress.
    resolved = sm.advance_attempt(unknown, A.COMPLETED, _reconciled(ReconciledOutcome.COMPLETED))
    assert sm.last_transition_was_late
    assert resolved.state is A.COMPLETED
    with pytest.raises(TransitionPreconditionError):
        sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    with pytest.raises(TransitionPreconditionError):
        sm.create_attempt(sm.state.steps[step.step_id])
    # The cancel request cannot be dropped by resuming.
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(reconciling, R.RUNNING, _resolution(ResolutionDecision.RESUME))
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(reconciling, R.CANCELLED, _resolution(ResolutionDecision.CANCEL))
    sm.set_step_status(sm.state.steps[step.step_id], S.CANCELLED)
    cancelled = sm.set_run_status(reconciling, R.CANCELLED, _resolution(ResolutionDecision.CANCEL))
    assert cancelled.status is R.CANCELLED
    assert cancelled.cancel_requested


def test_cancel_intent_cannot_be_forged_away_in_the_journal() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    sm.set_run_status(sm.state.runs[run.run_id], R.CANCELLING)
    events = _events(journal)

    def drop_intent(d: dict[str, Any]) -> None:
        d["payload"]["transitions"][0]["after"]["cancel_requested"] = False

    # A CANCELLING run without the intent is not even a valid record, so such an
    # event cannot be stored or read back (the journal restores with load_json).
    with pytest.raises(ValueError, match="cancel_requested"):
        _rewrite(events[-1], drop_intent)


def test_unknown_outcome_blocks_cancelled_even_after_reconciliation_state() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    cancelling = sm.set_run_status(sm.state.runs[run.run_id], R.CANCELLING)
    sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, w.evidence_for(attempt, A.OUTCOME_UNKNOWN))
    reconciling = sm.set_run_status(cancelling, R.RECONCILIATION_REQUIRED, _trigger())
    with pytest.raises(TransitionPreconditionError):
        sm.set_run_status(reconciling, R.CANCELLED, _resolution(ResolutionDecision.CANCEL))
