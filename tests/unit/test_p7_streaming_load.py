"""P-7 M5: the load reads the journal page by page and folds as it reads (tests first).

``RuntimeStateMachine.load`` no longer collects every event in a list: each
page is checked and folded (the Rebuild's gap, duplicate, order and hash chain
checks) before the next page is read. The contract for a journal that grows
while it is read is kept (reading goes on until the head is the last event
read), the resulting state and head are those of the earlier load, and a
failure returns no state. A head that stays ahead of the events the journal
hands out is refused instead of being read again forever. Only the events in
flight are bounded; the ``RuntimeState`` itself still grows with the journal.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_core.runtime import service as service_module
from agent_core.runtime import state_machine as smod
from agent_core.types.errors import StateRebuildError
from support import builders as b
from support import runtime_world as w
from support.fakes import InMemoryJournal


def _drive(journal: Any, runs: int = 4) -> None:
    sm = w.machine(journal)
    for _ in range(runs):
        w.running_run(sm)


class _Logged(InMemoryJournal):
    log: list[tuple[str, int]]

    def read_tenant(self, tenant_id: Any, *, after_tenant_seq: int, limit: int) -> Any:
        self.log.append(("read", after_tenant_seq))
        return super().read_tenant(tenant_id, after_tenant_seq=after_tenant_seq, limit=limit)


def test_each_page_is_folded_before_the_next_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = _Logged()
    journal.log = []
    _drive(journal)
    total = journal.head(b.TENANT).tenant_seq  # type: ignore[union-attr]
    journal.log = []
    real = smod._fold_event

    def logged(fold: Any, event: Any) -> None:
        journal.log.append(("fold", event.tenant_seq))
        real(fold, event)

    monkeypatch.setattr(smod, "_fold_event", logged)
    monkeypatch.setattr(service_module, "READ_PAGE", 3)
    loaded = w.machine(journal)
    reads = [i for i, (kind, _) in enumerate(journal.log) if kind == "read"]
    folds = [seq for kind, seq in journal.log if kind == "fold"]
    assert folds == list(range(1, total + 1))
    # Before each later read, every event of the earlier pages is already folded.
    for index in reads[1:]:
        after = journal.log[index][1]
        folded = [seq for kind, seq in journal.log[:index] if kind == "fold"]
        assert folded == list(range(1, after + 1))
    assert len(loaded.state.runs) == 4


def test_the_load_does_not_build_a_list_of_every_event(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = InMemoryJournal()
    _drive(journal)
    seen: list[Any] = []
    real = smod.rebuild_with_head

    def spy(events: Any) -> Any:
        seen.append(events)
        return real(events)

    monkeypatch.setattr(service_module, "rebuild_with_head", spy)
    w.machine(journal)
    assert len(seen) == 1
    assert not isinstance(seen[0], (list, tuple))  # an iterator, consumed as it is read


def test_the_state_and_head_are_those_of_a_full_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = InMemoryJournal()
    _drive(journal, runs=5)
    events = list(journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10_000))
    state, last, last_hash = smod.rebuild_with_head(events)
    for page in (1, 4, 1000):
        monkeypatch.setattr(service_module, "READ_PAGE", page)
        loaded = w.machine(journal)
        assert loaded.state == state
        assert last is not None
        assert loaded._head.tenant_seq == last.tenant_seq
        assert loaded._head.head_hash == last_hash


def test_a_head_ahead_of_the_events_is_refused_not_read_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Missing(InMemoryJournal):
        reads = 0
        hide_from = 10**9

        def read_tenant(self, tenant_id: Any, *, after_tenant_seq: int, limit: int) -> Any:
            self.reads += 1
            if self.reads > 50:
                raise AssertionError("the load keeps reading")
            page = super().read_tenant(tenant_id, after_tenant_seq=after_tenant_seq, limit=limit)
            return tuple(e for e in page if e.tenant_seq < self.hide_from)

    journal = Missing()
    _drive(journal)
    journal.hide_from = 6  # the head says more events exist than are handed out
    monkeypatch.setattr(service_module, "READ_PAGE", 4)
    with pytest.raises(StateRebuildError):
        w.machine(journal)


def test_a_failure_returns_no_state(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = InMemoryJournal()
    _drive(journal)
    real = smod._fold_event

    def fail_late(fold: Any, event: Any) -> None:
        if event.tenant_seq == 9:
            raise StateRebuildError("synthetic: a bad event in a later page")
        real(fold, event)

    monkeypatch.setattr(smod, "_fold_event", fail_late)
    monkeypatch.setattr(service_module, "READ_PAGE", 3)
    with pytest.raises(StateRebuildError):
        w.machine(journal)
