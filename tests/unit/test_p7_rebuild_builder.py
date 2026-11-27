"""P-7 M2: the Rebuild builds the state in place and freezes it once (tests first).

The Rebuild folds every event into a builder that only the Rebuild can see
(plain dictionaries written in place, no copy of a whole map per transition),
through the same ``_replan`` checks and the same ``_install`` as the live path
(one set of rules), and turns it into the immutable ``RuntimeState`` once at
the end. A failure discards the builder: no mutable or partly built state ever
leaves ``rebuild_with_head``. The identity with the earlier version is
``test_journal_identity.py`` and ``tools/perf/state_identity.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime import state_machine as smod
from agent_core.runtime.state_machine import RuntimeState, apply_event, rebuild_with_head
from agent_core.types.errors import StateRebuildError
from agent_core.types.events import Event
from agent_core.types.serialization import dump_json
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw


def _events(journal: Any) -> list[Event]:
    events: list[Event] = []
    while page := journal.read_tenant(b.TENANT, after_tenant_seq=len(events), limit=500):
        events.extend(page)
    return events


def _fold(events: list[Event]) -> RuntimeState:
    state = RuntimeState.empty()
    for event in events:
        state = apply_event(state, event)
    return state


@pytest.fixture
def events(tmp_path: Path) -> list[Event]:
    st = iw.shape_c(iw.shape_a(lw.setup(tmp_path), steps=12), runs=2)
    return _events(st.journal)


def test_the_built_state_equals_the_fold_of_the_live_rules(events: list[Event]) -> None:
    built = rebuild_with_head(events)[0]
    assert built == _fold(events)
    for prefix in (1, 2, len(events) // 2, len(events) - 1):
        assert rebuild_with_head(events[:prefix])[0] == _fold(events[:prefix])


def test_the_built_state_is_immutable_and_has_no_lists(events: list[Event]) -> None:
    built = rebuild_with_head(events)[0]
    for name in built.__dataclass_fields__:
        table = getattr(built, name)
        # Immutable state tables (P-7 M4: the layered mapping; no item assignment).
        assert isinstance(table, smod._Layered), name
        with pytest.raises(TypeError):
            table["x"] = 1  # type: ignore[index]
    for ordered in (*built.run_steps.values(), *built.step_attempts.values()):
        assert type(ordered) is tuple


def test_the_built_state_shares_nothing_with_a_later_rebuild(events: list[Event]) -> None:
    first = rebuild_with_head(events)[0]
    snapshot = iw.semantic(first)
    rebuild_with_head(events)  # a second Rebuild must not touch the first state
    rebuild_with_head(events[:3])
    assert iw.semantic(first) == snapshot


def test_the_rebuild_uses_the_same_checks_in_the_same_order(
    events: list[Event], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_live: list[Any] = []
    seen_built: list[Any] = []
    real = smod._replan

    def spy(into: list[Any]) -> Any:
        def replan(state: RuntimeState, transition: Any) -> Any:
            into.append(transition)
            return real(state, transition)

        return replan

    monkeypatch.setattr(smod, "_replan", spy(seen_live))
    _fold(events)
    monkeypatch.setattr(smod, "_replan", spy(seen_built))
    rebuild_with_head(events)
    assert seen_built == seen_live
    assert len(seen_built) >= len(events)


def test_a_failure_in_the_middle_leaves_no_partial_state(events: list[Event]) -> None:
    # A Step status change in the second half, forged to a status the rules did not give.
    at = next(
        i
        for i in range(len(events) // 2, len(events))
        if json.loads(dump_json(events[i]))["payload"]["transitions"][0]["entity"] == "step"
        and json.loads(dump_json(events[i]))["payload"]["transitions"][0]["before"] is not None
    )
    data = json.loads(dump_json(events[at]))
    after = data["payload"]["transitions"][0]["after"]
    after["status"] = "FAILED" if after["status"] != "FAILED" else "COMMITTED"
    bad = [*events[:at], Event.model_validate_json(json.dumps(data)), *events[at + 1 :]]
    with pytest.raises(StateRebuildError):
        rebuild_with_head(bad)
    # The same events without the bad one still build normally afterwards.
    assert rebuild_with_head(events)[0] == _fold(events)


def test_a_builder_cannot_be_used_after_it_is_frozen() -> None:
    builder = smod._Builder()
    builder.freeze()
    with pytest.raises(StateRebuildError):
        builder.freeze()
    with pytest.raises(StateRebuildError):
        builder.put("tasks", "x", 1)


def test_the_rebuild_never_copies_a_whole_map_per_transition(
    events: list[Event], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"with": 0, "replace": 0}
    real_with, real_replace = RuntimeState._with, RuntimeState._replace

    def counting_with(self: RuntimeState, *args: Any) -> RuntimeState:
        calls["with"] += 1
        return real_with(self, *args)

    def counting_replace(self: RuntimeState, **kwargs: Any) -> RuntimeState:
        calls["replace"] += 1
        return real_replace(self, **kwargs)

    monkeypatch.setattr(RuntimeState, "_with", counting_with)
    monkeypatch.setattr(RuntimeState, "_replace", counting_replace)
    rebuild_with_head(events)
    assert calls == {"with": 0, "replace": 0}
