"""P2's execution record (sandbox design 7.6.1; B1A-01, B1A-02). Not the Journal.

Files (one directory, owned by the P2 user):

- ``<record>``: append-only JSON lines. Each entry has ``seq``, ``prev`` (the
  previous entry's hash), its own ``hash`` and a ``mac`` (HMAC with the broker
  key over the hash). The genesis entry carries a random ``record_id``.
- ``<record>.head``: the last entry's ``seq`` and ``hash`` with the
  ``record_id`` and a MAC, replaced atomically (write, fsync, rename, fsync of
  the directory) after every entry.
- ``<record>.lock``: an exclusive ``flock`` held while the record is open,
  taken **before** anything is read. Released by the OS when the process ends.

Every entry is written and ``fsync``-ed before the caller goes on; then the head.
What is found when the record is opened (``ExecutionRecordError``):

- an entry changed or reordered: chain and MAC;
- complete entries lost at the end: the head names a later entry;
- a different record put in its place: ``record_id`` and MACs;
- a missing or forged head.

A crash between an entry and its head leaves the head exactly one entry
behind: it is moved forward. A partial last line (a write that never finished,
so never acknowledged) is cut off and reported (``repaired_partial_tail``).

After a write or fsync failure the record is not used again by this process
(``broken``): what reached the disk is checked at the next open, where an entry
that was written counts (a Receipt it names is never run again).

Not found, and not claimed: a consistent roll-back of the record and its head
to an older genuine copy, or any change by someone who holds the key (root).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import ReceiptId
from agent_core.types.safety import SecretKey
from agent_core.types.sandbox import SandboxState, StartRequest

_DOMAIN = b"agent-core/p2-execution-record/v2\x00"
_MAC_DOMAIN = b"agent-core/p2-execution-record-mac/v2\x00"
FORMAT = "agent-core-p2-execution-record-v2"
_STATE_FIELDS = frozenset(
    {"receipt_id", "attempt_id", "request_id", "permit_id", "owner_epoch", "state"}
)


class ExecutionRecordError(AgentCoreError):
    """The execution record cannot be used. Every start is refused."""

    code = "EXECUTION_RECORD_UNAVAILABLE"
    fatal = True


class ExecutionRecordBusyError(ExecutionRecordError):
    """Another broker holds the record (B1A-02)."""

    code = "EXECUTION_RECORD_BUSY"


def entry_hash(entry: dict[str, Any]) -> str:
    fields = {k: v for k, v in entry.items() if k not in ("hash", "mac")}
    data = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(_DOMAIN + data).hexdigest()


def _mac(key: SecretKey, text: str) -> str:
    return hmac.new(key.reveal(), _MAC_DOMAIN + text.encode(), hashlib.sha256).hexdigest()


def head_path(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _sync_dir(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock(path: Path) -> int:
    fd = os.open(lock_path(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ExecutionRecordBusyError("another broker holds the execution record") from exc
    return fd


def head_document(key: SecretKey, record_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The head naming ``entry`` (MAC over record_id, seq and hash)."""
    body = f"{record_id}:{entry['seq']}:{entry['hash']}"
    return {
        "record_id": record_id,
        "seq": entry["seq"],
        "hash": entry["hash"],
        "mac": _mac(key, body),
    }


def read_entries(path: Path) -> list[dict[str, Any]]:
    """The entries as stored, for diagnosis only: no lock, no checks."""
    lines = path.read_bytes().splitlines()
    return [json.loads(line) for line in lines if line.endswith(b"}")]


class ExecutionRecord:
    def __init__(
        self, path: Path, key: SecretKey, lock_fd: int, entries: list[dict[str, Any]]
    ) -> None:
        self._path = path
        self._key = key
        self._lock_fd: int | None = lock_fd
        self.entries = entries
        self.record_id = str(entries[0]["record_id"])
        self.epoch = 0
        self.broken: str | None = None
        self.repaired_partial_tail = False
        self._latest: dict[str, dict[str, Any]] = {}
        for entry in entries:
            self._index(entry)

    # ------------------------------------------------------------ files

    @classmethod
    def create(cls, path: Path, key: SecretKey) -> ExecutionRecord:
        lock = _lock(path)
        try:
            genesis: dict[str, Any] = {
                "seq": 1,
                "kind": "genesis",
                "format": FORMAT,
                "record_id": os.urandom(16).hex(),
                "prev": None,
            }
            genesis["hash"] = entry_hash(genesis)
            genesis["mac"] = _mac(key, genesis["hash"])
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, json.dumps(genesis, sort_keys=True).encode() + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            _sync_dir(path)
            record = cls(path, key, lock, [genesis])
            record._write_head()
        except BaseException:
            os.close(lock)
            raise
        return record

    @classmethod
    def open(cls, path: Path, key: SecretKey) -> ExecutionRecord:
        lock = _lock(path)  # before anything is read (B1A-02)
        try:
            return cls._open_locked(path, key, lock)
        except BaseException:
            os.close(lock)
            raise

    @classmethod
    def _open_locked(cls, path: Path, key: SecretKey, lock: int) -> ExecutionRecord:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExecutionRecordError("the execution record cannot be read") from exc
        repaired = False
        if data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                raise ExecutionRecordError("the execution record has no complete entry")
            with path.open("r+b") as handle:  # cut off a write that never finished
                handle.truncate(cut)
                handle.flush()
                os.fsync(handle.fileno())
            data, repaired = data[:cut], True
        entries = cls._verify(data, key)
        record = cls(path, key, lock, entries)
        record.repaired_partial_tail = repaired
        record._check_head()
        return record

    @staticmethod
    def _verify(data: bytes, key: SecretKey) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        previous: str | None = None
        for number, line in enumerate(data.splitlines(), start=1):
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise ExecutionRecordError("an execution record entry is not JSON") from exc
            if not isinstance(entry, dict) or entry.get("seq") != number:
                raise ExecutionRecordError("the execution record entries are not in order")
            if entry.get("prev") != previous or entry.get("hash") != entry_hash(entry):
                raise ExecutionRecordError("an execution record entry does not match its chain")
            mac = entry.get("mac")
            if not isinstance(mac, str) or not hmac.compare_digest(mac, _mac(key, entry["hash"])):
                raise ExecutionRecordError("an execution record entry has no valid MAC")
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
        return entries

    def _check_head(self) -> None:
        try:
            head = json.loads(head_path(self._path).read_text())
        except (OSError, ValueError) as exc:
            raise ExecutionRecordError("the execution record head is missing") from exc
        body = f"{head.get('record_id')}:{head.get('seq')}:{head.get('hash')}"
        mac = head.get("mac")
        if not isinstance(mac, str) or not hmac.compare_digest(mac, _mac(self._key, body)):
            raise ExecutionRecordError("the execution record head has no valid MAC")
        if head.get("record_id") != self.record_id:
            raise ExecutionRecordError("the head names another record_id")
        seq = head.get("seq")
        last = len(self.entries)
        if not isinstance(seq, int) or seq < 1 or seq > last:
            raise ExecutionRecordError("entries named by the head are missing")
        if self.entries[seq - 1]["hash"] != head.get("hash"):
            raise ExecutionRecordError("the head does not match its entry")
        if last - seq > 1:
            raise ExecutionRecordError("the head is more than one entry behind")
        if last - seq == 1:
            self._write_head()  # a crash between the entry and its head

    def _write_head(self) -> None:
        head = head_document(self._key, self.record_id, self.entries[-1])
        target = head_path(self._path)
        temporary = target.with_name(target.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(head, sort_keys=True).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, target)
        _sync_dir(target)

    def close(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _append(self, entry: dict[str, Any]) -> None:
        if self.broken is not None or self._lock_fd is None:
            raise ExecutionRecordError(f"the execution record is no longer used: {self.broken}")
        entry = {**entry, "seq": len(self.entries) + 1, "prev": self.entries[-1]["hash"]}
        entry["hash"] = entry_hash(entry)
        entry["mac"] = _mac(self._key, entry["hash"])
        line = json.dumps(entry, sort_keys=True).encode() + b"\n"
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND)
            try:
                if os.write(fd, line) != len(line):
                    raise OSError("a partial execution record entry was written")
                os.fsync(fd)
            finally:
                os.close(fd)
            self.entries.append(entry)
            self._index(entry)
            self._write_head()
        except OSError as exc:
            self.broken = f"{type(exc).__name__}: {exc}"
            raise ExecutionRecordError("the execution record cannot be written") from exc

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

    def accept(self, start: StartRequest, *, started_ms: int = 0, **extra: Any) -> None:
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
                "grace_ms": start.grace_ms,
                "started_ms": started_ms,
                **extra,
            }
        )

    def update(self, receipt_id: ReceiptId, **values: Any) -> None:
        """Facts about a Receipt that are not a state change (container, containment)."""
        found = self.latest(receipt_id)
        if found is None:
            raise ExecutionRecordError("no such Receipt in the execution record")
        self._append({"kind": "state", **{k: found[k] for k in sorted(_STATE_FIELDS)}, **values})

    def _state(self, receipt_id: ReceiptId, state: SandboxState, **values: Any) -> None:
        found = self.latest(receipt_id)
        if found is None:
            raise ExecutionRecordError("no such Receipt in the execution record")
        fields = {k: found[k] for k in sorted(_STATE_FIELDS - {"state"})}
        self._append({"kind": "state", "state": state.value, **fields, **values})

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
