"""The CLI's entry point: ``init``, ``status`` and the interactive ``shell``.

Exit codes (``EXIT_*``):

- 0 success (for ``shell``: the last Run the session acted on is COMPLETED or
  CANCELLED, or there is none);
- 1 failure (a refused command such as an existing home; for ``shell``: the
  last Run FAILED or ABORTED_SAFETY);
- 2 invalid command line;
- 3 waiting (for ``shell``: the last Run is left WAITING_APPROVAL,
  PAUSED_BUDGET, CANCELLING, RECONCILIATION_REQUIRED or RUNNING);
- 4 another Runtime holds the home's lock;
- 5 an unexpected error (shown by a fixed code and an exception class name);
- 130 interrupted (Ctrl+C).

Inside the shell, a refused line prints an ``error`` event with a fixed code
and the shell goes on; an unexpected error ends the shell (exit 5). EOF is a
normal end: nothing that waits is approved.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, NoReturn, TextIO

from agent_core.adapters.system import SystemClock
from agent_core.ports.clock import Clock
from agent_core.types.errors import JournalError, RuntimeLockHeldError
from agent_core.types.ids import RunId
from agent_core.types.records import RunStatus
from agent_core_cli import demo, views
from agent_core_cli.environment import (
    CliError,
    Hooks,
    Session,
    Snapshot,
    init_home,
    open_session,
    open_snapshot,
)
from agent_core_cli.render import Printer, clean

__all__ = ["Hooks", "main"]

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1
EXIT_USAGE: Final = 2
EXIT_WAITING: Final = 3
EXIT_LOCKED: Final = 4
EXIT_ERROR: Final = 5
EXIT_INTERRUPTED: Final = 130

_HASH: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_DONE_OK: Final = frozenset({RunStatus.COMPLETED, RunStatus.CANCELLED})
_DONE_FAILED: Final = frozenset({RunStatus.FAILED, RunStatus.ABORTED_SAFETY})
HELP: Final = (
    "commands: start <" + "|".join(demo.DEMOS) + ">; status; steps [run]; budget [run]; "
    "pending; approve <action_hash>; stop run [run|last]; stop tenant; stop global; stops; "
    "resume-scope run <run> <stop_seq>; resume-scope tenant|global <stop_seq>; help; quit"
)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse that never exits the process and writes to the given streams."""

    def __init__(self, *args: Any, out: TextIO, err: TextIO, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._out = out
        self._err = err

    def error(self, message: str) -> NoReturn:
        self._err.write("error: USAGE_INVALID_ARGUMENTS\n")
        self._err.write(clean(self.format_usage().strip()) + "\n")
        raise _UsageError

    def _print_message(self, message: str, file: Any = None) -> None:
        if message:
            (self._err if file is sys.stderr else self._out).write(clean(message) + "\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._err.write(clean(message) + "\n")
        raise SystemExit(status)


def _parser(out: TextIO, err: TextIO) -> _Parser:
    parser = _Parser(
        prog="agent-core-cli",
        description="Agent Core Phase 1 CLI: one synthetic Runtime (Fake model, synthetic data).",
        out=out,
        err=err,
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name, text in (
        ("init", "create a new synthetic home"),
        ("status", "show the Tasks and Runs (takes the Runtime lock briefly)"),
        ("shell", "open the Runtime and read commands (one session)"),
    ):
        cmd = sub.add_parser(name, help=text, out=out, err=err, allow_abbrev=False)
        cmd.add_argument("--home", required=True, type=Path, help="the synthetic home directory")
        if name != "init":
            cmd.add_argument("--json", action="store_true", help="fixed JSON lines")
    return parser


def main(
    argv: list[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
    reader: Callable[[], str] | None = None,
    clock: Clock | None = None,
    monotonic: Callable[[], int] | None = None,
    hooks: Hooks | None = None,
) -> int:
    try:
        args = _parser(stdout, stderr).parse_args(argv)
    except _UsageError:
        return EXIT_USAGE
    except SystemExit as done:  # --help
        return done.code if isinstance(done.code, int) else EXIT_USAGE
    the_clock: Clock = clock if clock is not None else SystemClock()
    the_monotonic = (
        monotonic if monotonic is not None else (lambda: time.monotonic_ns() // 1_000_000)
    )
    home: Path = args.home
    try:
        if args.command == "init":
            init_home(home, clock=the_clock)
            stdout.write("initialized a synthetic home\n")
            return EXIT_OK
        if args.command == "status":
            opened: Session | Snapshot = open_snapshot(home, clock=the_clock)
        else:
            opened = open_session(home, clock=the_clock, monotonic=the_monotonic, hooks=hooks)
    except BaseException as exc:  # noqa: BLE001 (every failure has a fixed code)
        return _refused(stderr, exc)
    printer = Printer(stdout, as_json=bool(args.json))
    try:
        if isinstance(opened, Snapshot):
            printer.event("status", **views.status(opened))
            code = EXIT_OK
        else:
            code = _Shell(opened, printer, reader, stdout).run()
    except BaseException as exc:  # noqa: BLE001 (reported by a fixed code; closed below)
        code = _refused(stderr, exc)
        _close(opened, stderr, original=exc)
        return code
    return _close(opened, stderr, original=None) or code


def _refused(stderr: TextIO, exc: BaseException) -> int:
    """A fixed code for every failure; never a free message."""
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    if isinstance(exc, CliError):
        stderr.write(f"error: {exc.code}\n")
        return EXIT_FAILURE
    if isinstance(exc, RuntimeLockHeldError):
        stderr.write("error: RUNTIME_LOCKED\n")
        return EXIT_LOCKED
    if isinstance(exc, JournalError):
        stderr.write(f"error: JOURNAL_UNAVAILABLE ({clean(type(exc).__name__)})\n")
        return EXIT_ERROR
    stderr.write(f"error: INTERNAL_ERROR ({clean(type(exc).__name__)})\n")
    return EXIT_ERROR


def _close(opened: Session | Snapshot, stderr: TextIO, *, original: BaseException | None) -> int:
    """Close; a failure here never hides an earlier one (it is only noted)."""
    try:
        opened.close()
    except Exception as exc:  # noqa: BLE001 (noted with a fixed code)
        stderr.write(f"note: CLOSE_FAILED ({clean(type(exc).__name__)})\n")
        return EXIT_ERROR if original is None else 0
    return 0


MAX_LINE: Final = 4_096


def _bounded_stdin_line() -> str:
    """One line of at most ``MAX_LINE`` characters; a longer line is read to its
    end in bounded pieces and returned as an over-long marker (never kept)."""
    line = sys.stdin.readline(MAX_LINE + 1)
    if len(line) <= MAX_LINE or line.endswith("\n"):
        return line
    while True:
        rest = sys.stdin.readline(MAX_LINE)
        if not rest or rest.endswith("\n"):
            return "x" * (MAX_LINE + 1)


class _Shell:
    def __init__(
        self,
        session: Session,
        printer: Printer,
        reader: Callable[[], str] | None,
        stdout: TextIO,
    ) -> None:
        self._session = session
        self._printer = printer
        self._interactive = reader is None and sys.stdin.isatty()
        self._read = reader if reader is not None else _bounded_stdin_line
        self._stdout = stdout
        self._acted: RunId | None = None

    def run(self) -> int:
        code: int | None = None
        try:
            while code is None:
                if self._interactive:
                    self._stdout.write("agent-core> ")
                    self._stdout.flush()
                line = self._read()
                if line == "":
                    break  # EOF: a normal end; nothing waiting is approved
                if len(line) > MAX_LINE:
                    # Never parsed: bounded memory and time (WP10-R04).
                    self._printer.event("error", code="USAGE_LINE_TOO_LONG", detail=None)
                    continue
                code = self._line(line)
        except KeyboardInterrupt:
            code = EXIT_INTERRUPTED
        final = self._exit_code() if code is None else code
        self._printer.event("bye", exit_code=final)
        return final

    def _exit_code(self) -> int:
        if self._acted is None:
            return EXIT_OK
        status = self._session.sm.state.runs[self._acted].status
        if status in _DONE_OK:
            return EXIT_OK
        if status in _DONE_FAILED:
            return EXIT_FAILURE
        return EXIT_WAITING

    def _line(self, line: str) -> int | None:
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            self._printer.event("error", code="USAGE_INVALID_ARGUMENTS", detail=None)
            return None
        if not words:
            return None
        command, rest = words[0], words[1:]
        handler = _COMMANDS.get(command)
        if handler is None:
            self._printer.event("error", code="USAGE_INVALID_COMMAND", detail=None)
            return None
        try:
            return handler(self, rest)
        except CliError as refused:
            self._printer.event("error", code=refused.code, detail=refused.detail)
            return None
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 (fail closed: the shell ends)
            self._printer.event("error", code="INTERNAL_ERROR", detail=type(exc).__name__)
            return EXIT_ERROR

    # -------------------------------------------------------- commands

    def _outcome(self, run_id: RunId, result: Any) -> None:
        self._acted = run_id
        self._printer.event("outcome", **views.outcome(run_id, result))
        for attempt in self._session.pending():
            if self._session.sm.state.steps[attempt.step_id].run_id == run_id:
                self._printer.event(
                    "approval_request", **views.approval_screen(self._session, attempt)
                )

    def start(self, rest: list[str]) -> None:
        if len(rest) != 1:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        task, run, result = self._session.start(rest[0])
        self._printer.event(
            "started", demo=rest[0], task_id=str(task.task_id), run_id=str(run.run_id)
        )
        self._outcome(run.run_id, result)

    def status(self, rest: list[str]) -> None:
        if rest:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        self._printer.event("status", **views.status(self._session))

    def _run_ref(self, rest: list[str]) -> RunId:
        if len(rest) > 1:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        return self._session.resolve_run(rest[0] if rest else None)

    def steps(self, rest: list[str]) -> None:
        self._printer.event("steps", **views.steps(self._session, self._run_ref(rest)))

    def budget(self, rest: list[str]) -> None:
        self._printer.event("budget", **views.budget(self._session, self._run_ref(rest)))

    def pending(self, rest: list[str]) -> None:
        if rest:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        self._printer.event("pending", **views.pending(self._session))

    def approve(self, rest: list[str]) -> None:
        if len(rest) != 1 or _HASH.fullmatch(rest[0]) is None:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        waiting = [a for a in self._session.pending() if a.action_hash == rest[0]]
        result = self._session.approve(rest[0])
        run_id = self._session.sm.state.steps[waiting[0].step_id].run_id
        self._outcome(run_id, result)

    def stop(self, rest: list[str]) -> None:
        if not rest or rest[0] not in ("run", "tenant", "global"):
            raise CliError("USAGE_INVALID_ARGUMENTS")
        kind = rest[0]
        if kind == "run":
            run_id: RunId | None = self._run_ref(rest[1:])
        elif len(rest) != 1:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        else:
            run_id = None
        scope_type, scope_id = self._session.scope_id(kind, run_id)
        results = self._session.stop(kind, run_id)
        self._printer.event("stopped", scope_type=scope_type.value, scope_id=scope_id)
        for target, result in results:
            self._outcome(target, result)

    def stops(self, rest: list[str]) -> None:
        if rest:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        self._printer.event("stops", **views.stops(self._session))

    def resume_scope(self, rest: list[str]) -> None:
        if not rest or rest[0] not in ("run", "tenant", "global"):
            raise CliError("USAGE_INVALID_ARGUMENTS")
        expected = 3 if rest[0] == "run" else 2
        if len(rest) != expected or not rest[-1].isdigit():
            raise CliError("USAGE_INVALID_ARGUMENTS")
        run_id = self._session.resolve_run(rest[1]) if rest[0] == "run" else None
        self._session.resume_scope(rest[0], run_id, int(rest[-1]))
        self._printer.event("stops", **views.stops(self._session))

    def help(self, rest: list[str]) -> None:
        self._printer.event("help", text=HELP)

    def quit(self, rest: list[str]) -> int | None:
        if rest:
            raise CliError("USAGE_INVALID_ARGUMENTS")
        return self._exit_code()


_COMMANDS: Final[dict[str, Callable[[_Shell, list[str]], int | None]]] = {
    "start": _Shell.start,
    "status": _Shell.status,
    "steps": _Shell.steps,
    "budget": _Shell.budget,
    "pending": _Shell.pending,
    "approve": _Shell.approve,
    "stop": _Shell.stop,
    "stops": _Shell.stops,
    "resume-scope": _Shell.resume_scope,
    "help": _Shell.help,
    "quit": _Shell.quit,
    "exit": _Shell.quit,
}
