"""OS-level single-writer lock for the Runtime (A 5.3 layer 1, A.I18; Linux).

- ``flock(LOCK_EX | LOCK_NB)`` on a dedicated lock file next to the database:
  never waits. If another process (or another open file description in this
  process) holds it, ``RuntimeLockHeldError`` is raised and the caller must not
  start. Any path alias of the directory reaches the same lock file, so it
  meets the same lock.
- The lock is held until ``release``. If the process dies, the kernel releases it.
- The lock file is opened without following a final symbolic link and must be
  a regular file with exactly one link. Its content must be empty or this
  module's own display line; anything else is refused and left unchanged, so
  an unrelated file is never truncated. The PID written into it is for people
  only; it never decides ownership.
- After locking, and before every write (``verify``), the path must still
  name the locked inode with one link; otherwise ``RuntimeLockLostError``.

Assumptions and limits: the directory is a trusted local directory controlled
by the owner (no other user can rename entries in it). flock works only within
one kernel and is not reliable on network file systems.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
from pathlib import Path

from agent_core.types.errors import (
    RuntimeLockFileError,
    RuntimeLockHeldError,
    RuntimeLockLostError,
)

_OPEN_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_MODE = 0o600
_MAX_CONTENT = 256
_DISPLAY_LINE = re.compile(rb"pid [0-9]{1,10} \(display only\)\n")


class RuntimeLock:
    def __init__(self, path: Path, fd: int, identity: tuple[int, int]) -> None:
        self._path = path
        self._fd: int | None = fd
        self._identity = identity

    @classmethod
    def acquire(cls, path: Path) -> RuntimeLock:
        try:
            fd = os.open(path, _OPEN_FLAGS, _FILE_MODE)
        except OSError as exc:
            raise RuntimeLockFileError(f"cannot open lock file: {exc.strerror}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeLockFileError("lock file must be a regular file with one link")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeLockHeldError("another runtime holds the lock") from exc
            lock = cls(path, fd, (info.st_dev, info.st_ino))
            lock.verify()
            content = os.pread(fd, _MAX_CONTENT + 1, 0)
            if content and _DISPLAY_LINE.fullmatch(content) is None:
                raise RuntimeLockFileError("lock file has unexpected content; left unchanged")
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"pid {os.getpid()} (display only)\n".encode(), 0)
        except BaseException:
            os.close(fd)
            raise
        return lock

    @property
    def held(self) -> bool:
        return self._fd is not None

    def verify(self) -> None:
        """Raise ``RuntimeLockLostError`` unless the path still names the locked file."""
        if self._fd is None:
            raise RuntimeLockLostError("lock was released")
        try:
            current = os.stat(self._path, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise RuntimeLockLostError("lock file was deleted") from exc
        except OSError as exc:
            raise RuntimeLockLostError(f"cannot check lock file: {exc.strerror}") from exc
        if (current.st_dev, current.st_ino) != self._identity or current.st_nlink != 1:
            raise RuntimeLockLostError("lock file was replaced or linked")

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
