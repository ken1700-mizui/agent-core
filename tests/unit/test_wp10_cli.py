"""WP-10: the minimal CLI (in-process tests; the child-process tests are in
``test_wp10_cli_process.py``).

The CLI is the composition root of a single synthetic Runtime: it opens the
journal (which holds the OS lock), builds the runtime, the gate, the file broker
and the agent loop, and lets the owner start a synthetic demo task, look at its
state, approve exactly the waiting action, and stop. Calling the CLI grants
nothing: every change goes through the owner-signed path and the ledger.
"""

from __future__ import annotations

import io
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.fake_model import FakeFault, FakeFaultKind
from agent_core.types.model import ModelRequest, ModelResponse
from agent_core_cli import app, render
from support import ledger_world as lw

Step = str | Callable[[list[dict[str, Any]]], str]


# ---------------------------------------------------------------- harness


class Shell:
    """Feeds shell lines; a callable line sees the JSON events printed so far."""

    def __init__(self, steps: list[Step], out: io.StringIO) -> None:
        self._steps = list(steps)
        self._out = out

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._out.getvalue().splitlines() if line.strip()]

    def __call__(self) -> str:
        if not self._steps:
            return ""  # EOF
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return (step(self.events()) if callable(step) else step) + "\n"


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()
        self.mono = lw.ManualClock(0)

    def call(self, *argv: str, steps: list[Any] | None = None, **hooks: Any) -> tuple[int, Any]:
        out, err = io.StringIO(), io.StringIO()
        shell = Shell(steps or [], out)
        code = app.main(
            list(argv),
            stdout=out,
            stderr=err,
            reader=shell,
            clock=self.clock,
            monotonic=self.mono.now_ms,
            hooks=app.Hooks(**hooks) if hooks else None,
        )
        return code, _Result(out.getvalue(), err.getvalue())

    def init(self) -> None:
        code, result = self.call("init", "--home", str(self.home))
        assert code == 0, result.err

    def shell(self, steps: list[Any], **hooks: Any) -> tuple[int, Any]:
        return self.call("shell", "--home", str(self.home), "--json", steps=steps, **hooks)


class _Result:
    def __init__(self, out: str, err: str) -> None:
        self.out = out
        self.err = err

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.out.splitlines() if line.strip()]

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events() if e["kind"] == kind]


def last(kind: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return [e for e in events if e["kind"] == kind][-1]


def approve_last(events: list[dict[str, Any]]) -> str:
    return "approve " + str(last("approval_request", events)["action_hash"])


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(tmp_path)
    e.init()
    return e


def _target(env: Env, run_no: int = 1) -> Path:
    return env.home / "area" / "tree" / "workspace" / "out" / f"report-{run_no}.txt"


# ================================================================ init


def test_init_creates_a_private_home(tmp_path: Path) -> None:
    e = Env(tmp_path)
    e.init()
    assert stat.S_IMODE(e.home.stat().st_mode) == 0o700
    for name in ("owner.key", "owner.seq", "config.json"):
        assert stat.S_IMODE((e.home / name).stat().st_mode) == 0o600
    code, result = e.call("init", "--home", str(e.home))
    assert code == 1
    assert "HOME_EXISTS" in result.err


def test_a_command_on_a_missing_home_is_refused(tmp_path: Path) -> None:
    code, result = Env(tmp_path).call("status", "--home", str(tmp_path / "nowhere"))
    assert code == 1
    assert "HOME_MISSING" in result.err


# ================================================================ normal and repair tasks


def test_a_normal_task_runs_to_completion_with_one_approval(env: Env) -> None:
    code, result = env.shell(["start report", approve_last, "status", "quit"])
    assert code == 0
    outcomes = result.kinds("outcome")
    assert [o["run_status"] for o in outcomes] == ["WAITING_APPROVAL", "COMPLETED"]
    assert outcomes[-1]["reason"] == "VERIFIED"
    assert _target(env).read_text() == "synthetic report v1\n"
    status = last("status", result.events())
    [task] = status["tasks"]
    # The Run completed; the Task is a separate record (Phase 1 does not close it).
    assert task["runs"][0]["status"] == "COMPLETED"
    assert task["status"] == "ACTIVE"
    assert last("bye", result.events())["exit_code"] == 0


def test_a_task_with_a_repair_loop(env: Env) -> None:
    code, result = env.shell(["start repair", approve_last, approve_last, "quit"])
    assert code == 0
    final = result.kinds("outcome")[-1]
    assert (final["run_status"], final["repairs"]) == ("COMPLETED", 1)
    assert len(result.kinds("approval_request")) == 2


def test_a_false_completion_claim_fails_the_run(env: Env) -> None:
    code, result = env.shell(["start falseclaim", "quit"])
    assert code == 1  # the session's run failed
    final = result.kinds("outcome")[-1]
    assert (final["run_status"], final["reason"]) == ("FAILED", "REPAIR_LIMIT")


# ================================================================ state, steps, budget


def test_the_state_views(env: Env) -> None:
    code, result = env.shell(["start report", approve_last, "steps", "budget", "pending", "quit"])
    assert code == 0
    steps = last("steps", result.events())
    kinds = [s["kind"] for s in steps["steps"]]
    assert kinds == ["MODEL_CALL", "TOOL_CALL", "MODEL_CALL", "VERIFICATION", "VERIFICATION"]
    assert all(a["state"] == "COMPLETED" for s in steps["steps"] for a in s["attempts"])
    budget = last("budget", result.events())
    run_scope = next(s for s in budget["scopes"] if s["scope"] == "RUN")
    assert run_scope["spent_micro"] == 320
    assert run_scope["hold_micro"] == 0
    assert budget["unit"] == "XTU (synthetic)"
    assert budget["real_api_cost"] == "none"
    assert budget["unsettled"] == []
    assert last("pending", result.events())["items"] == []


def test_an_unsettled_cost_is_shown(env: Env) -> None:
    class NonFinal:
        def __init__(self, real: Any) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            response: ModelResponse = self.real.complete(request)
            return response.model_copy(update={"final_usage": False})

    code, result = env.shell(["start report", "budget", "quit"], model_wrapper=NonFinal)
    assert code == 3  # left waiting
    outcome = result.kinds("outcome")[-1]
    assert (outcome["run_status"], outcome["reason"]) == ("PAUSED_BUDGET", "UNSETTLED_COSTS")
    [item] = last("budget", result.events())["unsettled"]
    assert item["step_kind"] == "MODEL_CALL"
    assert item["cost_final"] is False


def test_an_unknown_outcome_keeps_its_hold_and_is_shown(env: Env) -> None:
    class Lost:
        def __init__(self, real: Any) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            from agent_core.types.model import Usage
            from agent_core.types.model_errors import ModelOutcomeUnknownError

            raise ModelOutcomeUnknownError("synthetic", usage=Usage.unknown(synthetic=True))

    code, result = env.shell(["start report", "budget", "quit"], model_wrapper=Lost)
    assert code == 3
    assert result.kinds("outcome")[-1]["run_status"] == "RECONCILIATION_REQUIRED"
    [item] = last("budget", result.events())["unsettled"]
    assert item["hold_micro"] > 0
    assert item["reservation_state"] == "UNKNOWN"


# ================================================================ approval


def test_the_approval_screen_shows_fixed_metadata_only(env: Env) -> None:
    code, result = env.shell(["start report", "pending", "quit"])
    assert code == 3
    [screen] = last("pending", result.events())["items"]
    assert screen["operation"] == "TOOL_CALL"
    assert (screen["tool_id"], screen["tool_version"]) == ("write_file", "1.0.0")
    assert screen["manifest_hash"].startswith("sha256:")
    assert screen["artifact_hash"].startswith("sha256:")
    assert screen["targets"] == [{"path": "workspace/out/report-1.txt", "access": "WRITE"}]
    assert screen["change"]["op"] == "CREATE"
    assert screen["change"]["before"] is None
    assert screen["change"]["after"].startswith("sha256:")
    assert screen["cost_cap_micro"] == 5_000
    assert screen["expires_at_ms"] > env.clock.now
    assert screen["action_hash"].startswith("sha256:")
    text = result.out
    assert "synthetic report v1" not in text  # never the content
    assert "Create workspace" not in text  # never the prompt


def test_a_wrong_action_is_not_approved(env: Env) -> None:
    wrong = "approve sha256:" + "0" * 64
    code, result = env.shell(["start report", wrong, "pending", "quit"])
    assert code == 3
    assert last("error", result.events())["code"] == "APPROVAL_TARGET_MISMATCH"
    assert len(last("pending", result.events())["items"]) == 1  # still waiting
    assert not _target(env).exists()


def test_an_expired_approval_window_is_refused(env: Env) -> None:
    def later(events: list[dict[str, Any]]) -> str:
        env.clock.now += 120_000  # past the action's deadline
        return approve_last(events)

    code, result = env.shell(["start report", later, "stop run", "quit"])
    assert last("error", result.events())["code"] == "APPROVAL_EXPIRED"
    assert not _target(env).exists()
    assert result.kinds("outcome")[-1]["run_status"] == "CANCELLED"
    assert code == 0


def test_an_approval_is_never_reused(env: Env) -> None:
    code, result = env.shell(["start report", approve_last, approve_last, "quit"])
    assert code == 0
    assert last("error", result.events())["code"] == "NO_PENDING_APPROVAL"


def test_nothing_is_approved_without_an_approve_command(env: Env) -> None:
    code, result = env.shell(["start report", "status", "steps", "budget", "pending"])
    assert code == 3  # EOF while waiting
    assert not _target(env).exists()
    assert [o["run_status"] for o in result.kinds("outcome")] == ["WAITING_APPROVAL"]


# ================================================================ stop


def test_a_stop_while_waiting_cancels_without_an_approval(env: Env) -> None:
    code, result = env.shell(["start report", "stop run", "pending", "quit"])
    assert code == 0
    assert result.kinds("outcome")[-1]["run_status"] == "CANCELLED"
    assert last("pending", result.events())["items"] == []
    assert not _target(env).exists()


def test_a_stop_wins_over_a_later_approval(env: Env) -> None:
    code, result = env.shell(["start report", "stop run", approve_last, "quit"])
    assert last("error", result.events())["code"] == "NO_PENDING_APPROVAL"
    assert not _target(env).exists()
    assert code == 0


def test_a_stop_racing_the_approved_action(env: Env) -> None:
    sessions: list[Any] = []

    class StopOnPrepare:
        def __init__(self, real: Any, session: Any) -> None:
            self._real = real
            sessions.append(session)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def prepare(self, descriptor: Any, policy: Any) -> Any:
            prepared = self._real.prepare(descriptor, policy)
            sessions[0].record_stop("tenant")  # the owner stops while the write starts
            return prepared

    code, result = env.shell(["start report", approve_last, "quit"], files_wrapper=StopOnPrepare)
    assert code == 0
    assert result.kinds("outcome")[-1]["run_status"] == "CANCELLED"
    assert not _target(env).exists()


def test_a_tenant_or_global_stop_survives_a_restart(env: Env) -> None:
    code, result = env.shell(["stop global", "stops", "quit"])
    assert code == 0
    [entry] = last("stops", result.events())["items"]
    assert (entry["scope_type"], entry["status"]) == ("GLOBAL", "STOPPED")
    code, result = env.shell(["stops", "start report", "quit"])
    assert last("stops", result.events())["items"][0]["status"] == "STOPPED"
    assert last("error", result.events())["code"] == "SCOPE_STOPPED"
    assert result.kinds("outcome") == []


def test_a_stop_is_lifted_only_by_an_explicit_resume(env: Env) -> None:
    code, result = env.shell(["stop global", "stops"])
    assert code == 0
    seq = last("stops", result.events())["items"][0]["stop_seq"]
    code, result = env.shell(
        ["resume-scope global", f"resume-scope global {seq}", "stops", "start report", "quit"]
    )
    errors = result.kinds("error")
    assert errors[0]["code"] == "USAGE_INVALID_ARGUMENTS"  # the stop_seq is required
    assert last("stops", result.events())["items"][0]["status"] != "STOPPED"
    assert result.kinds("outcome")[-1]["run_status"] == "WAITING_APPROVAL"


# ================================================================ input and output safety


def test_invalid_commands_and_arguments(env: Env) -> None:
    code, result = env.shell(
        [
            "frobnicate",
            "start",
            "start nosuchdemo",
            "steps run_not_an_id",
            "approve",
            "stop planet",
            "quit now please",
        ]
    )
    codes = [e["code"] for e in result.kinds("error")]
    assert codes == [
        "USAGE_INVALID_COMMAND",
        "USAGE_INVALID_ARGUMENTS",
        "UNKNOWN_DEMO",
        "RUN_NOT_FOUND",
        "USAGE_INVALID_ARGUMENTS",
        "USAGE_INVALID_ARGUMENTS",
        "USAGE_INVALID_ARGUMENTS",
    ]
    assert code == 0  # EOF, no run


def test_top_level_usage_errors(tmp_path: Path) -> None:
    e = Env(tmp_path)
    assert e.call("--bogus")[0] == 2
    assert e.call()[0] == 2
    assert e.call("shell")[0] == 2  # --home is required
    assert e.call("status", "--home", str(tmp_path), "--key", "x")[0] == 2  # no key arguments


def test_no_secret_reaches_the_output(env: Env) -> None:
    code, result = env.shell(
        ["start repair", approve_last, approve_last, "status", "budget", "quit"]
    )
    assert code == 0
    keys = json.loads((env.home / "owner.key").read_text())
    shown = result.out + result.err
    for value in keys.values():
        assert value not in shown
    assert "synthetic report v1" not in shown  # file contents
    assert "synthetic report draft" not in shown
    assert "Please create" not in shown  # the prompt


def test_terminal_control_characters_are_never_printed(env: Env) -> None:
    hostile = "frob\x1b[2J\x1b]0;title\x07\x9b31m\u202e\u200b"
    code, result = env.shell([hostile, "approve sha256:\x1b[31m", "quit"])
    assert code == 0
    shown = result.out + result.err
    for bad in ("\x1b", "\x07", "\x9b", "\u202e", "\u200b"):
        assert bad not in shown


def test_the_sanitizer() -> None:
    assert render.clean("plain text 123") == "plain text 123"
    cleaned = render.clean("a\x1b[2Jb\x00c\x7fd\x9be\u202ef\u2028g\ttab")
    assert cleaned == "a\\x1b[2Jb\\x00c\\x7fd\\x9be\\u202ef\\u2028g\\x09tab"
    assert len(render.clean("x" * 10_000)) <= 1_024


def test_human_readable_output(env: Env) -> None:
    out, err = io.StringIO(), io.StringIO()
    shell = Shell(["start report", "pending", "status", "quit"], out)
    code = app.main(
        ["shell", "--home", str(env.home)],
        stdout=out,
        stderr=err,
        reader=shell,
        clock=env.clock,
        monotonic=env.mono.now_ms,
    )
    assert code == 3
    text = out.getvalue()
    assert "run_status: WAITING_APPROVAL" in text
    assert "action_hash: sha256:" in text
    assert "approve sha256:" in text  # how to approve, with the exact target
    assert "cost unit: XTU (synthetic); real API cost: none" in text
    assert "task status: ACTIVE" in text


# ================================================================ session and process


def test_ctrl_c_at_the_prompt_exits_130_and_releases_the_lock(env: Env) -> None:
    code, _ = env.shell(["start report", KeyboardInterrupt()])
    assert code == 130
    code, _ = env.call("status", "--home", str(env.home), "--json")
    assert code == 0  # the lock was released


def test_ctrl_c_during_a_model_call_is_never_taken_as_not_run(env: Env) -> None:
    class Interrupt:
        def __init__(self, real: Any) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise KeyboardInterrupt

    code, _ = env.shell(["start report", "quit"], model_wrapper=Interrupt)
    assert code == 130
    code, result = env.call("status", "--home", str(env.home), "--json")
    run = last("status", result.events())["tasks"][0]["runs"][0]
    assert run["status"] == "RECONCILIATION_REQUIRED"  # started: its outcome is unknown


def test_a_run_of_an_earlier_session_is_not_resumed(env: Env) -> None:
    code, result = env.shell(["start report"])  # EOF: the session ends while waiting
    assert code == 3
    hash_ = last("approval_request", result.events())["action_hash"]
    run_id = result.kinds("started")[0]["run_id"]
    code, result = env.shell([f"approve {hash_}", "pending", f"stop run {run_id}", "quit"])
    assert result.kinds("error")[0]["code"] == "SESSION_UNAVAILABLE"
    assert not _target(env).exists()
    # Stopping it is still possible (nothing runs).
    assert result.kinds("outcome")[-1]["run_status"] == "CANCELLED"


def test_one_runtime_at_a_time(env: Env) -> None:
    from agent_core_cli.environment import open_session

    session = open_session(env.home, clock=env.clock, monotonic=env.mono.now_ms)
    try:
        code, result = env.call("status", "--home", str(env.home))
        assert code == 4
        assert "RUNTIME_LOCKED" in result.err
    finally:
        session.close()
    assert env.call("status", "--home", str(env.home))[0] == 0


def test_status_outside_a_session_is_read_only(env: Env) -> None:
    env.shell(["start report"])
    code, result = env.call("status", "--home", str(env.home), "--json")
    assert code == 0
    run = last("status", result.events())["tasks"][0]["runs"][0]
    assert run["status"] == "WAITING_APPROVAL"
    code, _ = env.call("status", "--home", str(env.home), "--json")
    run_again = last("status", result.events())["tasks"][0]["runs"][0]
    assert run_again == run


def test_a_fault_in_the_model_script_is_reported_with_a_fixed_code(env: Env) -> None:
    class Fault:
        def __init__(self, real: Any) -> None:
            self.real = real

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("free text that must not be shown \x1b[2J")

    code, result = env.shell(["start report", "quit"], model_wrapper=Fault)
    outcome = result.kinds("outcome")[-1]
    assert outcome["run_status"] == "RECONCILIATION_REQUIRED"
    assert "free text" not in result.out + result.err
    assert code == 3


def test_the_demo_model_is_the_fake_provider() -> None:
    from agent_core_cli import demo

    fault = FakeFault(kind="fault", fault=FakeFaultKind.OUTCOME_UNKNOWN, elapsed_ms=1)
    assert fault.fault is FakeFaultKind.OUTCOME_UNKNOWN
    assert demo.MODEL_PROFILE.provider.value == "FAKE"
    assert demo.PRICE_TABLE.status == "test"
