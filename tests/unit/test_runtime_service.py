"""Runtime state machine and the Journal (WP-04): commit-before-state, rebuild, versions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.runtime.state_machine import RuntimeState, rebuild
from agent_core.types.errors import (
    JournalStorageError,
    StateRebuildError,
)
from agent_core.types.event_hash import event_hash
from agent_core.types.events import Event
from agent_core.types.records import AttemptState, RunStatus, StepStatus, TaskStatus
from agent_core.types.serialization import dump_json
from agent_core.types.transitions import RUNTIME_EVENT_SCHEMA_VERSION, RUNTIME_EVENT_TYPE
from support import builders as b
from support import runtime_world as w
from support.fakes import InMemoryJournal

A, R, S = AttemptState, RunStatus, StepStatus


class FailingJournal(InMemoryJournal):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False
        self.error: Exception = JournalStorageError("synthetic journal failure")

    def append(self, draft: Any, *, expected: Any) -> Event:
        if self.fail:
            raise self.error
        return super().append(draft, expected=expected)


def _scenario(sm: Any) -> Any:
    """A Task with one completed Run (synthetic), touching every entity kind."""
    the_task = w.task(sm)
    run = w.running_run(sm, the_task)
    step = w.step_in_progress(sm, run)
    attempt = w.dispatched_attempt(sm, step)
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    sm.set_step_status(sm.state.steps[step.step_id], S.COMMITTED)
    verifying = sm.set_run_status(sm.state.runs[run.run_id], R.VERIFYING)
    sm.set_run_status(verifying, R.COMPLETED, w.passing_verification(the_task))
    sm.set_task_status(sm.state.tasks[the_task.task_id], TaskStatus.COMPLETED)
    return the_task


def _events(journal: Any) -> tuple[Event, ...]:
    return tuple(journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1000))


# ---------------------------------------------------------------- commit before state


def test_failed_journal_write_leaves_the_state_unchanged() -> None:
    journal = FailingJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    before = sm.state
    journal.fail = True
    with pytest.raises(JournalStorageError):
        sm.set_run_status(run, R.WAITING_TOOL)
    assert sm.state is before
    assert sm.state.runs[run.run_id].status is R.RUNNING
    journal.fail = False
    assert sm.set_run_status(run, R.WAITING_TOOL).status is R.WAITING_TOOL


def test_every_change_is_one_runtime_event() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    events = _events(journal)
    assert all(e.type == RUNTIME_EVENT_TYPE for e in events)
    assert all(e.schema_version == RUNTIME_EVENT_SCHEMA_VERSION for e in events)
    assert all(e.labels == ("synthetic",) for e in events)
    # task created; run started (task activated + run created + attached); queued; running
    assert len(events) == 4
    assert [e.run_id for e in events] == [None, run.run_id, run.run_id, run.run_id]


def test_events_carry_metadata_only() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    _scenario(sm)
    for event in _events(journal):
        text = dump_json(event).decode()
        assert "synthetic-secret" not in text
        payload = json.loads(text)["payload"]
        assert payload["transitions"]


# ---------------------------------------------------------------- rebuild


def test_rebuild_reproduces_the_live_state_deterministically() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    _scenario(sm)
    events = _events(journal)
    first = rebuild(events)
    second = rebuild(events)
    assert first == sm.state
    assert second == first
    reloaded = w.machine(journal)
    assert reloaded.state == sm.state


def test_rebuild_from_sqlite_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())
    sm = w.machine(journal)
    _scenario(sm)
    live = sm.state
    journal.close()
    reopened = SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())
    try:
        restored = w.machine(reopened)
        assert restored.state == live
        # The restored machine keeps working with the new owner epoch.
        more = w.task(restored)
        assert restored.state.tasks[more.task_id].status is TaskStatus.CREATED
    finally:
        reopened.close()


def _rewrite(event: Event, mutate: Any) -> Event:
    data = json.loads(dump_json(event))
    mutate(data)
    return Event.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(type="runtime.unknown"), id="unknown-type"),
        pytest.param(lambda d: d.update(schema_version=1, payload=None), id="no-payload"),
    ],
)
def test_rebuild_rejects_unknown_or_foreign_events(mutate: Any) -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))

    def apply(d: dict[str, Any]) -> None:
        mutate(d)
        if d.get("payload") is None:
            d.pop("payload", None)

    events[1] = _rewrite(events[1], apply)
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_rebuild_rejects_an_illegal_transition_in_the_journal() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))

    def skip_to_completed(d: dict[str, Any]) -> None:
        d["payload"]["transitions"][0]["after"]["status"] = "COMPLETED"

    events[-1] = _rewrite(events[-1], skip_to_completed)
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_rebuild_rejects_a_smuggled_field_change() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))

    def change_grant(d: dict[str, Any]) -> None:
        d["payload"]["transitions"][0]["after"]["owner_epoch"] = 99

    events[-1] = _rewrite(events[-1], change_grant)
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_rebuild_rejects_a_replayed_event() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))
    with pytest.raises(StateRebuildError):
        rebuild([*events, events[-1]])


def test_rebuild_rejects_events_out_of_order() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))
    events[2], events[3] = events[3], events[2]
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_rebuild_rejects_a_wrong_event_run_id() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = list(_events(journal))
    wrong = events[0].model_copy(update={})  # task event: run_id is None
    assert wrong.run_id is None
    events[2] = _rewrite(events[2], lambda d: d.update(run_id=None, run_seq=None))
    with pytest.raises(StateRebuildError):
        rebuild(events)


def test_empty_state_and_empty_rebuild_agree() -> None:
    assert rebuild([]) == RuntimeState.empty()


# ---------------------------------------------------------------- event format versions


def test_version_1_events_serialize_without_payload_and_keep_their_hash() -> None:
    event = b.event()
    data = json.loads(dump_json(event))
    assert "payload" not in data
    assert event_hash(event) == event_hash(Event.model_validate_json(dump_json(event)))


def test_version_1_events_cannot_carry_a_payload() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    runtime_event = _events(journal)[0]
    data = json.loads(dump_json(runtime_event))
    data["schema_version"] = 1
    with pytest.raises(ValueError, match="payload"):
        Event.model_validate_json(json.dumps(data))


def test_runtime_events_survive_the_sqlite_journal_verification(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())
    try:
        sm = w.machine(journal)
        _scenario(sm)
        assert journal.verify_tenant(b.TENANT) is not None
    finally:
        journal.close()
    conn = sqlite3.connect(path)
    try:
        versions = {row[0] for row in conn.execute("SELECT body FROM events")}
    finally:
        conn.close()
    assert all('"schema_version":3' in body for body in versions)
