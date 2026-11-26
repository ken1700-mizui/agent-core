"""Ports: synthetic and system implementations satisfy the Protocols.

Static compatibility is checked by mypy through the annotated assignments;
runtime compatibility through ``runtime_checkable`` isinstance checks.
"""

from __future__ import annotations

import pytest

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.ports.clock import Clock
from agent_core.ports.id_registry import IdRegistry
from agent_core.ports.journal import JournalPort
from agent_core.ports.randomness import RandomSource
from agent_core.types.errors import DuplicateIdError
from agent_core.types.events import Event, EventDraft
from agent_core.types.ids import MAX_TIMESTAMP_MS, MIN_GENERATION_TIMESTAMP_MS, EventId, RunId
from support import builders as b
from support.fakes import (
    InMemoryIdRegistry,
    InMemoryJournal,
    ScriptedClock,
    ScriptedRandomSource,
)


def test_implementations_satisfy_the_protocols() -> None:
    clocks: list[Clock] = [ScriptedClock(b.TS), SystemClock()]
    randoms: list[RandomSource] = [ScriptedRandomSource([]), OsRandomSource()]
    registry: IdRegistry = InMemoryIdRegistry()
    journal: JournalPort = InMemoryJournal()
    assert all(isinstance(c, Clock) for c in clocks)
    assert all(isinstance(r, RandomSource) for r in randoms)
    assert isinstance(registry, IdRegistry)
    assert isinstance(journal, JournalPort)


def test_system_clock_is_in_the_generation_range() -> None:
    now = SystemClock().now_ms()
    assert type(now) is int
    assert MIN_GENERATION_TIMESTAMP_MS <= now <= MAX_TIMESTAMP_MS


@pytest.mark.parametrize("n", [1, 10, 32])
def test_os_random_source_returns_the_requested_length(n: int) -> None:
    assert len(OsRandomSource().token_bytes(n)) == n


@pytest.mark.parametrize("n", [0, -1])
def test_os_random_source_rejects_non_positive_lengths(n: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        OsRandomSource().token_bytes(n)


def test_synthetic_journal_assigns_gapless_positions_and_a_chain() -> None:
    journal = InMemoryJournal()
    run_id = b.new_id(RunId)
    first = journal.append(b.event_draft())
    second = journal.append(b.event_draft(run_id=run_id))
    third = journal.append(b.event_draft(run_id=run_id))
    assert [e.tenant_seq for e in (first, second, third)] == [1, 2, 3]
    assert [second.run_seq, third.run_seq] == [1, 2]
    assert first.prev_hash is None
    assert second.prev_hash is not None
    head = journal.head(b.TENANT)
    assert head is not None
    assert head.tenant_seq == 3
    assert journal.read_tenant(b.TENANT, after_tenant_seq=1, limit=10) == (second, third)


def test_journal_duplicate_event_id_drives_the_generator_retry() -> None:
    journal = InMemoryJournal()
    taken = EventId.compose(b.SITE, b.TS, 5)
    journal.append(b.event_draft(event_id=taken))
    random = ScriptedRandomSource([(5).to_bytes(10, "big"), (6).to_bytes(10, "big")])
    generator = IdGenerator(b.SITE, ScriptedClock(b.TS), random)

    def append(event_id: EventId) -> Event:
        draft: EventDraft = b.event_draft(event_id=event_id)
        return journal.append(draft)

    stored = generator.issue_unique(EventId, append)
    assert stored.event_id.random_bits == 6
    assert stored.tenant_seq == 2
    with pytest.raises(DuplicateIdError):
        journal.append(b.event_draft(event_id=taken))
