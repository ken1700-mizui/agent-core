"""Local JSON Lines log sink (WP-11, WP11-R01/R02).

- Appends complete lines to one local file in a trusted directory; never sends
  anything over a network.
- Opening: ``O_NOFOLLOW`` (no symbolic link), ``O_NONBLOCK`` (a FIFO or device
  cannot block the open), then the file must be a regular file with one link,
  owned by this user and not accessible to group or others (mode 0600 is used
  when creating it). A file already above the cap is refused and left as it is.
- Single writer: the sink holds a non-blocking ``flock`` on the file for its
  life, so a second cooperating sink on the same file is refused.
- After the lock is held, a non-empty file must end with a newline. A file with
  an incomplete last line (for example left by a partial write before a
  restart) is refused (``LogFileIncompleteError``) and left as it is: never
  truncated, repaired or removed (WP11-R04).
- Before every write the sink checks that the path still names the same regular
  file with one link, owned by this user with mode 0600, and that its real size
  equals the size it tracked. Any difference stops the sink
  (``LogFileChangedError``).
- Each line must end with exactly one newline. A line that would exceed the cap
  is refused whole. A short write or an I/O error stops the sink: the partial
  tail stays in the file (never truncated), so the last line may be incomplete,
  and no further line is written after it.

Not claimed: protection against an uncooperative process that writes the file
directly, or against races on an untrusted parent directory (Safety Gate). No
``fsync``: a line that was written may still be lost on a crash. This sink is
an observability log, not the durable mandatory audit record (WP-09).
"""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from pathlib import Path

from agent_core.types.errors import (
    LogCapacityError,
    LogFileChangedError,
    LogFileIncompleteError,
    LogPartialWriteError,
    LogSinkStoppedError,
    LogWriteError,
)

_FLAGS = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_MAX_TOTAL_BYTES = 1 << 40


class JsonLinesLogSink:
    def __init__(self, path: Path, *, max_total_bytes: int) -> None:
        if type(max_total_bytes) is not int or not 0 < max_total_bytes <= _MAX_TOTAL_BYTES:
            raise LogWriteError("max_total_bytes must be a positive int")
        self._path = Path(path)
        try:
            fd = os.open(self._path, _FLAGS, 0o600)
        except OSError as exc:
            raise LogWriteError(f"cannot open log file: {exc.strerror}") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LogWriteError("log file must be a regular file with one link")
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise LogWriteError("log file must be owned by this user, mode 0600")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LogWriteError("log file is in use by another sink") from None
            if info.st_size > max_total_bytes:
                raise LogCapacityError("existing log file is above the cap; left unchanged")
            try:
                tail = os.pread(fd, 1, info.st_size - 1) if info.st_size else b"\n"
            except OSError as exc:
                raise LogWriteError(f"cannot read log file: {exc.strerror}") from None
            if tail != b"\n":
                raise LogFileIncompleteError(
                    "log file ends with an incomplete line; left unchanged"
                )
        except BaseException:
            os.close(fd)
            raise
        self._fd: int | None = fd
        self._identity = (info.st_dev, info.st_ino)
        self._size = info.st_size
        self._max_total_bytes = max_total_bytes
        self._lock = threading.Lock()

    def write(self, line: bytes) -> None:
        with self._lock:
            fd = self._fd
            if fd is None:
                raise LogSinkStoppedError("log sink is stopped")
            if type(line) is not bytes or len(line) < 2 or not line.endswith(b"\n"):
                raise LogWriteError("a log line must end with one newline")
            if line.count(b"\n") != 1:
                raise LogWriteError("a log line must not contain other newlines")
            self._check_file(fd)
            if self._size + len(line) > self._max_total_bytes:
                raise LogCapacityError("log storage cap reached")
            try:
                written = os.write(fd, line)
            except OSError as exc:
                self._stop()
                raise LogWriteError(f"log write failed: {exc.strerror}") from None
            if written != len(line):
                self._size += max(written, 0)
                self._stop()
                raise LogPartialWriteError("partial log write; the last line is incomplete")
            self._size += written

    def _check_file(self, fd: int) -> None:
        try:
            current = os.fstat(fd)
            named = os.stat(self._path, follow_symlinks=False)
        except OSError:
            self._stop()
            raise LogFileChangedError("log file cannot be checked") from None
        if (
            (named.st_dev, named.st_ino) != self._identity
            or (current.st_dev, current.st_ino) != self._identity
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_uid != os.geteuid()
            or current.st_mode & 0o077
            or current.st_size != self._size
        ):
            self._stop()
            raise LogFileChangedError("log file changed outside this sink")

    def _stop(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def close(self) -> None:
        with self._lock:
            self._stop()
