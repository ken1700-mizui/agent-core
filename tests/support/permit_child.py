"""Fixed local child process for WP-09C crash tests (tests only; synthetic keys).

Usage: python permit_child.py <mode> <db_path> <attempt_id> <pep_instance_id>
stdin: two lines, the synthetic owner key and permit key in hex.

The attempt is at INTENT_RECORDED. The child is the owner now, so it issues the
permit itself (a permit of an earlier owner epoch is stale), then:

- ``consume-then-exit``: consumes it and ends the process at once with
  ``os._exit`` (no close, no cleanup) right after the COMMIT.
- ``exit-inside-consume``: ends the process with ``os._exit`` inside the
  consumption transaction, after the permit row was updated and before COMMIT.
- ``start-then-exit``: consumes, claims and starts through the execution gate,
  then ends the process right after the start was committed, before the
  synthetic callback could run (WP09-R03).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_core.adapters import sqlite_ledger
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.types.ids import AttemptId, PepInstanceId
from agent_core.types.safety import LedgerKeys, SecretKey, ToolContent
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s
from support import tool_world as tw


def main() -> int:
    mode, db_path, attempt_id, pep = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    owner_hex, permit_hex = sys.stdin.readline().strip(), sys.stdin.readline().strip()
    keys = LedgerKeys(
        owner=SecretKey(bytes.fromhex(owner_hex)), permit=SecretKey(bytes.fromhex(permit_hex))
    )
    clock = lw.ManualClock()
    journal = lw.open_journal(db_path, clock, keys)
    st = lw.Setup(
        db_path, journal, w.machine(journal), keys, clock, OwnerApprovalChannel(keys.owner)
    )
    st.pep = PepInstanceId(pep)
    attempt = lw.issue(st, st.sm.state.attempts[AttemptId(attempt_id)])
    print("issued", flush=True)
    if mode == "exit-inside-consume":

        def die(*_args: object, **_kwargs: object) -> None:
            print("in-transaction", flush=True)
            os._exit(7)

        sqlite_ledger.consume_reservation = die  # test-only fault point
    consumed = lw.consume(st, attempt)
    print("consumed", flush=True)
    if mode == "start-then-exit":
        gate = lw.gate(st)
        content = ToolContent(kind="tool", canonical_input=tw.SYNTHETIC_INPUT)
        claimed = gate.claim(consumed, content=content, registration=s.SYNTH_TOOL)
        gate.start(claimed)
        print("started", flush=True)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
