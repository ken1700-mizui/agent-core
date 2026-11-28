"""P2's execution record (sandbox design 7.6.1). Not the Journal.

An append-only file of JSON lines, one entry each, chained by hash. Every
append is written and ``fsync``-ed before the caller goes on: an ``ACCEPTED``
entry is durable before anything runs. ``create`` makes the file (with its
genesis entry) once; ``open`` requires it and checks every entry. A missing,
truncated, edited or reordered file raises ``ExecutionRecordError``, and the
broker then refuses every start (it cannot know which Receipts were used).

Entries: ``genesis``; ``epoch`` (a higher owner epoch seen in an authenticated
request); ``state`` (``ACCEPTED`` with the start's identifiers and deadline,
then ``FINISHED`` with exit code, output reference and wall time, ``STOPPED``,
``REAPED``). The record never forgets a Receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import SandboxState, StartRequest

_DOMAIN = b"agent-core/p2-execution-record/v1\x00"
FORMAT = "agent-core-p2-execution-record"
_STATE_FIELDS = frozenset(
    {"receipt_id", "attempt_id", "request_id", "permit_id", "owner_epoch", "state"}
)


class ExecutionRecordError(AgentCoreError):
    """The execution record cannot be used. Every start is refused."""

    code = "EXECUTION_RECORD_UNAVAILABLE"
    fatal = True


def _canonical(entry: dict[str, Any]) -> bytes:
    fields = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def _hash(entry: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_DOMAIN + _canonical(entry)).hexdigest()


def _sync_dir(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ExecutionRecord:
    def __init__(self, path: Path, entries: list[dict[str, Any]]) -> None:
        self._path = path
        self.entries = entries
        self.epoch = 0
        self._latest: dict[str, dict[str, Any]] = {}
        for entry in entries:
            self._index(entry)

    # ------------------------------------------------------------ files

    @classmethod
    def create(cls, path: Path) -> ExecutionRecord:
        genesis: dict[str, Any] = {"seq": 1, "kind": "genesis", "format": FORMAT, "prev": None}
        genesis["hash"] = _hash(genesis)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(genesis, sort_keys=True).encode() + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_dir(path)
        return cls(path, [genesis])

    @classmethod
    def open(cls, path: Path) -> ExecutionRecord:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExecutionRecordError("the execution record cannot be read") from exc
        if not data.endswith(b"\n"):
            raise ExecutionRecordError("the execution record ends in a partial entry")
        entries: list[dict[str, Any]] = []
        previous: str | None = None
        for number, line in enumerate(data.splitlines(), start=1):
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise ExecutionRecordError("an execution record entry is not JSON") from exc
            if not isinstance(entry, dict) or entry.get("seq") != number:
                raise ExecutionRecordError("the execution record entries are not in order")
            if entry.get("prev") != previous or entry.get("hash") != _hash(entry):
                raise ExecutionRecordError("an execution record entry does not match its chain")
            if number == 1 and (entry.get("kind"), entry.get("format")) != ("genesis", FORMAT):
                raise ExecutionRecordError("the execution record has no genesis entry")
            if number > 1 and entry.get("kind") not in ("epoch", "state"):
                raise ExecutionRecordError("unknown execution record entry")
            if entry.get("kind") == "state" and not set(entry) >= _STATE_FIELDS:
                raise ExecutionRecordError("an execution record state entry is incomplete")
            entries.append(entry)
            previous = entry["hash"]
        if not entries:
            raise ExecutionRecordError("the execution record is empty")
        return cls(path, entries)

    def _append(self, entry: dict[str, Any]) -> None:
        entry = {**entry, "seq": len(self.entries) + 1, "prev": self.entries[-1]["hash"]}
        entry["hash"] = _hash(entry)
        line = json.dumps(entry, sort_keys=True).encode() + b"\n"
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND)
            try:
                written = os.write(fd, line)
                if written != len(line):
                    raise ExecutionRecordError("a partial execution record entry was written")
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise ExecutionRecordError("the execution record cannot be written") from exc
        self.entries.append(entry)
        self._index(entry)

    def _index(self, entry: dict[str, Any]) -> None:
        if entry["kind"] == "epoch":
            self.epoch = max(self.epoch, int(entry["owner_epoch"]))
        elif entry["kind"] == "state":
            current = self._latest.get(entry["receipt_id"], {})
            self._latest[entry["receipt_id"]] = {**current, **entry}

    # ------------------------------------------------------------ queries

    def latest(self, receipt_id: ReceiptId) -> dict[str, Any] | None:
        return self._latest.get(str(receipt_id))

    def state(self, receipt_id: ReceiptId) -> SandboxState:
        found = self.latest(receipt_id)
        return SandboxState.NOT_SEEN if found is None else SandboxState(found["state"])

    def accepted(self) -> list[dict[str, Any]]:
        return list(self._latest.values())

    # ------------------------------------------------------------ appends

    def raise_epoch(self, owner_epoch: int) -> None:
        if owner_epoch > self.epoch:
            self._append({"kind": "epoch", "owner_epoch": owner_epoch})

    def accept(self, start: StartRequest, *, started_ms: int = 0) -> None:
        """Durable before the run (fsync). A second accept for a Receipt is refused."""
        if self.latest(start.receipt_id) is not None:
            raise ExecutionRecordError("a Receipt is accepted once")
        self._append(
            {
                "kind": "state",
                "state": SandboxState.ACCEPTED.value,
                "receipt_id": str(start.receipt_id),
                "attempt_id": str(start.attempt_id),
                "request_id": str(start.request_id),
                "permit_id": str(start.permit_id),
                "owner_epoch": start.owner_epoch,
                "program_ref": start.program_ref,
                "deadline_ms": start.deadline_ms,
                "started_ms": started_ms,
            }
        )

    def _state(self, receipt_id: ReceiptId, state: SandboxState, **values: Any) -> None:
        found = self.latest(receipt_id)
        if found is None:
            raise ExecutionRecordError("no such Receipt in the execution record")
        self._append(
            {
                "kind": "state",
                "state": state.value,
                **{k: found[k] for k in sorted(_STATE_FIELDS - {"state"})},
                **values,
            }
        )

    def finished(
        self, receipt_id: ReceiptId, *, exit_code: int, output_ref: str, wall_ms: int
    ) -> None:
        self._state(
            receipt_id,
            SandboxState.FINISHED,
            exit_code=exit_code,
            output_ref=output_ref,
            wall_ms=wall_ms,
        )

    def stopped(self, receipt_id: ReceiptId, *, reason: str) -> None:
        self._state(receipt_id, SandboxState.STOPPED, stop_reason=reason)

    def reaped(self, receipt_id: ReceiptId) -> None:
        self._state(receipt_id, SandboxState.REAPED)
