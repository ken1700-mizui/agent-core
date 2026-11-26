"""Fixed local child process for journal tests (tests only; never part of agent_core).

Usage: python journal_child.py <mode> <db_path>

- ``hold``: open the journal, print ``ready <epoch>``, then wait until stdin closes.
- ``append-then-hang``: open, append two events, then block inside the third
  append's transaction (after the event row is written, before the heads are
  updated) after printing ``in-transaction``. The parent kills it with SIGKILL.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.types.ids import EventId
from support import builders as b
from support.fakes import append_next


def main() -> int:
    mode, db_path = sys.argv[1], Path(sys.argv[2])
    journal = SqliteJournal.open(db_path, clock=SystemClock(), random=OsRandomSource())
    # Random event IDs, so they never collide with IDs the parent process builds.
    ids = IdGenerator(b.SITE, SystemClock(), OsRandomSource())
    if mode == "hold":
        print(f"ready {journal.owner_epoch}", flush=True)
        sys.stdin.read()
        journal.close()
        return 0
    if mode == "append-then-hang":
        for _ in range(2):
            append_next(
                journal, b.event_draft(owner_epoch=journal.owner_epoch, event_id=ids.new(EventId))
            )

        def hang(*_args: object, **_kwargs: object) -> None:
            print("in-transaction", flush=True)
            while True:
                time.sleep(60)

        journal._update_heads = hang  # type: ignore[method-assign]  # test-only fault point
        append_next(
            journal, b.event_draft(owner_epoch=journal.owner_epoch, event_id=ids.new(EventId))
        )
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
