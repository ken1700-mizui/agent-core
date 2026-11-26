"""Synthetic implementations of the WP-02 ports, for tests only.

They hold everything in memory. They are not adapters: nothing here persists,
and uniqueness checked here does not survive a restart (persistent uniqueness
is WP-03's SQLite UNIQUE constraint).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from agent_core.ports.journal import JournalHead
from agent_core.types.errors import DuplicateIdError
from agent_core.types.event_hash import event_hash
from agent_core.types.ids import EntityId, EventId, RunId, TenantId
from agent_core.types.records import Event, EventDraft
from agent_core.types.values import Digest


class ScriptedClock:
    """Returns the given millisecond timestamps in order, then repeats the last one."""

    def __init__(self, *timestamps_ms: int) -> None:
        if not timestamps_ms:
            raise ValueError("at least one timestamp is required")
        self._values = list(timestamps_ms)
        self.calls = 0

    def now_ms(self) -> int:
        index = min(self.calls, len(self._values) - 1)
        self.calls += 1
        return self._values[index]


class ScriptedRandomSource:
    """Returns the given byte strings in order. Fails when exhausted."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks: Iterator[bytes] = iter(chunks)
        self.requested: list[int] = []

    def token_bytes(self, n: int) -> bytes:
        self.requested.append(n)
        try:
            return next(self._chunks)
        except StopIteration:
            raise AssertionError("scripted random source exhausted") from None


class InMemoryIdRegistry:
    """Rejects an ID that was already registered in this process."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def register(self, entity_id: EntityId) -> None:
        if str(entity_id) in self._seen:
            raise DuplicateIdError(entity_id)
        self._seen.add(str(entity_id))


class InMemoryJournal:
    """Minimal synthetic journal honouring the JournalPort contract.

    Uses the real event hash (agent_core.types.event_hash) but keeps everything
    in memory; it is not a substitute for the SQLite journal.
    """

    def __init__(self) -> None:
        self._events: dict[TenantId, list[Event]] = {}
        self._event_ids: set[EventId] = set()
        self._run_seq: dict[RunId, int] = {}

    def append(self, draft: EventDraft) -> Event:
        if draft.event_id in self._event_ids:
            raise DuplicateIdError(draft.event_id)
        events = self._events.setdefault(draft.tenant_id, [])
        prev_hash = _stand_in_hash(events[-1]) if events else None
        run_seq = None
        if draft.run_id is not None:
            run_seq = self._run_seq.get(draft.run_id, 0) + 1
        event = Event.from_draft(
            draft, tenant_seq=len(events) + 1, run_seq=run_seq, prev_hash=prev_hash
        )
        events.append(event)
        self._event_ids.add(draft.event_id)
        if draft.run_id is not None and run_seq is not None:
            self._run_seq[draft.run_id] = run_seq
        return event

    def read_tenant(
        self, tenant_id: TenantId, *, after_tenant_seq: int, limit: int
    ) -> tuple[Event, ...]:
        events = self._events.get(tenant_id, [])
        return tuple(events[after_tenant_seq : after_tenant_seq + limit])

    def read_run(self, run_id: RunId, *, after_run_seq: int, limit: int) -> tuple[Event, ...]:
        events = [e for es in self._events.values() for e in es if e.run_id == run_id]
        return tuple(events[after_run_seq : after_run_seq + limit])

    def head(self, tenant_id: TenantId) -> JournalHead | None:
        events = self._events.get(tenant_id)
        if not events:
            return None
        last = events[-1]
        return JournalHead(
            tenant_id=tenant_id, tenant_seq=last.tenant_seq, head_hash=_stand_in_hash(last)
        )


def _stand_in_hash(event: Event) -> Digest:
    return event_hash(event)
