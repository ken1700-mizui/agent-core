"""Synthetic work trees and file-operation attempts for WP-08 tests.

Trees are created by the production ``create_synthetic_workspace`` inside a
managed area (``<tmp_path>/area``, 0700). ``write_files`` and ``install`` build a
tree by hand, only for tests that copy or forge one.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_core.adapters.file_broker import SyntheticFileBroker, create_synthetic_workspace
from agent_core.safety.workspace import manifest_digest, manifest_key
from agent_core.types.files import MANIFEST_FILE_NAME, SignedWorkspaceManifest
from agent_core.types.records import Attempt
from agent_core.types.safety import SecretKey
from agent_core.types.serialization import dump_json
from support import ledger_world as lw
from support import safety_world as s
from support import tool_world as tw

FILES: dict[str, bytes] = {
    "workspace/a.txt": b"synthetic a\n",
    "workspace/out/r.txt": b"synthetic old\n",
}
READ_INPUT = '{"path":"workspace/a.txt"}'
NEW_CONTENT = "synthetic new\n"
WRITE_INPUT = tw.write_input("workspace/out/new.txt", content=NEW_CONTENT)
CREATED_AT = s.NOW - 10_000


@dataclass(frozen=True)
class Tree:
    area: Path
    workspace_id: str
    signed: SignedWorkspaceManifest
    digest: str

    @property
    def root(self) -> Path:
        return self.area / self.workspace_id


def area_of(tmp_path: Path) -> Path:
    """The managed area: a private directory the composition root names."""
    area = tmp_path / "area"
    if not area.exists():
        area.mkdir(mode=0o700)
    area.chmod(0o700)
    return area


def make_tree(
    tmp_path: Path,
    owner: SecretKey,
    name: str = "tree",
    files: dict[str, bytes] | None = None,
    *,
    key: SecretKey | None = None,
    area: Path | None = None,
) -> Tree:
    """A new synthetic tree, its manifest signed by the derived manifest key (or by
    ``key``, for forgery tests)."""
    where = area if area is not None else area_of(tmp_path)
    signed = create_synthetic_workspace(
        area=where,
        workspace_id=name,
        files=dict(FILES if files is None else files),
        key=key if key is not None else manifest_key(owner),
        now_ms=CREATED_AT,
    )
    return Tree(
        area=where, workspace_id=name, signed=signed, digest=manifest_digest(signed.manifest)
    )


def broker(owner: SecretKey, tree: Tree, workspace_id: str | None = None) -> SyntheticFileBroker:
    return SyntheticFileBroker(
        area=tree.area,
        workspace_id=tree.workspace_id if workspace_id is None else workspace_id,
        key=manifest_key(owner),
    )


def write_files(root: Path, files: dict[str, bytes]) -> None:
    """A hand-made tree (copy and forgery tests only)."""
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o600)


def install(root: Path, signed: SignedWorkspaceManifest) -> None:
    target = root / MANIFEST_FILE_NAME
    target.write_bytes(dump_json(signed))
    target.chmod(0o600)


def snapshot(root: Path, *, dir_times: bool = True) -> dict[str, Any]:
    """Every entry under ``root`` (links not followed): kind, bytes, inode, times, mode.
    ``dir_times=False`` leaves out directory times: a temporary file that was created
    and removed again changes its directory's times, not its listing."""
    result: dict[str, Any] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(current) / name
            info = os.lstat(path)
            if stat.S_ISREG(info.st_mode):
                data: Any = path.read_bytes()
            elif stat.S_ISDIR(info.st_mode):
                data = sorted(os.listdir(path))
            else:
                data = None
            timed = dir_times or not stat.S_ISDIR(info.st_mode)
            result[str(path.relative_to(root))] = (
                stat.S_IFMT(info.st_mode),
                data,
                info.st_ino,
                info.st_mtime_ns if timed else None,
                info.st_ctime_ns if timed else None,
                info.st_mode,
                info.st_nlink,
            )
    result["."] = sorted(os.listdir(root))
    return result


def world(tmp_path: Path, **tree: Any) -> tuple[lw.Setup, Tree]:
    """A ledger whose policy trusts exactly this tree's manifest digest."""
    keys = lw.new_keys()
    made = make_tree(tmp_path, keys.owner, **tree)
    st = lw.setup(tmp_path, keys=keys, policy=s.policy(trusted_synthetic_workspaces=(made.digest,)))
    return st, made


def consumed(
    st: lw.Setup,
    tree: Tree | str,
    *,
    write: bool = False,
    canonical_input: str | None = None,
    **descriptor: Any,
) -> Attempt:
    """A file operation on ``tree`` up to PERMIT_CONSUMED through the real path."""
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    workspace_ref = tree if isinstance(tree, str) else tree.digest
    inp = canonical_input if canonical_input is not None else (WRITE_INPUT if write else READ_INPUT)
    descriptor.setdefault("registration", s.WRITE_TOOL if write else s.READ_TOOL)
    attempt = lw.fixed_attempt(
        st, step, scopes, workspace_ref=workspace_ref, canonical_input=inp, **descriptor
    )
    attempt = lw.reserve(st, attempt, 1_000)
    attempt = lw.issue(st, lw.intent(st, attempt))
    return lw.consume(st, attempt)


def history(st: lw.Setup, attempt: Attempt) -> list[Any]:
    """The attempt's recorded transitions, oldest first (``after.state``, evidence)."""
    from agent_core.types.transitions import AttemptTransition

    run_id = st.sm.state.steps[attempt.step_id].run_id
    found: list[Any] = []
    for event in st.journal.read_run(run_id, after_run_seq=0, limit=1_000):
        if event.payload is None:
            continue
        for t in event.payload.transitions:
            if isinstance(t, AttemptTransition) and t.after.attempt_id == attempt.attempt_id:
                found.append(t)
    return found


class Spy:
    """Records the path-taking os calls (name, path, dir_fd) while installed."""

    NAMES = (
        "open",
        "stat",
        "lstat",
        "rename",
        "replace",
        "link",
        "symlink",
        "unlink",
        "remove",
        "rmdir",
        "mkdir",
        "makedirs",
        "truncate",
        "chmod",
        "chown",
        "utime",
        "mkfifo",
        "mknod",
    )

    def __init__(self, monkeypatch: Any) -> None:
        import builtins

        self.calls: list[tuple[str, str, Any]] = []
        for name in self.NAMES:
            monkeypatch.setattr(os, name, self._wrap(name, getattr(os, name)))
        monkeypatch.setattr(builtins, "open", self._wrap("builtins.open", builtins.open))

    def _wrap(self, name: str, original: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args:
                first = args[0]
                path = str(first) if isinstance(first, int) else os.fsdecode(first)
                self.calls.append((name, path, kwargs.get("dir_fd", kwargs.get("src_dir_fd"))))
            return original(*args, **kwargs)

        return wrapper

    def paths(self) -> list[str]:
        return [p for _n, p, _d in self.calls]

    def changes(self) -> list[tuple[str, str, Any]]:
        """os calls that create, change or remove something."""
        reading = {"open", "stat", "lstat", "builtins.open"}
        return [c for c in self.calls if c[0] in self.NAMES and c[0] not in reading]


class AfterPrepare:
    """A file port that runs ``action`` once the real broker has prepared the tree
    (to change something between the check and the operation)."""

    def __init__(self, real: Any, action: Any) -> None:
        self._real = real
        self._action = action

    def prepare(self, descriptor: Any, policy: Any) -> Any:
        prepared = self._real.prepare(descriptor, policy)
        self._action()
        return prepared
