"""P-6 compatibility fix: a Run whose grant fails unexpectedly never goes on (tests first).

A grant failure that can be seen in advance is refused before the Task and the
Run are created (``test_p6_compatibility.py``). Here the grant installation
fails after they were created (an unexpected failure, injected): the Run is
ended FAILED at once, so it never runs without its authority; if even that
fails, it is left for the owner's recovery, which ends it. No grant, no hold.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.types.errors import SafetyError
from agent_core_cli import app
from agent_core_cli import environment as env
from support import ledger_world as lw


class _Home:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()

    def shell(self, *lines: str) -> tuple[int, list[dict[str, Any]], str]:
        out, err = io.StringIO(), io.StringIO()
        queue = [*lines]

        def reader() -> str:
            return queue.pop(0) + "\n" if queue else ""

        code = app.main(
            ["shell", "--home", str(self.home), "--json"],
            stdout=out,
            stderr=err,
            reader=reader,
            clock=self.clock,
        )
        events = [json.loads(x) for x in out.getvalue().splitlines() if x.startswith("{")]
        return code, events, err.getvalue()

    def db(self, sql: str) -> list[Any]:
        conn = sqlite3.connect(f"file:{self.home / 'journal.db'}?mode=ro", uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


@pytest.fixture
def home(tmp_path: Path) -> _Home:
    h = _Home(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    assert app.main(["init", "--home", str(h.home)], stdout=out, stderr=err, clock=h.clock) == 0
    return h


def _refuse(*_args: Any, **_kwargs: Any) -> None:
    raise SafetyError("SYNTHETIC_GRANT_FAILURE")


def test_a_grant_failure_after_the_run_was_created_ends_the_run(
    home: _Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env.Session, "_install_run", _refuse)
    code, events, err = home.shell("start report", "quit")
    assert code == 0, err
    errors = [e for e in events if e["kind"] == "error"]
    assert [(e["code"], e["detail"]) for e in errors] == [
        ("RUN_NOT_AUTHORIZED", "GRANT_NOT_INSTALLED")
    ]
    assert home.db("SELECT status FROM rt_runs") == [("FAILED",)]
    assert home.db("SELECT COUNT(*) FROM run_grants")[0][0] == 0
    assert home.db("SELECT COUNT(*) FROM reservations")[0][0] == 0


def test_if_ending_the_run_fails_too_it_is_left_for_recovery(
    home: _Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env.Session, "_install_run", _refuse)

    def no_end(self: Any, *_args: Any, **_kwargs: Any) -> None:
        raise SafetyError("SYNTHETIC_END_FAILURE")

    monkeypatch.setattr(env.Session, "_end_unauthorized", no_end)
    _code, events, _err = home.shell("start report", "quit")
    errors = [e for e in events if e["kind"] == "error"]
    assert [(e["code"], e["detail"]) for e in errors] == [
        ("RUN_NOT_AUTHORIZED", "RUN_LEFT_FOR_RECOVERY")
    ]
    assert home.db("SELECT COUNT(*) FROM run_grants")[0][0] == 0
    monkeypatch.undo()
    _code, events, err = home.shell("recover all", "quit")
    assert [e["after"] for e in events if e["kind"] == "run_recovered"] == ["FAILED"], err
    assert home.db("SELECT status FROM rt_runs") == [("FAILED",)]
