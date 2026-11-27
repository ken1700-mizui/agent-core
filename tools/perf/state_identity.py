"""P-7 S1 cross-version identity check (development tool, not part of Agent Core).

``generate DIR [--scale N]``: write the identity scenarios of
``tests/support/identity_world`` (shapes A, B, C, a stop, a recovery, an unknown
outcome, a late reconciliation) into ``DIR/<name>/journal.db`` with this version
of the code; with ``--scale N`` only shapes A, B and C, N times larger.

``digest DIR``: copy each journal to a temporary directory, open it, rebuild the
Runtime from it (``RuntimeStateMachine.load``) and print one JSON line with the
digest of its meaning (``identity_world.semantic``), of its cost side and head
(``identity_world.ledger_view``) and the event count.

Generate once with the version before a change, digest with that version and
again with the version after it: equal digests mean the same journals give the
same state. The ledger keys are derived from fixed labels: synthetic test keys,
never used anywhere else. Run with ``PYTHONPATH=src:tools:tests``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from agent_core.types.safety import LedgerKeys, SecretKey
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w


def _keys() -> LedgerKeys:
    def key(label: str) -> SecretKey:
        return SecretKey(hashlib.sha256(f"p7-identity-synthetic-{label}".encode()).digest())

    return LedgerKeys(owner=key("owner"), permit=key("permit"))


SIZES = {"A": ("steps", 40), "B": ("runs", 12), "C": ("runs", 3)}


def generate(out: Path, scale: int) -> None:
    for name, scenario in sorted(iw.SCENARIOS.items()):
        if scale > 1 and name not in SIZES:
            continue  # the other scenarios have one size
        target = out / name
        target.mkdir(parents=True)
        st = lw.setup(target, keys=_keys())
        if name in SIZES:
            field, base = SIZES[name]
            st = scenario(st, **{field: base * scale})
        else:
            st = scenario(st)
        st.journal.close()


def _count(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def digest(source: Path) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for name in sorted(p.name for p in source.iterdir() if p.is_dir()):
        with tempfile.TemporaryDirectory(prefix="p7-identity-") as tmp:
            path = Path(tmp) / "journal.db"
            shutil.copyfile(source / name / "journal.db", path)
            clock = lw.ManualClock()
            keys = _keys()
            journal = lw.open_journal(path, clock, keys)
            try:
                st = lw.Setup(path, journal, w.machine(journal), keys, clock, None)  # type: ignore[arg-type]
                found[name] = {
                    "events": _count(path),
                    "semantic": iw.digest(iw.semantic(st.sm.state)),
                    "ledger": iw.digest(iw.ledger_view(st)),
                }
            finally:
                journal.close()
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "digest"))
    parser.add_argument("dir", type=Path)
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.dir, args.scale)
    else:
        print(json.dumps(digest(args.dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
