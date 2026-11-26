"""Child process for WP-12 kill tests (tests only; never part of agent_core).

Usage: python recovery_child.py <home> <point>

Runs a CLI shell on <home> that starts the ``report`` demo and approves its
write, and stops (prints ``ready`` and sleeps) at <point>, where the parent
kills it with SIGKILL:

- ``model_call``: inside the first model call (DISPATCH_STARTED recorded);
- ``file_prepare``: in the approved write's broker preparation (the permit is
  consumed; the start is not recorded);
- ``file_write``: inside the broker's write (DISPATCH_STARTED recorded);
- ``before_save``: the model's answer arrived; its result is about to be
  saved to the journal (not yet saved);
- ``during_recovery``: inside the owner's ``recover`` (the parent made a Run
  that needs it), after its first change;
- ``after_reserve``: the first model call's budget is reserved; nothing else;
- ``file_committed``: the broker's write has committed the file (rename),
  before the result reaches the journal;
- ``during_repair``: inside ``repair_projections``'s transaction, after the
  mirror was rewritten, before the commit (the parent damaged the mirror).
"""

from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path
from typing import Any

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.runtime import recovery
from agent_core.types.records import AttemptState
from agent_core.types.transitions import AttemptTransition
from agent_core_cli import app

HOME, POINT = Path(sys.argv[1]), sys.argv[2]


def block() -> None:
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    time.sleep(600)


class Model:
    def __init__(self, real: Any) -> None:
        self.real = real

    def complete(self, request: Any) -> Any:
        if POINT == "model_call":
            block()
        return self.real.complete(request)


class Files:
    def __init__(self, real: Any, _session: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def prepare(self, descriptor: Any, policy: Any) -> Any:
        if POINT == "file_prepare":
            block()
        prepared = self._real.prepare(descriptor, policy)
        if POINT not in ("file_write", "file_committed"):
            return prepared

        class Prepared:
            def __getattr__(self, name: str) -> Any:
                return getattr(prepared, name)

            def write(self, *args: Any) -> Any:
                if POINT == "file_write":
                    block()
                result = prepared.write(*args)
                block()  # file_committed: the rename is done, the journal not yet
                return result

        return Prepared()


def main() -> int:
    if POINT == "during_repair":
        from agent_core.adapters import sqlite_ledger
        from agent_core.adapters.system import SystemClock

        real_repair = sqlite_ledger.repair_mirror

        def repair(cur: Any) -> Any:
            result = real_repair(cur)
            block()  # inside the transaction, before COMMIT
            return result

        sqlite_ledger.repair_mirror = repair
        SqliteJournal.repair_projections(HOME / "journal.db", clock=SystemClock())
        return 0
    if POINT == "after_reserve":
        from agent_core.runtime.pipeline import AttemptPipeline

        def authorize(self: Any, attempt: Any, consumer: Any) -> Any:
            block()

        AttemptPipeline.authorize = authorize  # type: ignore[method-assign]
    if POINT == "before_save":
        real = SqliteJournal.append_with_ledger

        def append(self: Any, draft: Any, **kwargs: Any) -> Any:
            payload = draft.payload
            for t in getattr(payload, "transitions", ()):
                if isinstance(t, AttemptTransition) and t.after.state is AttemptState.COMPLETED:
                    block()
            return real(self, draft, **kwargs)

        SqliteJournal.append_with_ledger = append  # type: ignore[method-assign]
    if POINT == "during_recovery":
        real_abort = recovery._abort

        def abort(*args: Any) -> None:
            real_abort(*args)
            block()

        recovery._abort = abort  # type: ignore[assignment]
        lines = ["recover all"]
    else:
        lines = ["start report", "APPROVE"]
    out = io.StringIO()

    def reader() -> str:
        if not lines:
            return ""
        line = lines.pop(0)
        if line == "APPROVE":
            found = re.findall(r'"action_hash": "(sha256:[0-9a-f]{64})"', out.getvalue())
            line = f"approve {found[-1]}"
        return line + "\n"

    hooks = app.Hooks(model_wrapper=Model, files_wrapper=Files)
    return app.main(
        ["shell", "--home", str(HOME), "--json"],
        stdout=out,
        stderr=sys.stderr,
        reader=reader,
        hooks=hooks,
    )


if __name__ == "__main__":
    sys.exit(main())
