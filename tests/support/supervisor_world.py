"""The Deadline Supervisor in tests (S-R01, D20): the real ``Supervisor`` with a
fake cgroup port. ``SandboxCgroups`` sees FakeSandbox's running containers as
populated cgroups and kills them as ``cgroup.kill`` would (even programs that
ignore signals), except targets marked ``resist`` (to test unconfirmed kills).
``DirCgroups`` emulates the cgroup v2 files in a temporary directory for the
real ``CgroupV2`` port (with its cgroup2 check replaced)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_core.types.sandbox import SandboxState
from agent_core_broker.fake import FakeSandbox
from agent_core_supervisor.supervisor import Supervisor

DS_KEY = b"synthetic deadline supervisor key, test only"


@dataclass
class SandboxCgroups:
    sandbox: FakeSandbox
    resist: set[str] = field(default_factory=set)
    extra: dict[str, bool] = field(default_factory=dict)  # target -> populated (not a sandbox)
    kills: list[str] = field(default_factory=list)
    reason: str | None = None

    def _container(self, target: str) -> Any:
        for receipt, container in self.sandbox.containers.items():
            if f"agentcore-{receipt}" == target:
                return container
        return None

    def unavailable(self) -> str | None:
        return self.reason

    def exists(self, target: str) -> bool:
        c = self._container(target)
        return target in self.extra or (c is not None and c.state is SandboxState.RUNNING)

    def populated(self, target: str) -> bool:
        if target in self.extra:
            return self.extra[target]
        return self.exists(target)

    def kill(self, target: str) -> None:
        self.kills.append(target)
        if target in self.resist:
            return
        if target in self.extra:
            self.extra[target] = False
            return
        c = self._container(target)
        if c is not None and c.state is SandboxState.RUNNING:
            c.state = SandboxState.STOPPED

    def targets(self) -> tuple[str, ...]:
        running = [
            f"agentcore-{r}"
            for r, c in self.sandbox.containers.items()
            if c.state is SandboxState.RUNNING
        ]
        return tuple(sorted({*running, *self.extra}))


class MemoryRecord:
    """An in-memory record (tests that inject I/O faults into P2's own record:
    the real supervisor logic, without the shared os.write and os.fsync)."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self.entries.append(event)

    def close(self) -> None:
        pass


def memory_supervisor(cgroups: Any, **kw: Any) -> Supervisor:
    return Supervisor(
        record=MemoryRecord(),  # type: ignore[arg-type]
        record_error=None,
        cgroups=cgroups,
        instance_id=kw.pop("instance_id", "ds-memory"),
        **kw,
    )


def supervisor(directory: Path, cgroups: Any, **kw: Any) -> Supervisor:
    directory.mkdir(parents=True, exist_ok=True)
    return Supervisor.open(
        record_path=directory / "deadline-supervisor.log",
        key=DS_KEY,
        cgroups=cgroups,
        instance_id=kw.pop("instance_id", "ds-test"),
        **kw,
    )


class DirCgroups:
    """cgroup v2 files emulated in a directory (for CgroupV2's file logic)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def make(self, target: str, *, populated: bool = True) -> Path:
        d = self.root / target
        d.mkdir()
        (d / "cgroup.events").write_text(f"populated {int(populated)}\nfrozen 0\n")
        (d / "cgroup.kill").write_text("")
        return d

    def killed(self, target: str) -> bool:
        return (self.root / target / "cgroup.kill").read_text() == "1"
