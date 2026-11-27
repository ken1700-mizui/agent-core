"""WP-10 limited fixes R01-R04 (regression tests, written before the fixes).

R01 the home, keys and config are read through the WP-08 safe checks from a
verified home fd (no FIFO or device side effect, links, owners and modes
refused, bounded reads, strict JSON, unfinished init refused); R02 the owner's
batch counter (atomic, fsynced, strict, consistent with the journal; a stale
counter stops owner operations, a gap does not); R03 ``status`` is read-only
(no new owner epoch, no revocation, nothing changed); R04 failures and closing
(fixed codes, the original error kept, bounded input, sessions of earlier Runs
never stood in for).
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.types.errors import OwnerAuthorizationError
from agent_core_cli import app, environment, views
from support import ledger_world as lw

REPO_ROOT = Path(__file__).resolve().parents[2]


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()
        self.mono = lw.ManualClock(0)

    def call(self, *argv: str, lines: list[str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        feed = list(lines or [])

        def reader() -> str:
            return feed.pop(0) + "\n" if feed else ""

        code = app.main(
            list(argv),
            stdout=out,
            stderr=err,
            reader=reader,
            clock=self.clock,
            monotonic=self.mono.now_ms,
        )
        return code, out.getvalue(), err.getvalue()

    def init(self) -> None:
        assert self.call("init", "--home", str(self.home))[0] == 0

    def shell(self, *lines: str) -> tuple[int, list[dict[str, Any]]]:
        code, out, _ = self.call("shell", "--home", str(self.home), "--json", lines=list(lines))
        return code, [json.loads(line) for line in out.splitlines() if line.strip()]

    def status(self) -> tuple[int, str, str]:
        return self.call("status", "--home", str(self.home), "--json")


def errors(events: list[dict[str, Any]]) -> list[str]:
    return [e["code"] for e in events if e["kind"] == "error"]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(tmp_path)
    e.init()
    return e


def _cli(*argv: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "agent_core_cli", *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


# ================================================================ R01 home, keys, config


def test_r01_a_fifo_in_place_of_the_key_never_blocks(env: Env) -> None:
    key = env.home / "owner.key"
    key.unlink()
    os.mkfifo(key, 0o600)
    result = _cli("status", "--home", str(env.home))  # would hang if the FIFO were opened
    assert result.returncode == 1
    assert "HOME_UNSAFE" in result.stderr


@pytest.mark.parametrize("name", ["owner.key", "config.json", "owner.seq"])
def test_r01_links_are_refused(env: Env, tmp_path: Path, name: str) -> None:
    target = env.home / name
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(target.read_bytes())
    elsewhere.chmod(0o600)
    target.unlink()
    target.symlink_to(elsewhere)
    assert "HOME_UNSAFE" in env.status()[2]
    target.unlink()
    os.link(elsewhere, target)  # a hard link: two names for the file
    assert "HOME_UNSAFE" in env.status()[2]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400])
def test_r01_a_wrong_mode_is_refused(env: Env, mode: int) -> None:
    (env.home / "owner.key").chmod(mode)
    assert "HOME_UNSAFE" in env.status()[2]


def test_r01_an_unsafe_home_or_parent_is_refused(env: Env) -> None:
    env.home.chmod(0o755)
    assert "HOME_UNSAFE" in env.status()[2]
    env.home.chmod(0o700)
    env.home.parent.chmod(0o777)  # writable by others, no sticky bit
    try:
        assert "HOME_UNSAFE" in env.status()[2]
    finally:
        env.home.parent.chmod(0o755)
    assert env.status()[0] == 0


def test_r01_an_oversized_file_is_refused(env: Env) -> None:
    config = env.home / "config.json"
    config.write_bytes(b" " * 2_000_000)
    assert "HOME_UNSAFE" in env.status()[2]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d | {"extra": 1},
        lambda d: {k: v for k, v in d.items() if k != "owner"},
        lambda d: d | {"owner": d["owner"][:-2]},
        lambda d: d | {"owner": d["owner"].upper()},
        lambda d: d | {"owner": 1},
    ],
)
def test_r01_the_key_file_is_strict(env: Env, mutate: Any) -> None:
    key = env.home / "owner.key"
    key.write_text(json.dumps(mutate(json.loads(key.read_text()))))
    assert "HOME_UNSAFE" in env.status()[2]


def test_r01_duplicate_json_keys_are_refused(env: Env) -> None:
    config = env.home / "config.json"
    text = config.read_text()
    config.write_text(text[:-1] + ', "version": 1}')
    assert "HOME_UNSAFE" in env.status()[2]


def test_r01_an_unfinished_init_is_not_a_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = Env(tmp_path)

    def crash(*_a: Any, **_k: Any) -> Any:
        raise OSError("synthetic crash during init")

    monkeypatch.setattr(environment, "create_synthetic_workspace", crash)
    code, _, err = e.call("init", "--home", str(e.home))
    assert code != 0
    monkeypatch.undo()
    before = sorted(p.name for p in e.home.iterdir())
    code, _, err = e.status()
    assert code == 1
    assert "HOME_INCOMPLETE" in err
    assert e.call("init", "--home", str(e.home))[0] == 1  # never re-initialized over it
    assert sorted(p.name for p in e.home.iterdir()) == before  # nothing deleted


# ================================================================ R02 the owner's counter


def _seq(env: Env) -> int:
    return int((env.home / "owner.seq").read_text())


def _db_seq(env: Env) -> int:
    conn = sqlite3.connect(f"file:{env.home / 'journal.db'}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT last_seq FROM owner_sequence").fetchone()[0])
    finally:
        conn.close()


def test_r02_the_counter_follows_the_journal(env: Env) -> None:
    assert _seq(env) == _db_seq(env)
    env.shell("start report")
    assert _seq(env) == _db_seq(env)


@pytest.mark.parametrize("value", [b"abc\n", b"-1\n", b"", b"1 2\n", str(2**63).encode() + b"\n"])
def test_r02_an_invalid_counter_stops_owner_operations(env: Env, value: bytes) -> None:
    (env.home / "owner.seq").write_bytes(value)
    _, events = env.shell("start report", "quit")
    assert errors(events) == ["OWNER_SEQUENCE_INVALID"]
    assert (env.home / "owner.seq").read_bytes() == value  # not repaired by guessing


def test_r02_a_stale_counter_is_never_reused(env: Env) -> None:
    env.shell("start report")
    (env.home / "owner.seq").write_text("1\n")  # an old copy: its numbers are used
    _, events = env.shell("start report", "stop global", "quit")
    assert errors(events) == ["OWNER_SEQUENCE_STALE", "OWNER_SEQUENCE_STALE"]
    assert _seq(env) == 1  # left for the owner to look at
    assert not [e for e in events if e["kind"] == "started"]


def test_r02_a_gap_is_allowed(env: Env) -> None:
    (env.home / "owner.seq").write_text(f"{_db_seq(env) + 5}\n")  # a crash after saving
    _, events = env.shell("start report", "quit")
    assert errors(events) == []
    assert _seq(env) == _db_seq(env)


def test_r02_a_crash_after_saving_the_counter_leaves_a_gap(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _db_seq(env)
    real = SqliteJournal.apply_owner_ledger

    def crash(self: Any, batch: Any, *, owner_mac: str) -> None:
        raise OSError("synthetic crash before the batch is applied")

    monkeypatch.setattr(SqliteJournal, "apply_owner_ledger", crash)
    env.shell("start report", "quit")
    monkeypatch.setattr(SqliteJournal, "apply_owner_ledger", real)
    assert _seq(env) > before == _db_seq(env)
    _, events = env.shell("start report", "quit")
    assert "OWNER_SEQUENCE_STALE" not in errors(events)


def test_r02_a_signed_batch_is_never_applied_twice(env: Env) -> None:
    session = environment.open_session(env.home, clock=env.clock, monotonic=env.mono.now_ms)
    try:
        from agent_core.types.ledger import StopScope
        from agent_core.types.safety import StopScopeType

        signed = session.owner.sign(
            StopScope(op="stop_scope", scope_type=StopScopeType.GLOBAL, scope_id="global")
        )
        session.journal.apply_owner_ledger(signed[0], owner_mac=signed[1])
        with pytest.raises(OwnerAuthorizationError):
            session.journal.apply_owner_ledger(signed[0], owner_mac=signed[1])
    finally:
        session.close()


def test_r02_a_leftover_temporary_file_is_not_a_saved_counter(env: Env) -> None:
    (env.home / "owner.seq.tmp-leftover").write_text("999\n")
    _, events = env.shell("start report", "quit")
    assert errors(events) == []
    assert _seq(env) == _db_seq(env)


# ================================================================ R03 status is read-only


def _dump(env: Env) -> list[str]:
    conn = sqlite3.connect(f"file:{env.home / 'journal.db'}?mode=ro", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_r03_status_changes_nothing(env: Env) -> None:
    env.shell("start report")  # a Run waits with a permit history
    before = _dump(env)
    for _ in range(3):
        assert env.status()[0] == 0
    assert _dump(env) == before  # no new owner epoch, no revocation, no recovery


def test_r03_status_reads_a_consistent_state(env: Env) -> None:
    env.shell("start report")
    _, out, _ = env.status()
    run = json.loads(out.splitlines()[-1])["tasks"][0]["runs"][0]
    assert (run["status"], run["terminal"]) == ("WAITING_APPROVAL", False)


def test_r03_status_while_a_session_runs_is_refused(env: Env) -> None:
    session = environment.open_session(env.home, clock=env.clock, monotonic=env.mono.now_ms)
    try:
        code, _, err = env.status()
        assert code == 4
        assert "RUNTIME_LOCKED" in err
    finally:
        session.close()


# ================================================================ R04 failures and closing


def test_r04_a_status_failure_has_a_fixed_code(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("free text \x1b[2J")

    monkeypatch.setattr(views, "status", broken)
    code, out, err = env.status()
    assert code == 5
    assert "INTERNAL_ERROR (RuntimeError)" in err
    assert "free text" not in out + err


def test_r04_a_close_failure_does_not_hide_the_original(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("original")

    def bad_close(self: Any) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(views, "status", broken)
    monkeypatch.setattr(SqliteJournal, "close", bad_close)
    code, _, err = env.status()
    assert code == 5
    assert "INTERNAL_ERROR (RuntimeError)" in err


def test_r04_an_overlong_line_is_refused_with_bounded_memory(env: Env) -> None:
    code, events = env.shell("x" * 5_000_000, "quit")
    assert errors(events) == ["USAGE_LINE_TOO_LONG"]
    assert code == 0


def test_r04_an_overlong_line_from_a_pipe(env: Env) -> None:
    env_vars = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env_vars["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "agent_core_cli", "shell", "--home", str(env.home), "--json"],
        input="y" * 3_000_000 + "\nquit\n",
        capture_output=True,
        text=True,
        env=env_vars,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert errors(events) == ["USAGE_LINE_TOO_LONG"]


def test_r04_an_earlier_run_is_never_stood_in_for_by_a_new_one(env: Env) -> None:
    env.shell("start report")  # run 1 waits; the session ends
    code, events = env.shell("start report", "pending", "quit")
    assert code == 3  # the new Run waits
    items = [e for e in events if e["kind"] == "pending"][-1]["items"]
    by_resumable = sorted((i["resumable"], i["targets"][0]["path"]) for i in items)
    assert by_resumable == [
        (False, "workspace/out/report-1.txt"),
        (True, "workspace/out/report-2.txt"),
    ]


def test_r04_task_and_run_end_states_are_shown_apart(env: Env) -> None:
    code, events = env.shell("start falseclaim", "status", "quit")
    assert code == 1  # the Run failed
    status = [e for e in events if e["kind"] == "status"][-1]
    [task] = status["tasks"]
    run = task["runs"][0]
    assert (run["status"], run["terminal"]) == ("FAILED", True)
    assert task["status"] == "ACTIVE"
    assert task["closed"] is False  # Phase 1 never closes a Task by a Run's end
