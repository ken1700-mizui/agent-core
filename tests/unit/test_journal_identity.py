"""P-7 S1: the state rebuilt from the journal is the state the Runtime had
(identity tests, written before any performance change).

For every scenario (shapes A, B and C, a stop, a recovery, an unknown outcome
and a late reconciliation) the journal is written through the real Runtime path
and then read back in three ways:

- the live state (built transition by transition while the journal was written);
- ``RuntimeStateMachine.load`` after a restart (the Rebuild the changes touch);
- a reference fold that applies ``apply_event`` to each stored event in order.

All three must have the same meaning (``identity_world.semantic``), the same
digest, and the cost side and the journal head must not change by reading.
The checks against malformed journals stay in ``test_runtime_consistency.py``;
the ones here cover paging (the streaming read of M5) and a failure in a later
page. The cross-version check (journals written by the earlier version, read by
the new one) is ``tools/perf/state_identity.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime import service as service_module
from agent_core.runtime.state_machine import RuntimeState, apply_event, rebuild_with_head
from agent_core.types.errors import StateRebuildError
from agent_core.types.events import Event
from agent_core.types.serialization import dump_json
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w
from support.fakes import InMemoryJournal

SCENARIOS = sorted(iw.SCENARIOS)


def _all_events(journal: Any) -> list[Event]:
    events: list[Event] = []
    while True:
        page = journal.read_tenant(b.TENANT, after_tenant_seq=len(events), limit=500)
        if not page:
            return events
        events.extend(page)


def _reference_fold(events: list[Event]) -> RuntimeState:
    state = RuntimeState.empty()
    for event in events:
        state = apply_event(state, event)
    return state


@pytest.fixture(params=SCENARIOS)
def written(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, lw.Setup]:
    st = iw.SCENARIOS[request.param](lw.setup(tmp_path))
    return request.param, st


def test_the_rebuild_has_the_meaning_of_the_live_state(written: tuple[str, lw.Setup]) -> None:
    _name, st = written
    live = iw.semantic(st.sm.state)
    ledger = iw.ledger_view(st)
    again = st.reopen()
    assert iw.semantic(again.sm.state) == live
    assert iw.ledger_view(again) == ledger  # reading changes no hold, cost, stop or head


def test_the_rebuild_equals_the_reference_fold(written: tuple[str, lw.Setup]) -> None:
    _name, st = written
    events = _all_events(st.journal)
    reference = _reference_fold(events)
    rebuilt, last, last_hash = rebuild_with_head(events)
    assert rebuilt == reference
    head = st.journal.head(b.TENANT)
    assert head is not None
    assert last is not None
    assert (head.tenant_seq, head.head_hash) == (last.tenant_seq, last_hash)
    assert iw.semantic(st.reopen().sm.state) == iw.semantic(reference)


def test_the_digest_is_deterministic_across_reloads(written: tuple[str, lw.Setup]) -> None:
    _name, st = written
    first = st.reopen()
    one = iw.digest([iw.semantic(first.sm.state), iw.ledger_view(first)])
    second = first.reopen()
    assert iw.digest([iw.semantic(second.sm.state), iw.ledger_view(second)]) == one


def test_each_scenario_reaches_the_states_it_is_named_for(tmp_path: Path) -> None:
    """Guards the scenarios themselves: each one covers what it is named for."""
    seen: dict[str, set[str]] = {}
    for name in SCENARIOS:
        (tmp_path / name).mkdir()
        st = iw.SCENARIOS[name](lw.setup(tmp_path / name))
        state = st.sm.state
        seen[name] = {a.state.value for a in state.attempts.values()} | {
            r.status.value for r in state.runs.values()
        }
        assert state.tasks
        assert state.runs
    assert "OUTCOME_UNKNOWN" in seen["unknown"]
    assert "COMPLETED" in seen["late"]
    assert "OUTCOME_UNKNOWN" not in seen["late"]
    assert "CANCELLED" in seen["stop"]
    assert "COMPLETED" in seen["C"]


# ---------------------------------------------------------------- paging (M5)


@pytest.mark.parametrize("page", [1, 2, 7, 1000])
def test_the_page_size_does_not_change_the_state(
    page: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = iw.shape_c(lw.setup(tmp_path), runs=2)
    again = st.reopen()
    expected = again.sm.state
    again.journal.close()
    monkeypatch.setattr(service_module, "READ_PAGE", page)
    journal = lw.open_journal(st.path, st.clock, st.keys)
    try:
        assert w.machine(journal).state == expected
    finally:
        journal.close()


class _TamperingJournal(InMemoryJournal):
    """Hands out a changed copy of one stored event (the stored one is kept)."""

    target = 0
    mutate: Any = None

    def read_tenant(self, tenant_id: Any, *, after_tenant_seq: int, limit: int) -> Any:
        page = super().read_tenant(tenant_id, after_tenant_seq=after_tenant_seq, limit=limit)
        return tuple(self._maybe(e) for e in page)

    def _maybe(self, event: Event) -> Event:
        if event.tenant_seq != self.target:
            return event
        data = json.loads(dump_json(event))
        self.mutate(data)
        return Event.model_validate_json(json.dumps(data))


def _drive(journal: Any) -> None:
    sm = w.machine(journal)
    for _ in range(4):
        w.running_run(sm)


def _tampered(mutate: Any, target: int) -> Iterator[_TamperingJournal]:
    journal = _TamperingJournal()
    _drive(journal)
    journal.target = target
    journal.mutate = mutate
    yield journal


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(prev_hash=b.digest("forged")), id="hash"),
        pytest.param(lambda d: d.update(tenant_seq=d["tenant_seq"] + 1), id="gap"),
        pytest.param(lambda d: d.update(tenant_seq=d["tenant_seq"] - 1), id="duplicate"),
    ],
)
def test_a_bad_event_in_a_later_page_refuses_the_whole_load(
    mutate: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "READ_PAGE", 3)
    for journal in _tampered(mutate, target=8):
        with pytest.raises(StateRebuildError):
            w.machine(journal)


def test_a_head_that_does_not_match_the_events_read_is_refused() -> None:
    class WrongHead(InMemoryJournal):
        broken = False

        def head(self, tenant_id: Any) -> Any:
            real = super().head(tenant_id)
            if real is None or not self.broken:
                return real
            return real.model_copy(update={"head_hash": b.digest("x")})

    journal = WrongHead()
    _drive(journal)
    journal.broken = True
    with pytest.raises(StateRebuildError):
        w.machine(journal)
