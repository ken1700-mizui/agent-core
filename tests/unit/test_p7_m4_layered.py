"""P-7 M4: the live path no longer copies a whole table per transition (tests first).

The state's tables are ``_Layered`` mappings: a frozen base dictionary and a
small overlay of later changes. A change copies only the overlay; the overlay is
merged into a new base once it passes about the square root of the base. Every
mapping behaves as an immutable dictionary with the same contents and the same
order a dictionary would have, and a state, once made, never changes (an older
state keeps its contents after later transitions). The rules are not touched.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, MutableMapping
from typing import Any

import pytest

from agent_core.runtime import state_machine as smod
from agent_core.runtime.state_machine import RuntimeState
from agent_core.types.records import StepKind, StepStatus
from support import identity_world as iw
from support import runtime_world as w

L = smod._Layered


def test_a_layered_mapping_is_an_immutable_mapping() -> None:
    table = L.of({"a": 1})
    assert isinstance(table, Mapping)
    assert not isinstance(table, MutableMapping)
    with pytest.raises(TypeError):
        table["b"] = 2  # type: ignore[index]
    with pytest.raises(KeyError):
        table["missing"]


def test_it_behaves_as_a_dictionary_through_any_sequence_of_changes() -> None:
    rng = random.Random(7)  # noqa: S311 (a fixed, deterministic test sequence)
    reference: dict[int, int] = {}
    table: smod._Layered[int, int] = L.of({})
    snapshots: list[tuple[Any, dict[int, int]]] = []
    for step in range(3000):
        key = rng.randrange(400)
        if rng.random() < 0.05 and key in reference:
            del reference[key]
            table = table.delete(key)
        else:
            reference[key] = step
            table = table.set(key, step)
        if step % 97 == 0:
            snapshots.append((table, dict(reference)))
        assert len(table) == len(reference)
    assert list(table.items()) == list(reference.items())  # same order as a dict
    assert table == reference
    assert table == L.of(reference)
    for old, expected in snapshots:  # older versions are never changed
        assert list(old.items()) == list(expected.items())
    assert table.get(10_000, "none") == "none"
    assert (10_000 in table) is False


def test_a_change_copies_only_the_overlay() -> None:
    base = L.of({i: i for i in range(100_000)})
    changed = base.set(5, -5).set(100_001, 1)
    assert changed._base is base._base  # the big base is shared, not copied
    assert len(changed._over) == 2
    assert base[5] == 5  # the earlier mapping is unchanged
    assert changed[5] == -5


def test_the_overlay_is_merged_once_it_grows() -> None:
    table = L.of({i: i for i in range(10_000)})
    for i in range(400):
        table = table.set(10_000 + i, i)
    assert len(table._over) <= 2 * int(len(table._base) ** 0.5) + 64
    assert len(table) == 10_400


def test_transitions_never_change_an_earlier_state() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    earlier = sm.state
    snapshot = iw.semantic(earlier)
    for _ in range(50):
        step = sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
        step = sm.set_step_status(step, StepStatus.IN_PROGRESS)
        sm.set_step_status(step, StepStatus.CANCELLED)
    assert iw.semantic(earlier) == snapshot
    assert len(sm.state.steps) == 50


def test_every_table_of_a_state_is_layered() -> None:
    sm = w.machine()
    w.dispatched_attempt(sm, w.step_in_progress(sm, w.running_run(sm)))
    for state in (sm.state, RuntimeState.empty()):
        for name in state.__dataclass_fields__:
            assert isinstance(getattr(state, name), L), name


def test_a_live_transition_shares_the_big_tables() -> None:
    sm = w.machine()
    run = w.running_run(sm)
    for _ in range(300):
        step = sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
        sm.set_step_status(step, StepStatus.IN_PROGRESS)
    before = sm.state.steps
    step = sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
    after = sm.state.steps
    assert isinstance(before, L)
    assert isinstance(after, L)
    assert after._base is before._base or len(after._over) == 0  # shared, or merged once
    assert step.step_id in after
    assert step.step_id not in before


def test_a_failed_append_keeps_the_earlier_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.types.errors import JournalStorageError
    from support.fakes import InMemoryJournal

    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    before = sm.state
    snapshot = iw.semantic(before)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise JournalStorageError("synthetic: the append is refused")

    monkeypatch.setattr(journal, "append", refuse)
    with pytest.raises(JournalStorageError):
        sm.create_step(sm.state.runs[run.run_id], StepKind.TOOL_CALL)
    assert sm.state is before  # nothing unconfirmed is shown
    assert iw.semantic(sm.state) == snapshot
