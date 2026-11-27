"""WP-13: the final integration of Phase 1 (synthetic data, the Fake model).

Each scenario runs the real composition: the CLI (in-process or as a child
process), the journal and ledger, the runtime, the execution gate, the Context
Engine, the agent loop and the file broker. These are integration and
characterization tests: where a scenario passes on the first run it is reported
as a characterization, not as a confirmed red. The map of every Phase 1
invariant to its tests is in ``docs/design/phase-1/WP-13_INTEGRATION.md``.
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
from agent_core.adapters.system import SystemClock
from agent_core.runtime.state_machine import rebuild_with_head
from agent_core.types.cost import BudgetScopeType, CounterKind
from agent_core.types.ledger import InstallPolicy, RevokeAccess
from agent_core.types.model import ModelRequest, ModelResponse, ToolUseBlock
from agent_core.types.transitions import RunTransition
from agent_core_cli import app, environment

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "recovery_child.py"
TIMEOUT = 60.0


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests"))
    return env


def _cli(*argv: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_core_cli", *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env=_env(),
        timeout=TIMEOUT,
        check=False,
    )


def _events(text: str) -> list[dict[str, Any]]:
    return [json.loads(x) for x in text.splitlines() if x.startswith("{")]


def _kinds(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["kind"] == kind]


def _shell(home: Path, *lines: Any, **hooks: Any) -> tuple[int, list[dict[str, Any]]]:
    out, err = io.StringIO(), io.StringIO()
    feed = list(lines)

    def reader() -> str:
        if not feed:
            return ""
        line = feed.pop(0)
        text: str = line(out.getvalue()) if callable(line) else line
        return text + "\n"

    code = app.main(
        ["shell", "--home", str(home), "--json"],
        stdout=out,
        stderr=err,
        reader=reader,
        hooks=app.Hooks(**hooks) if hooks else None,
    )
    return code, _events(out.getvalue())


def approve(out: str) -> str:
    hashes = [e["action_hash"] for e in _events(out) if e["kind"] == "approval_request"]
    return f"approve {hashes[-1]}"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    assert _cli("init", "--home", str(path)).returncode == 0
    return path


def _dump(home: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{home / 'journal.db'}?mode=ro", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _target(home: Path, n: int = 1) -> Path:
    return home / "area" / "tree" / "workspace" / "out" / f"report-{n}.txt"


def _journal_runs(home: Path) -> list[Any]:
    journal = SqliteJournal.open_read_only(home / "journal.db", clock=SystemClock())
    try:
        events: list[Any] = []
        config = environment.HomeConfig.load(environment.Home(home))
        while True:
            page = journal.read_tenant(config.tenant_id, after_tenant_seq=len(events), limit=1000)
            if not page:
                return events
            events.extend(page)
    finally:
        journal.close()


# ================================================================ I1 normal work (child process)


def test_i1_normal_work_through_the_cli(home: Path) -> None:
    with subprocess.Popen(
        [sys.executable, "-m", "agent_core_cli", "shell", "--home", str(home), "--json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    ) as cli:
        assert cli.stdin is not None
        assert cli.stdout is not None
        cli.stdin.write("start report\n")
        cli.stdin.flush()
        seen = ""
        while '"approval_request"' not in seen:
            seen += cli.stdout.readline()
        cli.stdin.write(approve(seen) + "\nbudget\nsteps\nquit\n")
        cli.stdin.flush()
        rest, _ = cli.communicate(timeout=TIMEOUT)
        assert cli.returncode == 0
    events = _events(seen + rest)
    assert _kinds(events, "outcome")[-1]["run_status"] == "COMPLETED"
    assert _target(home).read_text() == "synthetic report v1\n"  # the real artifact
    [budget] = _kinds(events, "budget")
    run_scope = next(s for s in budget["scopes"] if s["scope"] == "RUN")
    assert (run_scope["spent_micro"], run_scope["hold_micro"]) == (320, 0)  # 180 + 140
    assert budget["unsettled"] == []
    statuses = [
        t.after.status.value
        for e in _journal_runs(home)
        if e.payload is not None
        for t in e.payload.transitions
        if isinstance(t, RunTransition)
    ]
    assert statuses == [
        "CREATED",
        "QUEUED",
        "RUNNING",
        "WAITING_APPROVAL",
        "RUNNING",
        "VERIFYING",
        "COMPLETED",
    ]
    [steps] = _kinds(events, "steps")
    assert [s["kind"] for s in steps["steps"]] == [
        "MODEL_CALL",
        "TOOL_CALL",
        "MODEL_CALL",
        "VERIFICATION",
        "VERIFICATION",
    ]


# ================================================================ I2 the repair loop and its limits


def test_i2_repair_and_limits(home: Path) -> None:
    code, events = _shell(home, "start repair", approve, approve, "budget", "quit")
    assert code == 0
    final = _kinds(events, "outcome")[-1]
    assert (final["run_status"], final["repairs"], final["tool_calls"]) == ("COMPLETED", 1, 2)
    assert len(_kinds(events, "approval_request")) == 2  # a new approval for the new action
    assert _kinds(events, "budget")[0]["scopes"][0]["spent_micro"] == 640
    _, events = _shell(home, "start falseclaim", "quit")
    final = _kinds(events, "outcome")[-1]
    assert (final["run_status"], final["reason"], final["repairs"]) == ("FAILED", "REPAIR_LIMIT", 3)
    assert final["model_calls"] == 4  # within the explicit limit (10)


# ================================================================ I3 authority


def _waiting_session(home: Path) -> tuple[Any, Any]:
    session = environment.open_session(home, clock=SystemClock(), monotonic=lambda: 0)
    _task, _run, outcome = session.start("report")
    return session, outcome


def test_i3_a_policy_change_invalidates_the_waiting_action(home: Path) -> None:
    session, outcome = _waiting_session(home)
    try:
        policy = session.journal.policy(str(session.config.tenant_id))
        assert policy is not None
        session._owner_ops(
            InstallPolicy(
                op="install_policy", policy=policy.model_copy(update={"policy_version": "policy-2"})
            )
        )
        result = session.approve(outcome.pending_action_hash)
        assert result.status.value != "COMPLETED"
    finally:
        session.close()
    assert not _target(home).exists()


def test_i3_a_revocation_invalidates_the_waiting_action(home: Path) -> None:
    session, outcome = _waiting_session(home)
    try:
        session._owner_ops(RevokeAccess(op="revoke_access", tenant_id=session.config.tenant_id))
        result = session.approve(outcome.pending_action_hash)
        assert result.status.value != "COMPLETED"
    finally:
        session.close()
    assert not _target(home).exists()


# ================================================================ I4 costs survive a restart


def test_i4_costs_and_counts_are_kept_by_a_new_runtime(home: Path) -> None:
    _shell(home, "start report", approve, "quit")
    first = _kinds(_events(_cli("status", "--home", str(home), "--json").stdout), "status")[0]
    run = first["tasks"][0]["runs"][0]
    session = environment.open_session(home, clock=SystemClock(), monotonic=lambda: 0)
    try:
        usage = session.journal.scope_usage(BudgetScopeType.RUN, run["run_id"])
        calls = session.journal.counter(BudgetScopeType.RUN, run["run_id"], CounterKind.MODEL_CALLS)
        assert (usage.spent_micro, usage.hold_micro) == (run["spent_micro"], 0) == (320, 0)
        assert calls is not None
        assert calls.used == 2
    finally:
        session.close()


# ================================================================ I5 kills at the remaining points


def _kill_at(home: Path, point: str) -> None:
    with subprocess.Popen(
        [sys.executable, str(CHILD), str(home), point],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
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


def _recover(home: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    _, events = _shell(home, "recover all", "recovery", "status", "quit")
    assert not _kinds(events, "error"), events
    return _kinds(events, "recovery")[-1], _kinds(events, "status")[-1]["tasks"][0]["runs"][0]


def test_i5_killed_after_the_reservation(home: Path) -> None:
    _kill_at(home, "after_reserve")
    view, run = _recover(home)
    assert run["status"] == "FAILED"  # nothing ran; the reservation released
    assert view["unknown"] == []
    assert run["hold_micro"] == 0


def test_i5_killed_right_after_the_file_was_committed(home: Path) -> None:
    """The rename is done, the journal does not know: the outcome stays unknown;
    the file that matches what was expected is not taken as proof, and nothing is
    run again."""
    _kill_at(home, "file_committed")
    assert _target(home).read_text() == "synthetic report v1\n"  # it was committed
    view, run = _recover(home)
    assert run["status"] == "RECONCILIATION_REQUIRED"
    [unknown] = view["unknown"]
    assert unknown["reservation_state"] == "UNKNOWN"
    _, events = _shell(home, f"reconcile {unknown['attempt_id']}", "quit")
    assert _kinds(events, "reconciled")[0]["outcome"] == "STILL_UNKNOWN"
    _, events = _shell(home, "recover all", "quit")
    assert _kinds(events, "recovered")[0]["changed"] is False  # no re-run


def test_i5_killed_during_the_projection_repair(home: Path) -> None:
    _shell(home, "start report", approve, "quit")
    conn = sqlite3.connect(home / "journal.db")
    conn.execute("DELETE FROM rt_attempts")
    conn.commit()
    conn.close()
    damaged = _dump(home)
    _kill_at(home, "during_repair")
    assert _dump(home) == damaged  # the repair's transaction never committed
    repaired = SqliteJournal.repair_projections(home / "journal.db", clock=SystemClock())
    assert repaired == ("rt_attempts",)
    assert SqliteJournal.repair_projections(home / "journal.db", clock=SystemClock()) == ()
    assert _cli("status", "--home", str(home)).returncode == 0


# ================================================================ I6 Rebuild


def test_i6_the_journal_rebuilds_the_same_state_without_writing(home: Path) -> None:
    _shell(home, "start repair", approve, approve, "quit")
    before = _dump(home)
    events = _journal_runs(home)
    assert rebuild_with_head(events) == rebuild_with_head(events)
    assert _dump(home) == before


# ================================================================ I8 a synthetic secret marker


SEEN: list[str] = []


def test_i8_a_secret_marker_reaches_no_output(home: Path) -> None:
    marker = "SYNTHETIC-SECRET-" + os.urandom(8).hex()
    readme = home / "area" / "tree" / "workspace" / "README.txt"
    readme.chmod(0o600)
    readme.write_text(f"synthetic workspace {marker}\n")

    class ReadFirst:
        """The model first reads the marked file (its content enters the context)."""

        def __init__(self, real: Any) -> None:
            self.real = real
            self.first = True

        def complete(self, request: ModelRequest) -> ModelResponse:
            SEEN.append(
                "\n".join(getattr(b, "text", "") for m in request.messages for b in m.content)
            )
            if not self.first:
                answer: ModelResponse = self.real.complete(request)
                return answer
            self.first = False
            from agent_core.adapters.fake_model import (
                FakeModelProvider,
                FakeScript,
                request_fingerprint,
            )
            from agent_core_cli import demo

            reply = demo._write("x", "workspace/README.txt", "absent", "r0").model_copy(
                update={
                    "content": (
                        ToolUseBlock(
                            type="tool_use",
                            id="r0",
                            name="read_file",
                            arguments='{"path":"workspace/README.txt"}',
                        ),
                    )
                }
            )
            script = FakeScript.of({request_fingerprint(request): (reply,)})
            return FakeModelProvider(
                profile=demo.MODEL_PROFILE, script=script, price=demo.FAKE_PRICE
            ).complete(request)

    out, err = io.StringIO(), io.StringIO()
    feed = ["start report", "status", "budget", "pending", "recovery", "frobnicate", "quit"]
    app.main(
        ["shell", "--home", str(home), "--json"],
        stdout=out,
        stderr=err,
        reader=lambda: feed.pop(0) + "\n" if feed else "",
        hooks=app.Hooks(model_wrapper=ReadFirst),
    )
    shown = out.getvalue() + err.getvalue()
    assert marker not in shown  # CLI output, errors, the approval screen
    conn = sqlite3.connect(f"file:{home / 'journal.db'}?mode=ro", uri=True)
    try:
        audit = " ".join(str(r) for r in conn.execute("SELECT * FROM audit_log"))
    finally:
        conn.close()
    assert marker not in audit  # the audit log
    # Not vacuous: the marker did reach the model's context (a later request).
    assert any(marker in text for text in SEEN)
    tracked = subprocess.run(
        ["git", "grep", "-l", marker], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert tracked.stdout == ""  # never in the repository's content


# ================================================================ I9 a changed work tree


def test_i9_a_changed_work_tree_is_refused_before_any_write(home: Path) -> None:
    from agent_core.types.files import MANIFEST_FILE_NAME

    manifest = home / "area" / "tree" / MANIFEST_FILE_NAME
    data = json.loads(manifest.read_text())
    data["manifest"]["root_inode"] = int(data["manifest"]["root_inode"]) + 1  # another tree
    manifest.write_text(json.dumps(data))
    _, events = _shell(home, "start report", approve, "steps", "quit")
    assert not _target(home).exists()
    states = [a["state"] for s in _kinds(events, "steps")[0]["steps"] for a in s["attempts"]]
    assert "ABORTED_BEFORE_DISPATCH" in states  # refused by the broker before the start
    assert _kinds(events, "outcome")[-1]["run_status"] != "COMPLETED"
