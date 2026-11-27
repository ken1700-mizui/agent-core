"""P-2 remaining conditions (tests first).

- A new home starts with the owner's provisional limits (2026-11-27), never
  without a limit. An existing home is never rewritten implicitly: the owner
  adds the limits with ``limits --apply-provisional``.
- The start-up time (opening the journal and the Rebuild) is a third quantity:
  a start at or over the start-up limit limits new work in that session. Each
  session's start-up time is recorded in the home (``startup.json``, replaced
  atomically); a later start is measured again (a faster start is not limited
  by the start-up time). A home without the record (an older home) is valid.
- A new Attempt is new work too: refused at the limit (also inside an existing
  Step); finishing existing Attempts, stop, cancel, reconcile and recovery are not.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime.capacity import CapacityGate
from agent_core.types.capacity import (
    CapacityLevel,
    CapacityLimits,
    CapacityUsage,
    NewWork,
    capacity_level,
)
from agent_core.types.errors import CapacityLimitedError
from agent_core.types.records import AttemptState
from agent_core_cli import app
from agent_core_cli import environment as env
from support import ledger_world as lw
from support import runtime_world as w

L = CapacityLevel
PROVISIONAL = {
    "warning_events": 30_000,
    "limit_events": 50_000,
    "warning_bytes": 100_663_296,
    "limit_bytes": 167_772_160,
    "reserve_events": 200,
    "reserve_bytes": 1_048_576,
    "warning_startup_ms": 30_000,
    "limit_startup_ms": 60_000,
}


# ---------------------------------------------------------------- start-up time in the level


def _limits(**extra: int) -> CapacityLimits:
    return CapacityLimits(**{**PROVISIONAL, **extra})


@pytest.mark.parametrize(
    ("startup", "level"),
    [(None, L.NORMAL), (29_999, L.NORMAL), (30_000, L.WARNING), (60_000, L.LIMITED)],
)
def test_the_start_up_time_is_part_of_the_level(startup: int | None, level: L) -> None:
    usage = CapacityUsage(events=0, bytes=0, startup_ms=startup)
    assert capacity_level(_limits(), usage) is level


def test_start_up_thresholds_out_of_order_are_refused() -> None:
    with pytest.raises(ValueError, match="startup"):
        _limits(warning_startup_ms=70_000)


def test_the_provisional_values_are_the_approved_ones() -> None:
    assert env.PROVISIONAL_LIMITS.model_dump() == PROVISIONAL


# ---------------------------------------------------------------- new Attempts


def test_a_new_attempt_is_refused_at_the_limit_and_existing_ones_go_on() -> None:
    from support.fakes import InMemoryJournal

    class Probe:
        events = 0

        def capacity_usage(self) -> CapacityUsage:
            return CapacityUsage(events=self.events, bytes=0)

    probe = Probe()
    journal = InMemoryJournal()
    sm = w.machine(journal)
    sm._capacity = CapacityGate(  # the gate as the CLI sets it, here with a probe
        CapacityLimits(warning_events=1, limit_events=100, warning_bytes=1, limit_bytes=1 << 40),
        probe,
    )
    run = w.running_run(sm)
    step = w.step_in_progress(sm, run)
    first = sm.create_attempt(step)
    probe.events = 100  # now at the limit
    with pytest.raises(CapacityLimitedError) as refused:
        sm.create_attempt(sm.state.steps[step.step_id])
    assert refused.value.work == NewWork.ATTEMPT.value
    # The existing Attempt goes on to its end.
    first = sm.advance_attempt(
        first,
        AttemptState.ABORTED_BEFORE_DISPATCH,
        w.evidence_for(first, AttemptState.ABORTED_BEFORE_DISPATCH),
    )
    assert first.state is AttemptState.ABORTED_BEFORE_DISPATCH


# ---------------------------------------------------------------- the CLI home


class _Home:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()

    def call(
        self, *argv: str, lines: tuple[str, ...] = (), monotonic: Any = None
    ) -> tuple[int, list[dict[str, Any]], str]:
        out, err = io.StringIO(), io.StringIO()
        queue = list(lines)

        def reader() -> str:
            return queue.pop(0) + "\n" if queue else ""

        code = app.main(
            list(argv), stdout=out, stderr=err, reader=reader, clock=self.clock, monotonic=monotonic
        )
        events = [json.loads(x) for x in out.getvalue().splitlines() if x.startswith("{")]
        return code, events, err.getvalue()

    def shell(self, *lines: str, monotonic: Any = None) -> tuple[int, list[dict[str, Any]], str]:
        return self.call(
            "shell", "--home", str(self.home), "--json", lines=lines, monotonic=monotonic
        )

    def config(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.home / "config.json").read_text())
        return data

    def strip_limits(self) -> None:
        """Make the home look as an earlier version wrote it (no limits)."""
        path = self.home / "config.json"
        config = self.config()
        config.pop("limits")
        path.chmod(0o600)
        path.write_text(json.dumps(config, sort_keys=True))

    def events(self) -> int:
        conn = sqlite3.connect(f"file:{self.home / 'journal.db'}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            conn.close()


@pytest.fixture
def home(tmp_path: Path) -> _Home:
    h = _Home(tmp_path)
    assert h.call("init", "--home", str(h.home))[0] == 0
    return h


def test_a_new_home_starts_with_the_provisional_limits(home: _Home) -> None:
    assert home.config()["limits"] == PROVISIONAL
    _code, events, _err = home.shell("status", "quit")
    capacity = next(e for e in events if e["kind"] == "status")["capacity"]
    assert capacity["limits"] == PROVISIONAL
    assert capacity["level"] == "NORMAL"


def test_an_existing_home_is_never_rewritten_implicitly(home: _Home) -> None:
    home.strip_limits()
    before = (home.home / "config.json").read_bytes()
    for _ in range(2):
        assert home.call("status", "--home", str(home.home))[0] == 0
        home.shell("status", "quit")
    assert (home.home / "config.json").read_bytes() == before


def test_the_owner_adds_the_limits_explicitly(home: _Home) -> None:
    home.strip_limits()
    old = home.config()
    code, _events, err = home.call("limits", "--home", str(home.home), "--apply-provisional")
    assert code == 0, err
    new = home.config()
    assert new.pop("limits") == PROVISIONAL
    assert new == old  # nothing else changed
    code, _events, err = home.call("limits", "--home", str(home.home), "--apply-provisional")
    assert code != 0
    assert "LIMITS_ALREADY_SET" in err  # never overwritten


def _slow(step_ms: int) -> Any:
    now = [0]

    def monotonic() -> int:
        now[0] += step_ms
        return now[0]

    return monotonic


def test_a_slow_start_limits_new_work_and_is_recorded(home: _Home) -> None:
    before = home.events()
    _code, events, err = home.shell("start report", "quit", monotonic=_slow(61_000))
    assert [e["code"] for e in events if e["kind"] == "error"] == ["CAPACITY_LIMITED"], err
    assert home.events() == before
    record = json.loads((home.home / "startup.json").read_text())
    assert record["startup_ms"] >= 60_000
    # The record is shown by status (read only); a later, normal start is measured again.
    _code, events, _err = home.call("status", "--home", str(home.home), "--json")
    capacity = next(e for e in events if e["kind"] == "status")["capacity"]
    assert capacity["recorded_startup_ms"] == record["startup_ms"]
    _code, events, err = home.shell("start report", "quit")
    assert any(e["kind"] == "started" for e in events), err


def test_a_home_without_a_start_up_record_is_valid(home: _Home) -> None:
    assert not (home.home / "startup.json").exists()
    assert home.call("status", "--home", str(home.home))[0] == 0


def test_a_damaged_start_up_record_is_refused(home: _Home) -> None:
    home.shell("status", "quit")
    path = home.home / "startup.json"
    path.write_text("not json")
    code, _events, err = home.call("status", "--home", str(home.home))
    assert code != 0
    assert "HOME_UNSAFE" in err


def test_a_refused_attempt_inside_the_loop_ends_its_step(home: _Home) -> None:
    _code, events, err = home.shell("start report", "quit")  # a first Run, waiting
    path = home.home / "config.json"
    config = home.config()
    # Room for the start (4 events), the Step and its IN_PROGRESS, not its Attempt.
    config["limits"] = {
        **PROVISIONAL,
        "limit_events": home.events() + 6,
        "warning_events": 1,
        "reserve_events": 0,
    }
    path.chmod(0o600)
    path.write_text(json.dumps(config, sort_keys=True))
    _code, events, err = home.shell("start report", "quit")
    assert not [e for e in events if e["kind"] == "error"], (events, err)
    outcome = next(e for e in events if e["kind"] == "outcome")
    run_id = next(e for e in events if e["kind"] == "started")["run_id"]
    assert outcome["reason"] == "CAPACITY_LIMITED"
    assert outcome["run_status"] == "RUNNING"
    snapshot = env.open_snapshot(home.home, clock=home.clock)
    try:
        open_steps = [
            s
            for s in snapshot.sm.state.steps.values()
            if str(s.run_id) == run_id and s.status.value == "IN_PROGRESS"
        ]
        assert snapshot.sm.state.run_steps[run_id]  # its Step was created, then ended
    finally:
        snapshot.close()
    assert open_steps == []  # the refused Attempt's Step was ended, not left open
