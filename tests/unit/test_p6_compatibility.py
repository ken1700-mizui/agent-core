"""P-6 compatibility: data written before P-6 (tests for the compatibility fix).

Before P-6 the home's ``config.json`` had no ``profile``, the stored Policy had
no ``trusted_synthetic_tasks`` and a stored RunGrant had no
``synthetic_task_ref``. Two kinds of tests:

- **Real old format** (``_strip``): a home made by this version, then exactly the
  P-6 fields removed from the config and from the stored bodies, as the earlier
  version wrote them. A one-off check with the real earlier code (``d23b1e8``)
  gave the same data and behaviour (``P-7_JOURNAL_PERFORMANCE.md``, 6).
- **Deliberately inconsistent** (``_strip_one``, ``_untrust``): data no version
  wrote (one field removed and the other kept, or a current Policy that trusts
  no demo), to show that each check refuses on its own.

What must hold: an unsupported stored format is refused with a typed error and a
fixed guidance text (``STORED_FORMAT_UNSUPPORTED``); a new Run that can be seen
to fail is refused before its Task and Run are created; status, stop and
recovery keep working; nothing of the old records (journal, Policy, RunGrant,
approvals, hashes) is changed, deleted or re-signed; no old approval is used
again; there is no automatic migration.
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
        self.pending = ""

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
    requests = [e for e in events if e["kind"] == "approval_request"]
    assert requests, err
    home.pending = requests[0]["action_hash"]
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


def _tasks(home: _Home) -> int:
    return int(home.db("SELECT COUNT(*) FROM rt_tasks")[0][0])


def _runs(home: _Home) -> int:
    return int(home.db("SELECT COUNT(*) FROM rt_runs")[0][0])


def _errors(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["kind"] == "error"]


def test_a_new_run_on_an_old_policy_is_refused_before_anything_is_created(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    before = (old.db("SELECT COUNT(*) FROM events")[0][0], _tasks(old), _runs(old))
    code, events, err = old.shell("start report", "quit")
    assert code == 0, err  # a typed refusal; the shell goes on
    errors = _errors(events)
    assert [e["code"] for e in errors] == ["STORED_FORMAT_UNSUPPORTED"]
    assert "new home" in errors[0]["detail"]  # the fixed guidance
    assert (old.db("SELECT COUNT(*) FROM events")[0][0], _tasks(old), _runs(old)) == before
    assert old.db("SELECT COUNT(*) FROM reservations WHERE state = 'RESERVED'")[0][0] == 0
    assert _records(old) == records
    # Only the old Run is there for the owner's recovery.
    code, events, err = old.shell("recover all", "quit")
    assert [e["after"] for e in events if e["kind"] == "run_recovered"] == ["FAILED"], err
    assert _records(old) == records


def test_status_and_stop_work_on_an_old_format(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    code, _events, err = old.call("status", "--home", str(old.home))
    assert code == 0, err
    code, events, err = old.shell("stop tenant", "quit")
    assert code == 0, err
    assert not _errors(events), events
    assert old.db("SELECT COUNT(*) FROM stop_state")[0][0] >= 1
    assert _records(old) == records


def test_an_old_pending_approval_is_never_used_again(old: _Home) -> None:
    _strip(old, profile=True)
    records = _records(old)
    approvals = old.db("SELECT approval_id, uses, revoked, body FROM approvals ORDER BY 1")
    _code, events, _err = old.shell(f"approve {old.pending}", "quit")
    assert _errors(events)  # refused, whatever the first check that refuses it
    # No approval is added, used again or changed.
    assert old.db("SELECT approval_id, uses, revoked, body FROM approvals ORDER BY 1") == approvals
    assert old.db("SELECT COUNT(*) FROM reservations WHERE state = 'RESERVED'")[0][0] == 0
    assert _records(old) == records


# ---------------------------------------------------------------- deliberately inconsistent


def _strip_one(home: _Home, table: str, field: str) -> None:
    """Remove one P-6 field from one table only: data no version wrote."""
    conn = sqlite3.connect(home.home / "journal.db")
    try:
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


def _untrust(home: _Home) -> None:
    """A current-format Policy that trusts no synthetic task definition."""
    conn = sqlite3.connect(home.home / "journal.db")
    try:
        for key, body in conn.execute("SELECT rowid, body FROM policies").fetchall():
            data = json.loads(body)
            data["trusted_synthetic_tasks"] = []
            conn.execute(
                "UPDATE policies SET body = ? WHERE rowid = ?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), key),
            )
        conn.commit()
    finally:
        conn.close()


def test_only_the_policy_old_is_refused_before_anything_is_created(old: _Home) -> None:
    _strip_one(old, "policies", "trusted_synthetic_tasks")
    before = (_tasks(old), _runs(old))
    _code, events, _err = old.shell("start report", "quit")
    assert [e["code"] for e in _errors(events)] == ["STORED_FORMAT_UNSUPPORTED"]
    assert (_tasks(old), _runs(old)) == before


def test_an_untrusted_task_is_refused_before_anything_is_created(old: _Home) -> None:
    _untrust(old)
    before = (_tasks(old), _runs(old))
    records = _records(old)
    _code, events, _err = old.shell("start report", "quit")
    errors = _errors(events)
    assert [e["code"] for e in errors] == ["RUN_NOT_AUTHORIZED"]
    assert errors[0]["detail"] == "SYNTHETIC_TASK_UNTRUSTED"
    assert (_tasks(old), _runs(old)) == before
    assert _records(old) == records


def test_only_the_grant_old_is_refused_with_a_typed_error(old: _Home) -> None:
    from agent_core.adapters import sqlite_ledger
    from agent_core.types.errors import StoredFormatUnsupportedError

    _strip_one(old, "run_grants", "synthetic_task_ref")
    run_id = old.db("SELECT run_id FROM run_grants")[0][0]
    conn = sqlite3.connect(f"file:{old.home / 'journal.db'}?mode=ro", uri=True)
    try:
        with pytest.raises(StoredFormatUnsupportedError) as raised:
            sqlite_ledger.read_grant(conn.cursor(), run_id)
        assert raised.value.reasons == ("RUN_GRANT",)
    finally:
        conn.close()
    records = _records(old)
    code, _events, err = old.call("status", "--home", str(old.home))
    assert code == 0, err
    assert _records(old) == records


def test_an_old_policy_is_a_typed_error_at_the_ledger(old: _Home) -> None:
    from agent_core.adapters import sqlite_ledger
    from agent_core.types.errors import StoredFormatUnsupportedError

    _strip(old, profile=True)
    tenant = old.db("SELECT tenant_id FROM policies")[0][0]
    conn = sqlite3.connect(f"file:{old.home / 'journal.db'}?mode=ro", uri=True)
    try:
        with pytest.raises(StoredFormatUnsupportedError) as raised:
            sqlite_ledger.read_policy(conn.cursor(), tenant)
        assert raised.value.reasons == ("POLICY",)
    finally:
        conn.close()
