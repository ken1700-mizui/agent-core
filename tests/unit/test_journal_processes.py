"""Journal tests with real child processes (Linux).

The child is the fixed local script tests/support/journal_child.py, started
with the current interpreter. These tests use real OS behaviour: flock held by
another process, and a process killed with SIGKILL in the middle of a
transaction. They are not exception injection.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.types.errors import RuntimeLockHeldError
from support import builders as b
from support.fakes import append_next

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "journal_child.py"
TIMEOUT_SECONDS = 30


def _open(path: Path) -> SqliteJournal:
    return SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource())


@contextmanager
def _child(mode: str, db_path: Path) -> Iterator[subprocess.Popen[str]]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests"))
    process = subprocess.Popen(
        [sys.executable, str(CHILD), mode, str(db_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=TIMEOUT_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _read_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr else ""
        pytest.fail(f"child exited early: {stderr[-2000:]}")
    return line.strip()


def test_lock_held_by_another_process_refuses_a_second_runtime(tmp_path: Path) -> None:
    db_path = tmp_path / "journal.db"
    with _child("hold", db_path) as child:
        assert _read_line(child) == "ready 1"
        with pytest.raises(RuntimeLockHeldError):
            _open(db_path)
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=TIMEOUT_SECONDS) == 0
    journal = _open(db_path)
    try:
        assert journal.owner_epoch == 2
    finally:
        journal.close()


def test_kernel_releases_the_lock_when_the_owner_is_killed(tmp_path: Path) -> None:
    db_path = tmp_path / "journal.db"
    with _child("hold", db_path) as child:
        assert _read_line(child) == "ready 1"
        child.send_signal(signal.SIGKILL)
        assert child.wait(timeout=TIMEOUT_SECONDS) == -signal.SIGKILL
    journal = _open(db_path)
    try:
        assert journal.owner_epoch == 2
    finally:
        journal.close()


def test_kill_inside_a_transaction_leaves_no_partial_write_or_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "journal.db"
    with _child("append-then-hang", db_path) as child:
        assert _read_line(child) == "in-transaction"
        child.send_signal(signal.SIGKILL)
        assert child.wait(timeout=TIMEOUT_SECONDS) == -signal.SIGKILL

    journal = _open(db_path)
    try:
        assert journal.owner_epoch == 2
        head = journal.verify_tenant(b.TENANT)
        assert head is not None
        assert head.tenant_seq == 2
        events = journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10)
        assert [e.tenant_seq for e in events] == [1, 2]
        nxt = append_next(journal, b.event_draft(owner_epoch=journal.owner_epoch))
        assert nxt.tenant_seq == 3
        assert journal.verify_tenant(b.TENANT) is not None
    finally:
        journal.close()
