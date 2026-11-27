"""P-2 operating limits in the CLI home (tests first).

The limits are optional configuration in the home's ``config.json``
(``limits``): without them the level is always NORMAL and only the usage is
shown. With them, ``status`` shows the level, the usage and the last start-up
time; at LIMITED a new ``start`` is refused before its Task or Run is created,
while ``status``, ``stop``, ``recover`` and ``reconcile`` go on. The level is
the same after a restart. The numbers here are test values, not proposals.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core_cli import app
from support import ledger_world as lw

BIG = 1 << 40


class _Home:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()

    def call(
        self, *argv: str, lines: tuple[str, ...] = ()
    ) -> tuple[int, list[dict[str, Any]], str]:
        out, err = io.StringIO(), io.StringIO()
        queue = list(lines)

        def reader() -> str:
            return queue.pop(0) + "\n" if queue else ""

        code = app.main(list(argv), stdout=out, stderr=err, reader=reader, clock=self.clock)
        events = [json.loads(x) for x in out.getvalue().splitlines() if x.startswith("{")]
        return code, events, err.getvalue()

    def shell(self, *lines: str) -> tuple[int, list[dict[str, Any]], str]:
        return self.call("shell", "--home", str(self.home), "--json", lines=lines)

    def status(self) -> dict[str, Any]:
        code, events, err = self.call("status", "--home", str(self.home), "--json")
        assert code == 0, err
        return next(e for e in events if e["kind"] == "status")

    def db(self, sql: str) -> list[Any]:
        conn = sqlite3.connect(f"file:{self.home / 'journal.db'}?mode=ro", uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def events(self) -> int:
        return int(self.db("SELECT COUNT(*) FROM events")[0][0])

    def configure(self, limits: Any) -> None:
        path = self.home / "config.json"
        config = json.loads(path.read_text())
        if limits is None:
            config.pop("limits", None)
        else:
            config["limits"] = limits
        path.chmod(0o600)
        path.write_text(json.dumps(config, sort_keys=True))


def _limits(events: int, *, warning: int | None = None, reserve: int = 0) -> dict[str, int]:
    return {
        "warning_events": warning if warning is not None else events,
        "limit_events": events,
        "warning_bytes": BIG,
        "limit_bytes": BIG,
        "reserve_events": reserve,
        "reserve_bytes": 0,
    }


@pytest.fixture
def home(tmp_path: Path) -> _Home:
    """A home with one Run waiting for its approval (so the journal has events)."""
    h = _Home(tmp_path)
    assert h.call("init", "--home", str(h.home))[0] == 0
    _code, events, err = h.shell("start report", "quit")
    assert any(e["kind"] == "approval_request" for e in events), err
    return h


def test_without_limits_the_usage_is_shown_and_nothing_is_limited(home: _Home) -> None:
    capacity = home.status()["capacity"]
    assert capacity["level"] == "NORMAL"
    assert capacity["limits"] is None
    assert capacity["events"] == home.events()
    assert capacity["bytes"] > 0
    _code, events, err = home.shell("start report", "quit")
    assert any(e["kind"] == "started" for e in events), err


def test_status_shows_the_level_and_the_start_up_time(home: _Home) -> None:
    home.configure(_limits(home.events() + 1000, warning=1))
    capacity = home.status()["capacity"]
    assert capacity["level"] == "WARNING"
    assert capacity["limits"]["limit_events"] == home.events() + 1000
    _code, events, _err = home.shell("status", "quit")
    shown = next(e for e in events if e["kind"] == "status")["capacity"]
    assert isinstance(shown["startup_ms"], int)
    assert shown["startup_ms"] >= 0


def test_at_the_limit_a_start_is_refused_before_anything_is_created(home: _Home) -> None:
    home.configure(_limits(home.events()))
    before = (home.events(), home.db("SELECT COUNT(*) FROM rt_tasks")[0][0])
    code, events, err = home.shell("start report", "quit")
    assert code == 0, err
    errors = [e for e in events if e["kind"] == "error"]
    assert [e["code"] for e in errors] == ["CAPACITY_LIMITED"]
    assert (home.events(), home.db("SELECT COUNT(*) FROM rt_tasks")[0][0]) == before
    assert home.status()["capacity"]["level"] == "LIMITED"


def test_a_start_that_would_cross_the_limit_is_refused_as_a_whole(home: _Home) -> None:
    # Room for the Task's first event but not for the whole start.
    home.configure(_limits(home.events() + 3))
    before = (home.events(), home.db("SELECT COUNT(*) FROM rt_tasks")[0][0])
    _code, events, _err = home.shell("start report", "quit")
    assert [e["code"] for e in events if e["kind"] == "error"] == ["CAPACITY_LIMITED"]
    assert (home.events(), home.db("SELECT COUNT(*) FROM rt_tasks")[0][0]) == before


def test_existing_work_is_stopped_and_recovered_under_the_limit(home: _Home) -> None:
    home.configure(_limits(home.events()))
    assert home.status()["capacity"]["level"] == "LIMITED"  # after the restart, too
    holds = home.db("SELECT reservation_id, state, hold_micro FROM reservations ORDER BY 1")
    _code, events, err = home.shell("recover all", "quit")
    assert [e["after"] for e in events if e["kind"] == "run_recovered"] == ["FAILED"], err
    assert not [e for e in events if e["kind"] == "error"], events
    _code, events, err = home.shell("stop tenant", "quit")
    assert not [e for e in events if e["kind"] == "error"], (events, err)
    assert home.db("SELECT COUNT(*) FROM stop_state")[0][0] >= 1
    # The limit itself released nothing: holds change only by the recovery's own rules.
    after = home.db("SELECT reservation_id, state, hold_micro FROM reservations ORDER BY 1")
    assert [r[0] for r in after] == [r[0] for r in holds]


def test_the_limited_level_stays_after_a_restart(home: _Home) -> None:
    home.configure(_limits(home.events()))
    for _ in range(2):
        assert home.status()["capacity"]["level"] == "LIMITED"
        _code, events, _err = home.shell("start report", "quit")
        assert [e["code"] for e in events if e["kind"] == "error"] == ["CAPACITY_LIMITED"]


@pytest.mark.parametrize(
    "limits",
    [
        {"warning_events": 5, "limit_events": 1, "warning_bytes": 1, "limit_bytes": 2},
        {"warning_events": "5"},
        "many",
    ],
)
def test_malformed_limits_are_a_malformed_home(home: _Home, limits: Any) -> None:
    home.configure(limits)
    code, _events, err = home.call("status", "--home", str(home.home))
    assert code != 0
    assert "HOME_UNSAFE" in err


def test_a_step_refused_inside_the_loop_ends_its_turn_cleanly(home: _Home) -> None:
    # Room for the whole start (its 4 events) and one more, then no next Step.
    home.configure(_limits(home.events() + 5))
    code, events, err = home.shell("start report", "status", "quit")
    assert code == app.EXIT_WAITING, err  # the Run waits; it is not ended
    assert not [e for e in events if e["kind"] == "error"], events
    outcome = next(e for e in events if e["kind"] == "outcome")
    assert outcome["reason"] == "CAPACITY_LIMITED"
    assert outcome["run_status"] == "RUNNING"  # nothing ended or rolled back by the limit
    # The owner can still stop it.
    _code, events, err = home.shell("recover all", "stop tenant", "quit")
    assert not [e for e in events if e["kind"] == "error"], (events, err)
