"""The monitor's liveness record as one MAC'd file, replaced atomically (WP2-04).

Design (P-4 7.5) places it in one overwritten ledger row; that is a change of
the ledger format and is not made here (it needs an approval). This file keeps
the same contract: one record, overwritten, never appended; a missing, altered
or unreadable file is ``ABSENT`` or ``UNAUTHENTIC``, never trusted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from agent_core.types.monitor import Heartbeat
from agent_core.types.safety import SecretKey

_DOMAIN = b"agent-core/monitor-heartbeat/v1\x00"


class HeartbeatFile:
    def __init__(self, path: Path, key: SecretKey) -> None:
        self._path = path
        self._key = key

    def _mac(self, body: bytes) -> str:
        return hmac.new(self._key.reveal(), _DOMAIN + body, hashlib.sha256).hexdigest()

    def write(self, beat: Heartbeat) -> None:
        """Atomic and durable: write a temporary file, fsync, rename, fsync the
        directory. A failure raises (the caller then trusts no liveness)."""
        body = beat.model_dump_json().encode()
        data = json.dumps({"body": body.decode(), "mac": self._mac(body)}).encode()
        temporary = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            if os.write(fd, data) != len(data):
                raise OSError("a partial liveness record was written")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, self._path)
        directory = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def read(self) -> tuple[Heartbeat | None, bool]:
        """(record, authentic). Absent: (None, True). Altered: (None, False)."""
        try:
            info = os.lstat(self._path)
        except FileNotFoundError:
            return None, True
        except OSError:
            return None, False
        # Only a regular file of this user, private to it (S-R03).
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            return None, False
        try:
            raw = json.loads(self._path.read_bytes())
            body = str(raw["body"]).encode()
            mac = str(raw["mac"])
        except FileNotFoundError:
            return None, True
        except (OSError, ValueError, KeyError, TypeError):
            return None, False
        if not hmac.compare_digest(mac, self._mac(body)):
            return None, False
        try:
            return Heartbeat.model_validate_json(body), True
        except ValueError:
            return None, False
