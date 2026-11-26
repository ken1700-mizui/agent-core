"""Synthetic work trees and file-operation attempts for WP-08 tests.

The tree is created here, by the test, as Agent Core's own synthetic tree: a
private (0700) directory with synthetic files and the signed manifest at its root.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_core.safety.workspace import file_digest, manifest_digest, manifest_key, sign_manifest
from agent_core.types.files import (
    MANIFEST_FILE_NAME,
    EntryKind,
    SignedWorkspaceManifest,
    WorkspaceEntry,
    WorkspaceManifest,
)
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


@dataclass(frozen=True)
class Tree:
    root: Path
    signed: SignedWorkspaceManifest
    digest: str


def entries_of(files: dict[str, bytes]) -> tuple[WorkspaceEntry, ...]:
    dirs: set[str] = set()
    for path in files:
        parts = path.split("/")[:-1]
        dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    entries = [
        WorkspaceEntry(path=d, kind=EntryKind.DIRECTORY, size=None, content_digest=None)
        for d in dirs
    ] + [
        WorkspaceEntry(
            path=p, kind=EntryKind.FILE, size=len(data), content_digest=file_digest(data)
        )
        for p, data in files.items()
    ]
    return tuple(sorted(entries, key=lambda e: e.path))


def manifest_for(root: Path, files: dict[str, bytes], **overrides: Any) -> WorkspaceManifest:
    info = os.lstat(root)
    values: dict[str, Any] = {
        "manifest_version": 1,
        "workspace_id": "synthetic_tree",
        "synthetic": True,
        "root_path": str(root),
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
        "entries": entries_of(files),
        "created_at_ms": s.NOW - 10_000,
    }
    values.update(overrides)
    return WorkspaceManifest(**values)


def write_files(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def install(root: Path, signed: SignedWorkspaceManifest) -> None:
    target = root / MANIFEST_FILE_NAME
    target.write_bytes(dump_json(signed))
    target.chmod(0o600)


def make_tree(
    tmp_path: Path,
    owner: SecretKey,
    name: str = "tree",
    files: dict[str, bytes] | None = None,
    *,
    key: SecretKey | None = None,
) -> Tree:
    """A new synthetic tree with its manifest signed by the derived manifest key
    (or by ``key``, for forgery tests)."""
    files = dict(FILES if files is None else files)
    root = tmp_path / name
    write_files(root, files)
    manifest = manifest_for(root, files)
    signed = sign_manifest(key if key is not None else manifest_key(owner), manifest)
    install(root, signed)
    return Tree(root=root, signed=signed, digest=manifest_digest(signed.manifest))


def snapshot(root: Path) -> dict[str, Any]:
    """Every entry under ``root`` (links not followed): kind, bytes, inode, times, mode."""
    result: dict[str, Any] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(current) / name
            info = os.lstat(path)
            data = path.read_bytes() if stat.S_ISREG(info.st_mode) else sorted(os.listdir(path))
            result[str(path.relative_to(root))] = (
                stat.S_IFMT(info.st_mode),
                data,
                info.st_ino,
                info.st_mtime_ns,
                info.st_ctime_ns,
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
) -> Attempt:
    """A file operation on ``tree`` up to PERMIT_CONSUMED through the real path."""
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    workspace_ref = tree if isinstance(tree, str) else tree.digest
    inp = canonical_input if canonical_input is not None else (WRITE_INPUT if write else READ_INPUT)
    attempt = lw.fixed_attempt(
        st, step, scopes, write=write, workspace_ref=workspace_ref, canonical_input=inp
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
