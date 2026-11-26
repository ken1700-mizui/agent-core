"""OS-level single-writer lock for the Runtime (A 5.3 layer 1, A.I18; Linux).

- ``flock(LOCK_EX | LOCK_NB)`` on a dedicated lock file: never waits. If another
  process (or another open file description in this process) holds it,
  ``RuntimeLockHeldError`` is raised and the caller must not start.
- The lock is held for the life of the process (until ``release``). If the
  process dies, the kernel releases it.
- The PID written into the file is for people only; it never decides ownership.
- The lock file is opened without following symbolic links. After locking, and
  before every write (``verify``), the path must still name the locked inode.
  A deleted or replaced lock file means another process could lock a new file,
  so ``RuntimeLockLostError`` is raised. The owner epoch in the database (A 5.3
  layer 2) rejects the stale owner independently.

Limits: flock works only within one kernel; it is not reliable on network file
systems. The database and lock file must be on a local file system.
"""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

from agent_core.types.errors import RuntimeLockHeldError, RuntimeLockLostError

_OPEN_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_MODE = 0o600


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
            raise RuntimeLockHeldError(f"cannot open lock file: {exc.strerror}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeLockHeldError("lock path is not a regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeLockHeldError("another runtime holds the lock") from exc
            lock = cls(path, fd, (info.st_dev, info.st_ino))
            lock.verify()
            os.ftruncate(fd, 0)
            os.write(fd, f"pid {os.getpid()} (display only)\n".encode())
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
        if (current.st_dev, current.st_ino) != self._identity:
            raise RuntimeLockLostError("lock file was replaced")

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
