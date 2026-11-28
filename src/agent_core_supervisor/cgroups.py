"""The only thing the Deadline Supervisor acts on: cgroups directly under its
delegated root (A1: a cgroup v2 subtree delegated to the DS user; no root, no
CAP_KILL). A target is one path component named ``agentcore-...``; links,
other names and anything outside the root are refused."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from agent_core.types.supervisor import TARGET_PATTERN

_TARGET = re.compile(TARGET_PATTERN)


class CgroupError(OSError):
    pass


class CgroupPort(Protocol):
    def unavailable(self) -> str | None:
        """None when usable; otherwise why not (then nothing may start)."""
        ...

    def exists(self, target: str) -> bool: ...

    def populated(self, target: str) -> bool: ...

    def kill(self, target: str) -> None: ...

    def targets(self) -> tuple[str, ...]: ...


def mounted_cgroup2(path: Path) -> bool:
    """Whether ``path`` lies on a cgroup2 mount (from /proc/self/mountinfo)."""
    real = os.path.realpath(path)
    best, fstype = "", ""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                point = left.split()[4]
                if (real == point or real.startswith(point.rstrip("/") + "/")) and len(point) > len(
                    best
                ):
                    best, fstype = point, right.split()[0]
    except (OSError, IndexError):
        return False
    return fstype == "cgroup2"


class CgroupV2:
    """``cgroup.kill`` (Linux 5.14+) kills every process of the cgroup and its
    children; ``cgroup.events`` tells whether any is left (``populated 0``)."""

    def __init__(self, root: Path, *, is_cgroup2: Callable[[Path], bool] = mounted_cgroup2) -> None:
        self._root = root
        self._is_cgroup2 = is_cgroup2

    def unavailable(self) -> str | None:
        try:
            st = os.lstat(self._root)
        except OSError:
            return "the delegated root does not exist"
        import stat

        if not stat.S_ISDIR(st.st_mode):
            return "the delegated root is not a directory"
        if not self._is_cgroup2(self._root):
            return "the delegated root is not on cgroup v2"
        return None

    def _dir(self, target: str) -> Path:
        if not _TARGET.fullmatch(target):
            raise CgroupError(f"not a sandbox target name: {target!r}")
        return self._root / target

    def exists(self, target: str) -> bool:
        import stat

        try:
            st = os.lstat(self._dir(target))
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise CgroupError(f"{target} is not a cgroup directory")
        return True

    def populated(self, target: str) -> bool:
        if not self.exists(target):
            return False
        fd = os.open(self._dir(target) / "cgroup.events", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            text = os.read(fd, 4096).decode()
        finally:
            os.close(fd)
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            if key == "populated":
                return value.strip() != "0"
        raise CgroupError("cgroup.events has no populated line")

    def kill(self, target: str) -> None:
        if not self.exists(target):
            return
        fd = os.open(self._dir(target) / "cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW)
        try:
            if os.write(fd, b"1") != 1:
                raise CgroupError("cgroup.kill was not written")
        finally:
            os.close(fd)

    def targets(self) -> tuple[str, ...]:
        import stat

        found = []
        for name in sorted(os.listdir(self._root)):
            if _TARGET.fullmatch(name):
                st = os.lstat(self._root / name)
                if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                    found.append(name)
        return tuple(found)
