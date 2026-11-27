"""P-6 compatibility: data written before P-6 (characterization tests).

Before P-6 the home's ``config.json`` had no ``profile``, the stored Policy had
no ``trusted_synthetic_tasks`` and a stored RunGrant had no
``synthetic_task_ref``. These tests make such data synthetically (a home made by
this version, then the P-6 fields removed from the config and from the stored
bodies, as the earlier version wrote them) and record how this version treats
it. A separate one-off check with the real earlier code (``d23b1e8``) gave the
same behaviour (``docs/design/phase-1/P-7_JOURNAL_PERFORMANCE.md``, 6).

What must hold: every refusal is on the safe side, nothing of the old records
is changed, deleted or re-signed, and the old Runs can still be recovered once
the owner states the profile. Known issue (reported, not fixed here): a new Run
on an old Policy fails with an untyped error and leaves its Run for recovery.
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

    def db(self, sql: str) -> list[Any]:
        conn = sqlite3.connect(f"file:{self.home / 'journal.db'}?mode=ro", uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


def _strip(home: _Home, *, profile: bool) -> None:
    """Make the home look as the earlier version wrote it."""
    path = home.home / "config.json"
    config = json.loads(path.read_text())
    config.pop("profile")
    if profile:
        config["profile"] = "phase1_synthetic"  # the owner states the profile by hand
    path.chmod(0o600)
    path.write_text(json.dumps(config, sort_keys=True))
    conn = sqlite3.connect(home.home / "journal.db")
    try:
        for table, field in (
            ("policies", "trusted_synthetic_tasks"),
            ("run_grants", "synthetic_task_ref"),
        ):
            for key, body in conn.execute(f"SELECT rowid, body FROM {table}").fetchall():  # noqa: S608
                data = json.loads(body)
                data.pop(field)
                conn.execute(
                    f"UPDATE {table} SET body = ? WHERE rowid = ?",  # noqa: S608
                    (json.dumps(data, separators=(",", ":"), sort_keys=True), key),
                )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def old(tmp_path: Path) -> _Home:
    home = _Home(tmp_path)
    assert home.call("init", "--home", str(home.home))[0] == 0
    _code, events, err = home.shell("start report", "quit")  # left waiting for its approval
    assert any(e["kind"] == "approval_request" for e in events), err
    return home


def _records(home: _Home) -> tuple[Any, ...]:
    return (
        tuple(home.db("SELECT policy_version, policy_hash, body FROM policies")),
        tuple(home.db("SELECT run_id, grant_hash, body FROM run_grants")),
    )


def test_an_old_config_is_refused_before_anything_changes(old: _Home) -> None:
    _strip(old, profile=False)
    before = ((old.home / "journal.db").read_bytes(), (old.home / "config.json").read_bytes())
    for command in ("status", "shell"):
        code, _events, err = old.call(command, "--home", str(old.home))
        assert code != 0
        assert "PROFILE_REFUSED" in err
    after = ((old.home / "journal.db").read_bytes(), (old.home / "config.json").read_bytes())
    assert after == before  # not read into a new epoch, not rewritten


def test_with_the_profile_stated_an_old_journal_can_be_read(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    code, _events, err = old.call("status", "--home", str(old.home))
    assert code == 0, err
    assert _records(old) == records


def test_old_runs_can_still_be_recovered_and_old_records_are_kept(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    _code, events, err = old.shell("recover all", "quit")
    recovered = [e for e in events if e["kind"] == "run_recovered"]
    assert recovered, err
    assert recovered[0]["after"] == "FAILED", err
    assert _records(old) == records  # never rewritten or re-signed


def test_a_new_run_on_an_old_policy_is_refused_without_a_grant(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    grants = len(old.db("SELECT run_id FROM run_grants"))
    code, events, _err = old.shell("start report", "quit")
    assert code != 0
    assert [e for e in events if e["kind"] == "error"]
    assert len(old.db("SELECT run_id FROM run_grants")) == grants  # no grant for the new Run
    assert old.db("SELECT COUNT(*) FROM reservations WHERE state = 'RESERVED'")[0][0] == 0
    assert _records(old) == records
    # The Run it created is not lost: the owner's recovery ends it.
    code, events, err = old.shell("recover all", "quit")
    assert [e["after"] for e in events if e["kind"] == "run_recovered"] == ["FAILED", "FAILED"], err
