"""The Deadline Supervisor's record: append-only JSON lines, each with the
previous line's hash and an HMAC; synced before any answer that depends on it.
Any line that fails (MAC, chain, parse, a torn last line) makes the whole
record untrusted: the supervisor goes FAIL_SAFE."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

_DOMAIN = b"agent-core/deadline-supervisor-record/v1\x00"
_GENESIS = "0" * 64


class SupervisorRecordError(Exception):
    pass


class SupervisorRecord:
    def __init__(
        self, path: Path, key: bytes, fd: int, entries: list[dict[str, Any]], head: str
    ) -> None:
        self._path = path
        self._key = key
        self._fd = fd
        self.entries = entries
        self._head = head

    @classmethod
    def open(cls, path: Path, key: bytes) -> SupervisorRecord:
        if len(key) < 16:
            raise ValueError("the record key is at least 16 bytes")
        created = not path.exists()
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_mode & 0o077:
                raise SupervisorRecordError("the record is readable by group or others")
            if created:
                dfd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            entries, head = cls._read(path, key)
        except BaseException:
            os.close(fd)
            raise
        return cls(path, key, fd, entries, head)

    @staticmethod
    def _mac(key: bytes, line: dict[str, Any]) -> str:
        body = json.dumps({k: line[k] for k in ("seq", "prev", "event")}, sort_keys=True)
        return hmac.new(key, _DOMAIN + body.encode(), hashlib.sha256).hexdigest()

    @classmethod
    def _read(cls, path: Path, key: bytes) -> tuple[list[dict[str, Any]], str]:
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise SupervisorRecordError("a torn last line")
        entries: list[dict[str, Any]] = []
        head = _GENESIS
        for n, raw in enumerate(data.splitlines()):
            try:
                line = json.loads(raw)
            except ValueError as exc:
                raise SupervisorRecordError(f"line {n} does not parse") from exc
            if not isinstance(line, dict) or line.get("seq") != n or line.get("prev") != head:
                raise SupervisorRecordError(f"line {n} breaks the chain")
            if not hmac.compare_digest(str(line.get("mac")), cls._mac(key, line)):
                raise SupervisorRecordError(f"line {n} fails its MAC")
            entries.append(line["event"])
            head = hashlib.sha256(raw).hexdigest()
        return entries, head

    def append(self, event: dict[str, Any]) -> None:
        line: dict[str, Any] = {"seq": len(self.entries), "prev": self._head, "event": event}
        line["mac"] = self._mac(self._key, line)
        raw = json.dumps(line, sort_keys=True).encode()
        os.write(self._fd, raw + b"\n")
        os.fsync(self._fd)
        self.entries.append(event)
        self._head = hashlib.sha256(raw).hexdigest()

    def close(self) -> None:
        os.close(self._fd)
