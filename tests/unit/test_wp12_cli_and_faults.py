"""WP-12 stage 3: recovery through the CLI, fault injection, and real process
kills (SIGKILL) at crash points.

Two kinds of fault, reported apart:
- exception injection (in-process): a failure is raised at a chosen point;
- real process stop: a child CLI process is killed with SIGKILL at a chosen
  point (``tests/support/recovery_child.py``), then a new process recovers.
"""

from __future__ import annotations

import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.types.model import ModelRequest, ModelResponse
from agent_core_cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "recovery_child.py"
TIMEOUT = 60.0


class Env:
    def __init__(self, home: Path) -> None:
        self.home = home

    def call(
        self, *argv: str, lines: list[str] | None = None, **hooks: Any
    ) -> tuple[int, list[dict[str, Any]], str]:
        out, err = io.StringIO(), io.StringIO()
        feed = list(lines or [])

        def reader() -> str:
            if not feed:
                return ""
            line = feed.pop(0)
            if callable(line):
                line = line(out.getvalue())
            return line + "\n"

        code = app.main(
            list(argv),
            stdout=out,
            stderr=err,
            reader=reader,
            clock=None,  # the real clock: the child processes use it too
            monotonic=lambda: 0,
            hooks=app.Hooks(**hooks) if hooks else None,
        )
        events = [json.loads(x) for x in out.getvalue().splitlines() if x.startswith("{")]
        return code, events, err.getvalue()

    def shell(self, *lines: Any, **hooks: Any) -> tuple[int, list[dict[str, Any]], str]:
        return self.call("shell", "--home", str(self.home), "--json", lines=list(lines), **hooks)


def kinds(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["kind"] == kind]


def _approve(out: str) -> str:
    hashes = [json.loads(x)["action_hash"] for x in out.splitlines() if '"approval_request"' in x]
    return f"approve {hashes[-1]}"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(tmp_path / "home")
    assert e.call("init", "--home", str(e.home))[0] == 0
    return e


def _dump(home: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{home / 'journal.db'}?mode=ro", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _target(home: Path) -> Path:
    return home / "area" / "tree" / "workspace" / "out" / "report-1.txt"


# ================================================================ the CLI


def test_the_recovery_view_starts_nothing(env: Env) -> None:
    env.shell("start report")  # the session ends while the Run waits
    before = _dump(env.home)
    code, events, _ = env.call("recovery", "--home", str(env.home), "--json")
    assert code == 0
    [view] = kinds(events, "recovery")
    [run] = view["runs"]
    assert (run["status"], run["resumable"]) == ("WAITING_APPROVAL", False)
    assert run["attempts"][0]["action"] == "ABORT"
    assert _dump(env.home) == before  # looking changes nothing


def test_an_explicit_recovery_ends_a_run_that_cannot_continue(env: Env) -> None:
    env.shell("start report")
    code, events, _ = env.shell("recover all", "recover all", "status", "quit")
    first, second = kinds(events, "recovered")
    assert (first["changed"], second["changed"]) == (True, False)  # idempotent
    [moved] = kinds(events, "run_recovered")[:1]
    assert (moved["before"], moved["after"]) == ("WAITING_APPROVAL", "FAILED")
    assert not _target(env.home).exists()
    assert code == 1  # the Run ended FAILED (RECOVERY_FAILED)


def test_an_unknown_outcome_through_the_cli(env: Env) -> None:
    class Interrupt:
        def __init__(self, real: Any) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise KeyboardInterrupt

    assert env.shell("start report", model_wrapper=Interrupt)[0] == 130
    code, events, _ = env.shell("recovery", "recover all", "recovery", "quit")
    views = kinds(events, "recovery")
    [unknown] = views[-1]["unknown"]
    assert unknown["hold_micro"] > 0
    attempt = unknown["attempt_id"]
    run_id = unknown["run_id"]
    code, events, _ = env.shell(
        f"reconcile {attempt}", f"declare-unresolved {run_id}", f"budget {run_id}", "quit"
    )
    assert kinds(events, "reconciled")[0]["outcome"] == "STILL_UNKNOWN"
    assert kinds(events, "run_recovered")[0]["after"] == "FAILED"
    [held] = kinds(events, "budget")[0]["unsettled"]
    assert held["reservation_state"] == "UNKNOWN"  # the hold is kept
    assert code == 1


def test_recovery_refusals_have_fixed_codes(env: Env) -> None:
    env.shell("start report")
    _, events, _ = env.shell(
        "reconcile att_nope",
        "declare-unresolved last",
        "recover x y",
        "recover-temporaries",
        "quit",
    )
    codes = [e["code"] for e in kinds(events, "error")]
    assert codes == ["ATTEMPT_NOT_FOUND", "NOTHING_UNRESOLVED", "USAGE_INVALID_ARGUMENTS"]
    [temps] = kinds(events, "temporaries")
    assert temps["removed"] == []


# ================================================================ exception injection


def test_injected_commit_outcome_unknown_is_never_taken_as_not_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal commits the start, then the commit reports a failure: the
    caller does not know; the recorded start decides (never "not executed")."""
    real = SqliteJournal._commit
    state = {"armed": True}

    def commit_then_fail(self: Any) -> None:
        real(self)
        if state["armed"] and _has_started(self):
            state["armed"] = False
            raise sqlite3.OperationalError("synthetic: commit outcome unknown")

    def _has_started(journal: Any) -> bool:
        conn = journal._require_open()
        return bool(
            conn.execute("SELECT 1 FROM rt_attempts WHERE state = 'DISPATCH_STARTED'").fetchone()
        )

    monkeypatch.setattr(SqliteJournal, "_commit", commit_then_fail)
    env.shell("start report", "quit")
    monkeypatch.undo()
    _, events, _ = env.shell("recover all", "recovery", "quit")
    [view] = kinds(events, "recovery")[-1:]
    assert view["unknown"], "the started attempt must stay unknown"
    assert kinds(events, "run_recovered")[0]["after"] == "RECONCILIATION_REQUIRED"


# ================================================================ real process stops (SIGKILL)


def _kill_at(home: Path, point: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests"))
    with subprocess.Popen(
        [sys.executable, str(CHILD), str(home), point],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    ) as child:
        assert child.stdout is not None
        deadline = time.monotonic() + TIMEOUT
        line = ""
        while time.monotonic() < deadline and line != "ready":
            line = child.stdout.readline().strip()
            if not line and child.poll() is not None:
                pytest.fail(f"child ended early: {child.stderr.read() if child.stderr else ''}")
        assert line == "ready"
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=TIMEOUT)


def _recover(env: Env) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, events, err = env.shell("recover all", "recovery", "status", "quit")
    assert not kinds(events, "error"), err
    return kinds(events, "recovery")[-1], kinds(events, "status")[-1]["tasks"][0]["runs"]


@pytest.mark.parametrize(
    ("point", "run_status", "unknown"),
    [
        ("model_call", "RECONCILIATION_REQUIRED", True),
        ("file_prepare", "FAILED", False),
        ("file_write", "RECONCILIATION_REQUIRED", True),
        ("before_save", "RECONCILIATION_REQUIRED", True),
    ],
)
def test_a_killed_process_is_recovered(
    env: Env, point: str, run_status: str, unknown: bool
) -> None:
    _kill_at(env.home, point)
    view, runs = _recover(env)
    assert runs[0]["status"] == run_status
    assert bool(view["unknown"]) is unknown
    if unknown:
        # Kept UNKNOWN, never released (a file tool's reservation is its manifest
        # estimate, 0 here; a model call holds its required amount).
        assert all(u["reservation_state"] == "UNKNOWN" for u in view["unknown"])
        if point in ("model_call", "before_save"):
            assert all(u["hold_micro"] > 0 for u in view["unknown"])
    assert not _target(env.home).exists()  # nothing was re-run to finish it


def test_a_process_killed_during_the_recovery(env: Env) -> None:
    env.shell("start report")  # a Run that needs recovery
    _kill_at(env.home, "during_recovery")
    view, runs = _recover(env)  # a new process continues the recovery
    assert runs[0]["status"] == "FAILED"
    assert view["runs"] == []
    _, events, _ = env.shell("recover all", "quit")
    assert kinds(events, "recovered")[0]["changed"] is False


def _reconciliations(home: Path) -> int:
    conn = sqlite3.connect(f"file:{home / 'journal.db'}?mode=ro", uri=True)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM ledger_entries WHERE kind = 'RECONCILIATION'"
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_a_process_killed_while_settling_an_unknown_outcome(env: Env) -> None:
    """QR-01: SIGKILL inside the transaction that settles an unknown outcome and
    its cost together (``ReconcileOutcome``): neither is committed."""
    _kill_at(env.home, "file_prepare")  # the permit consumed, the start never recorded
    _kill_at(env.home, "during_settle")  # proving it never started, before the commit
    _, events, err = env.shell("recovery", "quit")
    view = kinds(events, "recovery")[-1]
    assert [u["reservation_state"] for u in view["unknown"]] == ["UNKNOWN"], err
    assert _reconciliations(env.home) == 0
    view, runs = _recover(env)  # a new process settles it, once
    assert runs[0]["status"] == "FAILED"
    assert view["unknown"] == []
    assert _reconciliations(env.home) == 1
    _, events, _ = env.shell("recover all", "quit")
    assert kinds(events, "recovered")[0]["changed"] is False
    assert _reconciliations(env.home) == 1


def test_the_cli_never_recovers_a_run_of_its_own_live_session(env: Env) -> None:
    def recover_current(out: str) -> str:
        run_id = [json.loads(x) for x in out.splitlines() if '"started"' in x][-1]["run_id"]
        return f"recover {run_id}"

    code, events, _ = env.shell("start report", recover_current, "recover all", "pending", "quit")
    assert [e["code"] for e in kinds(events, "error")] == ["RUN_ACTIVE"]
    assert kinds(events, "recovered")[0]["changed"] is False  # "all" skips the live Run
    assert len(kinds(events, "pending")[0]["items"]) == 1  # still waiting for its approval
    assert code == 3
