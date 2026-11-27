"""WP04-R05: event schema versions.

``tests/fixtures/wp04_v2_events.jsonl`` holds runtime events written by the
WP-04 draft (commit 9f1a799, schema_version 2, Run without cancel_requested).
Version 2 was later written with a different Run shape (635e0b3), so the same
number had two meanings during development. Version 2 is therefore retired:
it is rejected with an explicit reason, never reinterpreted, and the fixture's
bytes are kept unchanged as evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.types.errors import JournalCorruptionError, RecordValidationError
from agent_core.types.event_hash import EVENT_HASH_DOMAIN
from agent_core.types.events import SUPPORTED_EVENT_SCHEMA_VERSIONS, Event
from agent_core.types.serialization import load_json
from agent_core.types.transitions import RUNTIME_EVENT_SCHEMA_VERSION
from support import builders as b
from support import runtime_world as w
from support.fakes import InMemoryJournal

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wp04_v2_events.jsonl"
FIXTURE_SHA256 = "485c77235967a4ad6629832d1ef7e448f6687d480a1b0ccde9bcf7ef77531442"


def _lines() -> list[bytes]:
    return FIXTURE.read_bytes().splitlines()


def test_fixture_bytes_are_unchanged() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert all(json.loads(line)["schema_version"] == 2 for line in _lines())
    assert all(b"cancel_requested" not in line for line in _lines())


def test_versions_in_use() -> None:
    assert RUNTIME_EVENT_SCHEMA_VERSION == 3
    assert frozenset({1, 3}) == SUPPORTED_EVENT_SCHEMA_VERSIONS


@pytest.mark.parametrize("index", range(5))
def test_retired_version_2_is_rejected_with_an_explicit_reason(index: int) -> None:
    line = _lines()[index]
    with pytest.raises(ValidationError) as excinfo:
        Event.model_validate_json(line)
    assert "superseded_schema_version" in {e["type"] for e in excinfo.value.errors()}
    with pytest.raises(RecordValidationError) as typed:
        load_json(Event, line)
    assert "superseded_schema_version" in {i.type for i in typed.value.issues}


def test_journal_with_version_2_events_is_refused_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource()).close()
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        prev = None
        for seq, line in enumerate(_lines(), start=1):
            data = json.loads(line)
            digest = "sha256:" + hashlib.sha256(EVENT_HASH_DOMAIN + line).hexdigest()
            if seq == 1:
                conn.execute(
                    "INSERT INTO tenant_head VALUES (?, ?, ?)", (data["tenant_id"], seq, digest)
                )
            else:
                conn.execute("UPDATE tenant_head SET tenant_seq = ?, head_hash = ?", (seq, digest))
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, NULL, NULL, 1, ?, ?)",
                (data["tenant_id"], seq, data["event_id"], digest, line.decode()),
            )
            assert data["prev_hash"] == prev
            prev = digest
    finally:
        conn.close()
    before = path.read_bytes()
    with pytest.raises(JournalCorruptionError) as excinfo:
        SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())
    cause = excinfo.value.__cause__
    assert isinstance(cause, RecordValidationError)
    assert "superseded_schema_version" in {i.type for i in cause.issues}
    assert path.read_bytes() == before


def test_current_runtime_writes_version_3_with_the_cancel_intent() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=100)
    assert {e.schema_version for e in events} == {3}
    assert all(
        "cancel_requested" in t.after.model_dump()
        for e in events
        if e.payload is not None
        for t in e.payload.transitions
        if t.entity == "run"
    )
