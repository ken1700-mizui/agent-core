"""Fixed local child process for WP-08 write crash tests (tests only; synthetic).

Usage: python file_child.py <point> <db_path> <attempt_id> <pep> <area> <workspace_id>
stdin: four lines in hex: the owner key, the permit key, the canonical tool
input of the attempt, and the bytes to write.

The attempt is at INTENT_RECORDED. The child is the owner now: it issues and
consumes the permit, then runs the write through ``run_file_operation`` and
stops itself with SIGKILL (a real process stop: no cleanup, no close) at
``point``:

- ``after-temp-create``: right after the temporary file was created (empty)
- ``after-temp-write``: after the content was written, before its fsync
- ``before-commit``: after the fsync, right before the link (CREATE) or rename
  (REPLACE) that commits it
- ``after-commit``: right after the link or rename, before anything else
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any

from agent_core.adapters.file_broker import SyntheticFileBroker
from agent_core.runtime.file_execution import run_file_operation
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.workspace import manifest_key
from agent_core.types.files import TEMP_FILE_PREFIX
from agent_core.types.ids import AttemptId, PepInstanceId
from agent_core.types.safety import LedgerKeys, SecretKey, ToolContent
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s


def _die() -> None:
    print("stopping", flush=True)
    os.kill(os.getpid(), signal.SIGKILL)


def _install(point: str) -> None:
    real_open, real_fsync = os.open, os.fsync
    real_link, real_rename = os.link, os.rename
    state: dict[str, Any] = {"temp_fd": None}

    def open_(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, str) and path.startswith(TEMP_FILE_PREFIX):
            state["temp_fd"] = fd
            if point == "after-temp-create":
                _die()
        return fd

    def fsync(fd: int) -> None:
        if fd == state["temp_fd"] and point == "after-temp-write":
            _die()
        real_fsync(fd)

    def commit(real: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            if point == "before-commit":
                _die()
            result = real(*args, **kwargs)
            if point == "after-commit":
                _die()
            return result

        return call

    # Test-only fault points in this child process.
    for name, replacement in (
        ("open", open_),
        ("fsync", fsync),
        ("link", commit(real_link)),
        ("rename", commit(real_rename)),
    ):
        setattr(os, name, replacement)


def main() -> int:
    point, db_path, attempt_id, pep = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    area, workspace_id = sys.argv[5], sys.argv[6]
    owner_hex, permit_hex = sys.stdin.readline().strip(), sys.stdin.readline().strip()
    canonical_input = bytes.fromhex(sys.stdin.readline().strip()).decode()
    content = bytes.fromhex(sys.stdin.readline().strip())
    keys = LedgerKeys(
        owner=SecretKey(bytes.fromhex(owner_hex)), permit=SecretKey(bytes.fromhex(permit_hex))
    )
    clock = lw.ManualClock()
    journal = lw.open_journal(db_path, clock, keys)
    st = lw.Setup(
        db_path, journal, w.machine(journal), keys, clock, OwnerApprovalChannel(keys.owner)
    )
    st.pep = PepInstanceId(pep)
    attempt = lw.consume(st, lw.issue(st, st.sm.state.attempts[AttemptId(attempt_id)]))
    print("consumed", flush=True)
    broker = SyntheticFileBroker(area=area, workspace_id=workspace_id, key=manifest_key(keys.owner))
    _install(point)
    result = run_file_operation(
        gate=lw.gate(st),
        ledger=st.journal,
        files=broker,
        registry=s.REGISTRY,
        attempt=attempt,
        content=ToolContent(kind="tool", canonical_input=canonical_input),
        registration=s.WRITE_TOOL,
        write_content=content,
    )
    print("finished", result.attempt.state, flush=True)  # not reached when stopped
    return 3


if __name__ == "__main__":
    sys.exit(main())
