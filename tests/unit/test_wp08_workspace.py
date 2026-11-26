"""WP-08 stage 1 (WP08-R01, R02; ADR-0008): the managed area, the work-tree root
and its manifest fixed through file descriptors, the component-wise walk,
creating a managed synthetic tree, the cooperative single-writer lock, and the
S19 refusal when the conditions do not hold.

Permission checks look at owner and mode bits, so they hold as uid 0 too (these
tests run as root in this container). Root itself bypasses the bits: it is not a
writer the lock or 0700 can exclude (ADR-0008, limits).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import file_broker
from agent_core.adapters.file_broker import (
    SyntheticFileBroker,
    create_synthetic_workspace,
    safe_resolution_available,
    split_target,
)
from agent_core.runtime.file_execution import run_file_operation
from agent_core.safety.workspace import file_digest, manifest_digest, sign_manifest
from agent_core.types.cost import ReservationState
from agent_core.types.errors import (
    ActionDescriptorError,
    FileOperationFailedError,
    FileOperationRefusedError,
    WorkspaceRejectedError,
)
from agent_core.types.files import MANIFEST_FILE_NAME, TEMP_FILE_PREFIX, EntryKind, FileEffect
from agent_core.types.records import AttemptState
from agent_core.types.safety import ActionDescriptor, ReceiptState
from agent_core.types.serialization import dump_json
from agent_core.types.transitions import AbortReason
from support import files_world as fw
from support import ledger_world as lw
from support import safety_world as s

A = AttemptState
R = ReservationState


@pytest.fixture
def world(tmp_path: Path) -> Any:
    st, tree = fw.world(tmp_path)
    yield st, tree
    if not st.journal.closed:
        st.journal.close()


def _descriptor(st: Any, attempt: Any) -> ActionDescriptor:
    recorded: ActionDescriptor | None = st.journal.recorded_descriptor(attempt.attempt_id)
    assert recorded is not None
    return recorded


def _policy(tree: fw.Tree, **overrides: Any) -> Any:
    values: dict[str, Any] = {"trusted_synthetic_workspaces": (tree.digest,)}
    values.update(overrides)
    return s.policy(**values)


def _prepare(st: Any, tree: fw.Tree, attempt: Any, **kw: Any) -> Any:
    broker = fw.broker(st.keys.owner, tree, kw.pop("workspace_id", None))
    return broker.prepare(_descriptor(st, attempt), kw.pop("policy", _policy(tree)))


def _rejected(call: Any, reason: str, error: type[Exception] = WorkspaceRejectedError) -> None:
    with pytest.raises(error) as excinfo:
        call()
    assert reason in excinfo.value.reasons  # type: ignore[attr-defined]


# ================================================================ R01: root and manifest


def test_a_valid_tree_is_prepared(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    prepared = _prepare(st, tree, attempt)
    try:
        assert prepared.workspace_ref == tree.digest
        assert prepared.root_inode == tree.signed.manifest.root_inode
    finally:
        prepared.close()
    prepared.close()  # closing twice is harmless


def test_a_manifest_copied_to_another_tree_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    other = tree.area / "other"
    fw.write_files(other, fw.FILES)
    shutil.copyfile(tree.root / MANIFEST_FILE_NAME, other / MANIFEST_FILE_NAME)
    _rejected(lambda: _prepare(st, tree, attempt, workspace_id="other"), "ROOT_MISMATCH")


def test_a_copy_moved_into_the_original_path_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    moved = tree.area / "moved"
    tree.root.rename(moved)
    shutil.copytree(moved, tree.root)  # same path, same bytes, same manifest
    tree.root.chmod(0o700)
    _rejected(lambda: _prepare(st, tree, attempt), "ROOT_IDENTITY_MISMATCH")


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        pytest.param("other-tree", "ROOT_MISMATCH", id="valid-manifest-of-another-tree"),
        pytest.param("forged", "MANIFEST_MAC_INVALID", id="forged-mac"),
        pytest.param("garbage", "MANIFEST_INVALID", id="not-a-manifest"),
    ],
)
def test_a_replaced_manifest_is_refused(
    world: Any, tmp_path: Path, replacement: str, reason: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    if replacement == "other-tree":
        data = dump_json(fw.make_tree(tmp_path, st.keys.owner, "second").signed)
    elif replacement == "forged":
        changed = tree.signed.manifest.model_copy(update={"workspace_id": "forged"})
        data = dump_json(sign_manifest(lw.new_keys().owner, changed))
    else:
        data = b'{"manifest":{}}'
    target = tree.root / MANIFEST_FILE_NAME
    target.unlink()
    target.write_bytes(data)
    target.chmod(0o600)
    _rejected(lambda: _prepare(st, tree, attempt), reason)


def test_a_tree_without_a_manifest_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / MANIFEST_FILE_NAME).unlink()
    _rejected(lambda: _prepare(st, tree, attempt), "MANIFEST_UNREADABLE")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "group-writable"])
def test_the_manifest_must_be_a_private_regular_file(world: Any, tmp_path: Path, kind: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    target = tree.root / MANIFEST_FILE_NAME
    kept = tmp_path / "kept.json"
    shutil.copyfile(target, kept)
    if kind == "group-writable":
        target.chmod(0o620)
    else:
        target.unlink()
        if kind == "symlink":
            target.symlink_to(kept)
        elif kind == "hardlink":
            os.link(kept, target)
        elif kind == "fifo":
            os.mkfifo(target, 0o600)  # opening it for reading would block without care
        else:
            target.mkdir(mode=0o700)
    _rejected(lambda: _prepare(st, tree, attempt), "MANIFEST_UNREADABLE")


def test_an_oversized_manifest_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    target = tree.root / MANIFEST_FILE_NAME
    target.write_bytes(b" " * (1_048_576 + 1))
    _rejected(lambda: _prepare(st, tree, attempt), "MANIFEST_UNREADABLE")


def test_the_manifest_is_opened_relative_to_the_root_fd(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    spy = fw.Spy(monkeypatch)
    prepared = _prepare(st, tree, attempt)
    prepared.close()
    monkeypatch.undo()
    opens = [(p, d) for n, p, d in spy.calls if n == "open"]
    # "/" first, then one name at a time relative to the directory opened before.
    assert opens[0] == ("/", None)
    assert all("/" not in p and d is not None for p, d in opens[1:])
    assert any(p == MANIFEST_FILE_NAME for p, _d in opens)
    assert not any(str(tree.root) in p for p in spy.paths())
    assert spy.changes() == []


def test_a_root_behind_a_link_is_refused(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.area / "link").symlink_to(tree.root)
    _rejected(lambda: _prepare(st, tree, attempt, workspace_id="link"), "ROOT_UNUSABLE")


@pytest.mark.parametrize("change", ["group-readable", "other-owner"])
def test_the_root_must_be_private_and_ours(world: Any, change: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    if change == "group-readable":
        tree.root.chmod(0o750)
    else:
        os.chown(tree.root, 54_321, -1)
    _rejected(lambda: _prepare(st, tree, attempt), "ROOT_PERMISSIONS")


def test_the_descriptor_must_name_this_manifest(world: Any, tmp_path: Path) -> None:
    st, tree = world
    other = fw.make_tree(tmp_path, st.keys.owner, "other")
    attempt = fw.consumed(st, tree)
    _rejected(lambda: _prepare(st, other, attempt, policy=_policy(tree)), "WORKSPACE_MISMATCH")


@pytest.mark.parametrize("case", ["untrusted", "no-policy", "other-version"])
def test_the_policy_must_trust_the_manifest_now(world: Any, case: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    policy = {
        "untrusted": s.policy(trusted_synthetic_workspaces=()),
        "no-policy": None,
        "other-version": _policy(tree, policy_version="policy-2"),
    }[case]
    _rejected(lambda: _prepare(st, tree, attempt, policy=policy), "WORKSPACE_NOT_TRUSTED")


def test_the_verified_root_stays_fixed_when_the_tree_is_moved(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    with _prepare(st, tree, attempt) as prepared:
        moved = tree.area / "moved"
        tree.root.rename(moved)
        fw.write_files(tree.root, {"workspace/a.txt": b"decoy\n"})  # a decoy at the old path
        # The held fd still names the verified tree (now at "moved"), not the decoy.
        assert prepared.root_inode == os.lstat(moved).st_ino
        assert prepared.root_inode != os.lstat(tree.root).st_ino
        with prepared.resolve_parent("workspace/a.txt") as (parent, name):
            assert os.stat(name, dir_fd=parent).st_ino == os.lstat(moved / "workspace/a.txt").st_ino


# ================================================================ R02: the managed area


def test_the_area_must_be_private(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    tree.area.chmod(0o750)
    _rejected(lambda: _prepare(st, tree, attempt), "AREA_UNSAFE")


def test_a_link_above_the_area_is_refused(tmp_path: Path) -> None:
    keys = lw.new_keys()
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    area = real / "area"
    area.mkdir(mode=0o700)
    tree = fw.make_tree(tmp_path, keys.owner, area=area)
    (tmp_path / "alias").symlink_to(real)
    aliased = fw.Tree(tmp_path / "alias" / "area", tree.workspace_id, tree.signed, tree.digest)
    st = lw.setup(tmp_path, keys=keys, policy=_policy(tree))
    try:
        attempt = fw.consumed(st, tree)
        _rejected(lambda: _prepare(st, aliased, attempt), "AREA_UNSAFE")
        _prepare(st, tree, attempt).close()  # the real path is fine
    finally:
        st.journal.close()


@pytest.mark.parametrize(
    ("mode", "owner", "accepted"),
    [
        pytest.param(0o777, None, False, id="world-writable"),
        pytest.param(0o1777, None, True, id="world-writable-sticky"),
        pytest.param(0o755, 54_321, False, id="other-owner"),
        pytest.param(0o755, None, True, id="plain"),
    ],
)
def test_ancestors_of_the_area_are_checked(
    tmp_path: Path, mode: int, owner: int | None, accepted: bool
) -> None:
    keys = lw.new_keys()
    loose = tmp_path / "loose"
    loose.mkdir()
    area = loose / "area"
    area.mkdir(mode=0o700)
    tree = fw.make_tree(tmp_path, keys.owner, area=area)
    loose.chmod(mode)
    if owner is not None:
        os.chown(loose, owner, -1)
    st = lw.setup(tmp_path, keys=keys, policy=_policy(tree))
    try:
        attempt = fw.consumed(st, tree)
        if accepted:
            _prepare(st, tree, attempt).close()
        else:
            _rejected(lambda: _prepare(st, tree, attempt), "AREA_UNSAFE")
    finally:
        st.journal.close()


@pytest.mark.parametrize("area", ["relative/area", "/a/../b", "/a/./b", "/a//b", "/"])
def test_the_area_is_a_normalized_absolute_path(area: str) -> None:
    with pytest.raises(ValueError, match="managed area"):
        SyntheticFileBroker(area=area, workspace_id="t", key=s.new_key())


def test_a_device_change_inside_the_area_is_refused(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception injection: the root reports another device (a mount boundary). A bind
    mount of the same file system keeps the device and is not detected (ADR-0008)."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    root_ino = os.lstat(tree.root).st_ino
    real_fstat = os.fstat

    class _Moved:
        def __init__(self, info: Any) -> None:
            self._info = info

        def __getattr__(self, name: str) -> Any:
            if name == "st_dev":
                return self._info.st_dev + 1
            return getattr(self._info, name)

    def fstat(fd: int) -> Any:
        info = real_fstat(fd)
        return _Moved(info) if info.st_ino == root_ino else info

    monkeypatch.setattr(os, "fstat", fstat)
    _rejected(lambda: _prepare(st, tree, attempt), "MOUNT_BOUNDARY")


# ================================================================ R02: the component walk


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/workspace/a.txt",
        "workspace//a.txt",
        "workspace/./a.txt",
        "workspace/../a.txt",
        "workspace/a.txt/",
        ".",
        "..",
        "workspace/a\x00.txt",
        "workspace\\a.txt",
        MANIFEST_FILE_NAME,
        f"workspace/{MANIFEST_FILE_NAME}",
        f"workspace/{TEMP_FILE_PREFIX}0123",
    ],
)
def test_unsafe_or_reserved_paths_are_refused(path: str) -> None:
    with pytest.raises(FileOperationFailedError) as excinfo:
        split_target(path)
    assert excinfo.value.reasons == ("PATH_INVALID",)
    assert excinfo.value.effect is FileEffect.NONE


@pytest.mark.parametrize(
    "path", [f"workspace/{MANIFEST_FILE_NAME}", f"workspace/{TEMP_FILE_PREFIX}x.txt"]
)
def test_reserved_names_are_not_tool_targets(path: str) -> None:
    with pytest.raises(ActionDescriptorError) as excinfo:
        s.read_descriptor(s.ref(), canonical_input=f'{{"path":"{path}"}}')
    assert excinfo.value.reasons == ("RESOURCE_PATH_RESERVED",)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("symlink-first", id="link-at-first-level"),
        pytest.param("symlink-second", id="link-at-second-level"),
        pytest.param("file-instead", id="file-instead-of-directory"),
        pytest.param("group-writable", id="group-writable-directory"),
        pytest.param("other-owner", id="directory-of-another-user"),
        pytest.param("fifo-instead", id="fifo-instead-of-directory"),
    ],
)
def test_each_level_of_the_walk_is_checked(world: Any, tmp_path: Path, change: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    outside = tmp_path / "outside"
    fw.write_files(outside, {"out/r.txt": b"outside\n"})
    workspace = tree.root / "workspace"
    out = workspace / "out"
    with _prepare(st, tree, attempt) as prepared:
        # Changed after the root was verified: the walk itself must refuse.
        if change == "symlink-first":
            workspace.rename(tree.root / "aside")
            workspace.symlink_to(outside)
        elif change == "symlink-second":
            shutil.rmtree(out)
            out.symlink_to(outside / "out")
        elif change == "file-instead":
            shutil.rmtree(out)
            out.write_bytes(b"not a directory")
        elif change == "fifo-instead":
            shutil.rmtree(out)
            os.mkfifo(out, 0o600)
        elif change == "group-writable":
            out.chmod(0o770)
        else:
            os.chown(out, 54_321, -1)
        with (
            pytest.raises(FileOperationFailedError) as excinfo,
            prepared.resolve_parent("workspace/out/r.txt"),
        ):
            pass
    assert excinfo.value.reasons == ("PATH_UNSAFE",)
    assert excinfo.value.effect is FileEffect.NONE
    assert (outside / "out/r.txt").read_bytes() == b"outside\n"


def test_the_walk_uses_only_single_names_and_fds(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    with _prepare(st, tree, attempt) as prepared:
        spy = fw.Spy(monkeypatch)
        with prepared.resolve_parent("workspace/out/r.txt") as (_parent, name):
            assert name == "r.txt"
        monkeypatch.undo()
    assert [(n, p) for n, p, _d in spy.calls] == [("open", "workspace"), ("open", "out")]
    assert all(d is not None for _n, _p, d in spy.calls)


# ================================================================ R02: creating a managed tree


def test_a_workspace_is_created_private_with_its_manifest(tmp_path: Path) -> None:
    keys = lw.new_keys()
    tree = fw.make_tree(tmp_path, keys.owner)
    manifest = tree.signed.manifest
    info = os.lstat(tree.root)
    assert (manifest.root_path, manifest.root_device, manifest.root_inode) == (
        str(tree.root),
        info.st_dev,
        info.st_ino,
    )
    assert info.st_mode & 0o777 == 0o700
    for entry in manifest.entries:
        mode = os.lstat(tree.root / entry.path).st_mode & 0o777
        if entry.kind is EntryKind.FILE:
            assert mode == 0o600
            assert entry.content_digest == file_digest(fw.FILES[entry.path])
        else:
            assert mode == 0o700
    assert {e.path for e in manifest.entries} == {
        "workspace",
        "workspace/a.txt",
        "workspace/out",
        "workspace/out/r.txt",
    }
    manifest_file = tree.root / MANIFEST_FILE_NAME
    assert manifest_file.read_bytes() == dump_json(tree.signed)
    assert os.lstat(manifest_file).st_mode & 0o777 == 0o600
    assert manifest_digest(manifest) == tree.digest


def test_creating_an_existing_workspace_is_refused(tmp_path: Path) -> None:
    keys = lw.new_keys()
    tree = fw.make_tree(tmp_path, keys.owner)
    before = fw.snapshot(tree.root)
    _rejected(lambda: fw.make_tree(tmp_path, keys.owner), "WORKSPACE_EXISTS")
    assert fw.snapshot(tree.root) == before


@pytest.mark.parametrize(
    "files",
    [
        {"../escape.txt": b"x"},
        {"/abs.txt": b"x"},
        {"a//b.txt": b"x"},
        {MANIFEST_FILE_NAME: b"x"},
        {f"w/{TEMP_FILE_PREFIX}1": b"x"},
        {"a": b"x", "a/b": b"y"},
    ],
)
def test_creation_refuses_unsafe_paths_before_creating_anything(
    tmp_path: Path, files: dict[str, bytes]
) -> None:
    area = fw.area_of(tmp_path)
    _rejected(lambda: fw.make_tree(tmp_path, lw.new_keys().owner, files=files), "PATH_INVALID")
    assert os.listdir(area) == []


def test_creation_in_an_unsafe_area_is_refused(tmp_path: Path) -> None:
    area = fw.area_of(tmp_path)
    area.chmod(0o755)
    _rejected(lambda: fw.make_tree(tmp_path, lw.new_keys().owner, area=area), "AREA_UNSAFE")
    assert os.listdir(area) == []


# ================================================================ R02: one writer


def test_one_broker_at_a_time(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    first = _prepare(st, tree, attempt)
    try:
        _rejected(lambda: _prepare(st, tree, attempt), "WORKSPACE_BUSY", FileOperationRefusedError)
    finally:
        first.close()
    _prepare(st, tree, attempt).close()


_HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
sys.stdin.read()
"""


def test_a_lock_held_by_another_process_refuses(world: Any) -> None:
    """Real second process: a cooperating writer holding the tree's lock."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    with subprocess.Popen(
        [sys.executable, "-I", "-c", _HOLDER, str(tree.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "locked"
            _rejected(
                lambda: _prepare(st, tree, attempt), "WORKSPACE_BUSY", FileOperationRefusedError
            )
        finally:
            holder.kill()
            holder.wait(timeout=30)
    _prepare(st, tree, attempt).close()  # the lock went with the process


def test_a_busy_tree_aborts_before_dispatch(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    with _prepare(st, tree, attempt):
        done = run_file_operation(
            gate=lw.gate(st),
            ledger=st.journal,
            registry=s.REGISTRY,
            files=fw.broker(st.keys.owner, tree),
            attempt=attempt,
            write_content=None,
            **lw.actual(st, attempt),
        ).attempt
    assert done.state is A.ABORTED_BEFORE_DISPATCH
    assert fw.history(st, attempt)[-1].evidence.reason is AbortReason.WORKSPACE_BUSY
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro) == (R.RELEASED, 0)


# ================================================================ S19 stays in force


def test_safe_resolution_is_available_here() -> None:
    assert file_broker.SAFE_RESOLUTION_ADOPTED is True
    assert file_broker.missing_capabilities() == ()
    assert safe_resolution_available() is True


def _lose(monkeypatch: pytest.MonkeyPatch, capability: str) -> None:
    if capability == "platform":
        monkeypatch.setattr(sys, "platform", "darwin")
    elif capability == "flock":
        import fcntl

        monkeypatch.delattr(fcntl, "flock")
    elif capability == "link-dir-fd":
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd - {os.link})
    else:
        monkeypatch.delattr(os, capability)


CAPABILITIES = ["O_PATH", "O_NOFOLLOW", "O_DIRECTORY", "platform", "flock", "link-dir-fd"]


@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize("write", [False, True])
def test_without_the_conditions_s19_refuses_before_anything_is_opened(
    world: Any, monkeypatch: pytest.MonkeyPatch, capability: str, write: bool
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=write)
    before = fw.snapshot(tree.root)
    files = fw.broker(st.keys.owner, tree)
    gate = lw.gate(st)
    _lose(monkeypatch, capability)
    assert safe_resolution_available() is False
    spy = fw.Spy(monkeypatch)
    done = run_file_operation(
        gate=gate,
        ledger=st.journal,
        registry=s.REGISTRY,
        files=files,
        attempt=attempt,
        write_content=fw.NEW_CONTENT.encode() if write else None,
        **lw.actual(st, attempt),
    ).attempt
    monkeypatch.undo()
    assert done.state is A.ABORTED_BEFORE_DISPATCH
    assert fw.history(st, attempt)[-1].evidence.reason is AbortReason.SAFE_FILE_ACCESS_UNAVAILABLE
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.NOT_DISPATCHED
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.state, view.hold_micro, view.spent_micro) == (R.RELEASED, 0, 0)
    # Nothing at all was opened: not "/", not the area, not the tree.
    assert [c for c in spy.calls if c[0] == "open"] == []
    assert not any(str(tree.area) in p for p in spy.paths())
    assert fw.snapshot(tree.root) == before


@pytest.mark.parametrize("write", [False, True])
def test_losing_the_conditions_after_preparing_fails_definitely_without_effect(
    world: Any, monkeypatch: pytest.MonkeyPatch, write: bool
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=write)
    before = fw.snapshot(tree.root)
    real = fw.broker(st.keys.owner, tree)

    class _Then:
        def prepare(self, descriptor: Any, policy: Any) -> Any:
            prepared = real.prepare(descriptor, policy)
            _lose(monkeypatch, "O_PATH")  # gone between the check and the operation
            return prepared

    done = run_file_operation(
        gate=lw.gate(st),
        ledger=st.journal,
        registry=s.REGISTRY,
        files=_Then(),
        attempt=attempt,
        write_content=fw.NEW_CONTENT.encode() if write else None,
        **lw.actual(st, attempt),
    ).attempt
    monkeypatch.undo()
    assert done.state is A.FAILED_DEFINITE
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.hold_micro, view.spent_micro) == (0, 0)
    assert fw.snapshot(tree.root) == before


def test_creation_is_refused_without_the_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = fw.area_of(tmp_path)
    _lose(monkeypatch, "O_NOFOLLOW")
    with pytest.raises(FileOperationRefusedError) as excinfo:
        create_synthetic_workspace(
            area=area, workspace_id="t", files={}, key=s.new_key(), now_ms=fw.CREATED_AT
        )
    assert excinfo.value.reasons == ("SAFE_FILE_ACCESS_UNAVAILABLE",)
    monkeypatch.undo()
    assert os.listdir(area) == []
