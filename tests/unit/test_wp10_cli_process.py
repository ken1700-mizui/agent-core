"""WP-10: the CLI as a real child process (``python -m agent_core_cli``).

A synthetic home in a temporary directory; JSON lines on stdout. Covers a whole
session, the exit codes, the OS lock (one Runtime at a time), Ctrl+C (SIGINT),
EOF and a killed process (the next session does not resume the old Run).
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 60.0


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def _run(*argv: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_core_cli", *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env=_env(),
        timeout=TIMEOUT,
        check=False,
    )


@contextmanager
def _shell(home: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_core_cli", "shell", "--home", str(home), "--json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=TIMEOUT)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _send(process: subprocess.Popen[str], line: str) -> None:
    assert process.stdin is not None
    process.stdin.write(line + "\n")
    process.stdin.flush()


def _until(process: subprocess.Popen[str], kind: str) -> list[dict[str, Any]]:
    """Read JSON events until one of ``kind`` arrives."""
    assert process.stdout is not None
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.5)
        if not ready:
            continue
        line = process.stdout.readline()
        if not line:
            err = process.stderr.read() if process.stderr else ""
            pytest.fail(f"the CLI ended early: {err[-2000:]}")
        event = json.loads(line)
        seen.append(event)
        if event["kind"] == kind:
            return seen
    pytest.fail(f"no {kind} event in time")


def _init(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    result = _run("init", "--home", str(home))
    assert result.returncode == 0, result.stderr
    return home


def _target(home: Path) -> Path:
    return home / "area" / "tree" / "workspace" / "out" / "report-1.txt"


def test_a_whole_session_in_a_child_process(tmp_path: Path) -> None:
    home = _init(tmp_path)
    with _shell(home) as cli:
        _send(cli, "start report")
        events = _until(cli, "approval_request")  # after the outcome event
        assert [e["kind"] for e in events][-2:] == ["outcome", "approval_request"]
        action = events[-1]["action_hash"]
        _send(cli, f"approve {action}")
        outcome = _until(cli, "outcome")[-1]
        assert outcome["run_status"] == "COMPLETED"
        _send(cli, "quit")
        _until(cli, "bye")
        assert cli.wait(timeout=TIMEOUT) == 0
    assert _target(home).read_text() == "synthetic report v1\n"


def test_exit_codes(tmp_path: Path) -> None:
    home = _init(tmp_path)
    assert _run("--no-such-option").returncode == 2  # usage
    assert _run("init", "--home", str(home)).returncode == 1  # failure: exists
    assert _run("shell", "--home", str(home), "--json", stdin="start report\n").returncode == 3
    assert _run("status", "--home", str(home)).returncode == 0
    (tmp_path / "other").mkdir()
    other = _init(tmp_path / "other")
    failed = _run("shell", "--home", str(other), "--json", stdin="start falseclaim\nquit\n")
    assert failed.returncode == 1  # the session's run failed
    done = _run("shell", "--home", str(other), "--json", stdin="quit\n")
    assert done.returncode == 0


def test_one_runtime_at_a_time(tmp_path: Path) -> None:
    home = _init(tmp_path)
    with _shell(home) as cli:
        _send(cli, "status")
        _until(cli, "status")
        second = _run("status", "--home", str(home))
        assert second.returncode == 4
        assert "RUNTIME_LOCKED" in second.stderr
        another = _run("shell", "--home", str(home), "--json", stdin="quit\n")
        assert another.returncode == 4
        _send(cli, "quit")
        assert cli.wait(timeout=TIMEOUT) == 0
    assert _run("status", "--home", str(home)).returncode == 0  # released at exit


def test_sigint_while_waiting_for_input(tmp_path: Path) -> None:
    home = _init(tmp_path)
    with _shell(home) as cli:
        _send(cli, "start report")
        _until(cli, "approval_request")
        cli.send_signal(signal.SIGINT)
        assert cli.wait(timeout=TIMEOUT) == 130
    assert not _target(home).exists()
    status = _run("status", "--home", str(home), "--json")
    assert status.returncode == 0  # the lock was released
    run = json.loads(status.stdout.splitlines()[-1])["tasks"][0]["runs"][0]
    assert run["status"] == "WAITING_APPROVAL"


def test_eof_while_waiting_approves_nothing(tmp_path: Path) -> None:
    home = _init(tmp_path)
    result = _run("shell", "--home", str(home), "--json", stdin="start report\n")
    assert result.returncode == 3
    assert not _target(home).exists()
    assert json.loads(result.stdout.splitlines()[-1])["kind"] == "bye"


def test_a_killed_session_is_not_resumed_by_the_next_one(tmp_path: Path) -> None:
    home = _init(tmp_path)
    with _shell(home) as cli:
        _send(cli, "start report")
        action = _until(cli, "approval_request")[-1]["action_hash"]
        cli.send_signal(signal.SIGKILL)
        cli.wait(timeout=TIMEOUT)
    result = _run(
        "shell", "--home", str(home), "--json", stdin=f"approve {action}\nstop run last\nquit\n"
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    errors = [e["code"] for e in events if e["kind"] == "error"]
    assert errors[0] == "SESSION_UNAVAILABLE"
    assert not _target(home).exists()
    outcomes = [e for e in events if e["kind"] == "outcome"]
    assert outcomes[-1]["run_status"] == "CANCELLED"
    assert result.returncode == 0


def test_no_key_is_printed_by_a_child_process(tmp_path: Path) -> None:
    home = _init(tmp_path)
    keys = json.loads((home / "owner.key").read_text())
    result = _run("shell", "--home", str(home), "--json", stdin="start report\nstatus\nquit\n")
    for value in keys.values():
        assert value not in result.stdout + result.stderr
