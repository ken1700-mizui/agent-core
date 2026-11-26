"""Local JSON Lines log sink (WP-11).

- Appends complete lines to one local file in a trusted directory; never
  sends anything over a network.
- The file is opened without following a link (``O_NOFOLLOW``) and must be a
  regular file with one link; it is created with mode 0600.
- Each line must end with exactly one newline. The total size is capped
  (``max_total_bytes``); a line that would exceed the cap is refused whole
  (``LogCapacityError``), so no partial line is written. There is no rotation.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from agent_core.types.errors import LogCapacityError, LogWriteError

_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC


class JsonLinesLogSink:
    def __init__(self, path: Path, *, max_total_bytes: int) -> None:
        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise LogWriteError("max_total_bytes must be a positive int")
        try:
            fd = os.open(path, _FLAGS, 0o600)
        except OSError as exc:
            raise LogWriteError(f"cannot open log file: {exc.strerror}") from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise LogWriteError("log file must be a regular file with one link")
        self._fd: int | None = fd
        self._size = info.st_size
        self._max_total_bytes = max_total_bytes

    def write(self, line: bytes) -> None:
        if self._fd is None:
            raise LogWriteError("log sink is closed")
        if type(line) is not bytes or len(line) < 2 or not line.endswith(b"\n"):
            raise LogWriteError("a log line must end with one newline")
        if line.count(b"\n") != 1:
            raise LogWriteError("a log line must not contain other newlines")
        if self._size + len(line) > self._max_total_bytes:
            raise LogCapacityError("log storage cap reached")
        try:
            written = os.write(self._fd, line)
        except OSError as exc:
            raise LogWriteError(f"log write failed: {exc.strerror}") from exc
        if written != len(line):
            raise LogWriteError("partial log write")
        self._size += written

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)
