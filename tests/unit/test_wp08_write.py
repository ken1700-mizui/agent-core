"""WP-08 stage 3 (WP08-R05): writing one file (CREATE or REPLACE) in a managed
synthetic tree through the execution gate, failures, real process stops, and
recovering temporary files.

Two kinds of failure tests, kept apart:

- **Exception injection** (in this process): an os call is monkeypatched to fail
  or to behave unusually. It shows how each failure is classified.
- **Real process stop** (``tests/support/file_child.py``): a child process runs
  the write and is stopped with SIGKILL at a fixed point. Nothing in it can
  clean up; the parent then inspects the database and the tree.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import file_broker
from agent_core.adapters.file_broker import temp_tag
from agent_core.runtime.file_execution import recovery_receipts, run_file_operation
from agent_core.safety.workspace import file_digest
from agent_core.types.cost import ReservationState
from agent_core.types.errors import (
    FileEffect,
    FileOperationFailedError,
    FileOperationRefusedError,
    IllegalTransitionError,
    PermitRejectedError,
    WorkspaceRejectedError,
)
from agent_core.types.files import TEMP_FILE_PREFIX
from agent_core.types.ids import ReceiptId, TenantId
from agent_core.types.ledger import RecoverInFlight, RevokeAccess
from agent_core.types.records import AttemptState
from agent_core.types.safety import ReceiptState, StopScopeType, ToolContent
from support import builders as b
from support import files_world as fw
from support import ledger_world as lw
from support import privileged
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = ReservationState
REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "file_child.py"
NEW = "workspace/out/new.txt"
OLD = "workspace/out/r.txt"
OLD_BYTES = fw.FILES[OLD]
TEMP_NAME = re.compile(re.escape(TEMP_FILE_PREFIX) + r"[0-9a-f]{32}")


@pytest.fixture
def world(tmp_path: Path) -> Any:
    st, tree = fw.world(tmp_path)
    yield st, tree
    if not st.journal.closed:
        st.journal.close()


def _create(content: str = fw.NEW_CONTENT, path: str = NEW) -> str:
    return tw.write_input(path, content=content)


def _replace(content: str = "synthetic replaced\n", expected: bytes = OLD_BYTES) -> str:
    return tw.write_input(OLD, content=content, expected=file_digest(expected))


def _run(
    st: Any, tree: fw.Tree, attempt: Any, content: bytes, files: Any = None, gate: Any = None
) -> Any:
    return run_file_operation(
        gate=gate if gate is not None else lw.gate(st),
        ledger=st.journal,
        files=files if files is not None else fw.broker(st.keys.owner, tree),
        registry=s.REGISTRY,
        attempt=attempt,
        write_content=content,
        **lw.actual(st, attempt),
    )


def _temps(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if TEMP_NAME.fullmatch(p.name))


def _definite(st: Any, attempt: Any, result: Any) -> None:
    assert result.attempt.state is A.FAILED_DEFINITE
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    view = st.journal.reservation(lw.rsv(attempt))
    assert (view.hold_micro, view.spent_micro) == (0, 0)


def _unknown(st: Any, attempt: Any) -> None:
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    view = st.journal.reservation(lw.rsv(attempt))
    assert view.hold_micro == 1_000  # the hold is kept until reconciliation


# ================================================================ normal writes


def test_create_writes_a_new_private_file(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode())
    assert result.attempt.state is A.COMPLETED
    target = tree.root / NEW
    info = os.lstat(target)
    assert target.read_bytes() == fw.NEW_CONTENT.encode()
    assert (stat.S_ISREG(info.st_mode), info.st_nlink, stat.S_IMODE(info.st_mode)) == (
        True,
        1,
        0o600,
    )
    assert _temps(tree.root) == []
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    assert st.journal.reservation(lw.rsv(attempt)).hold_micro == 0
    assert (tree.root / OLD).read_bytes() == OLD_BYTES  # nothing else changed


def test_replace_swaps_the_content_atomically(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace())
    old_inode = os.lstat(tree.root / OLD).st_ino
    result = _run(st, tree, attempt, b"synthetic replaced\n")
    assert result.attempt.state is A.COMPLETED
    assert (tree.root / OLD).read_bytes() == b"synthetic replaced\n"
    assert os.lstat(tree.root / OLD).st_ino != old_inode  # a new file renamed over it
    assert os.lstat(tree.root / OLD).st_nlink == 1
    assert _temps(tree.root) == []


def test_partial_writes_are_completed(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception injection: every os.write writes one byte only."""
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, bytes(data[:1])))
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode())
    monkeypatch.undo()
    assert result.attempt.state is A.COMPLETED
    assert (tree.root / NEW).read_bytes() == fw.NEW_CONTENT.encode()


def test_a_write_lands_in_the_verified_tree_after_a_move(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())

    def move() -> None:
        tree.root.rename(tree.area / "moved")
        fw.write_files(tree.root, {"workspace/out/decoy.txt": b"decoy\n"})

    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), move)
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    assert result.attempt.state is A.COMPLETED
    assert (tree.area / "moved" / NEW).read_bytes() == fw.NEW_CONTENT.encode()
    assert not (tree.root / NEW).exists()


# ================================================================ refused writes (no effect)


def _refused_without_effect(
    st: Any, tree: fw.Tree, attempt: Any, content: bytes, files: Any = None
) -> None:
    before = fw.snapshot(tree.root)
    result = _run(st, tree, attempt, content, files=files)
    _definite(st, attempt, result)
    assert fw.snapshot(tree.root) == before
    assert _temps(tree.root) == []


def test_create_never_overwrites(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create(path=OLD))
    _refused_without_effect(st, tree, attempt, fw.NEW_CONTENT.encode())
    assert (tree.root / OLD).read_bytes() == OLD_BYTES


def test_create_refuses_a_link_at_the_target_name(world: Any, tmp_path: Path) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    outside = tmp_path / "outside.txt"
    (tree.root / NEW).symlink_to(outside)  # dangling: nothing exists behind it yet
    _refused_without_effect(st, tree, attempt, fw.NEW_CONTENT.encode())
    assert not outside.exists()


def test_replace_needs_the_expected_content(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace(expected=b"other\n"))
    _refused_without_effect(st, tree, attempt, b"synthetic replaced\n")


def test_replace_needs_the_target(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace())
    (tree.root / OLD).unlink()
    _refused_without_effect(st, tree, attempt, b"synthetic replaced\n")


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", pytest.param("char-device", marks=privileged.MKNOD)]
)
def test_replace_refuses_special_targets(
    world: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace())
    target = tree.root / OLD
    outside = tmp_path / "outside.txt"
    outside.write_bytes(OLD_BYTES)
    if kind == "hardlink":
        os.link(target, tree.root / "workspace/out/second.txt")
    else:
        target.unlink()
        if kind == "symlink":
            target.symlink_to(outside)
        elif kind == "fifo":
            os.mkfifo(target, 0o600)
        else:
            os.mknod(target, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    _refused_without_effect(st, tree, attempt, b"synthetic replaced\n")
    assert outside.read_bytes() == OLD_BYTES


def test_substituted_content_is_not_written(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    spy = fw.Spy(monkeypatch)
    before = fw.snapshot(tree.root)
    result = _run(st, tree, attempt, b"substituted bytes\n")
    monkeypatch.undo()
    _definite(st, attempt, result)
    assert spy.changes() == []  # refused at the ticket, before any file was created
    assert fw.snapshot(tree.root) == before


def test_a_write_over_the_limit_is_refused(world: Any) -> None:
    st, tree = world
    big = "x" * (file_broker.MAX_WRITE_BYTES + 1)
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create(content=big))
    _refused_without_effect(st, tree, attempt, big.encode())


@pytest.mark.parametrize("level", ["workspace", "workspace/out"])
def test_a_link_in_the_parent_chain_refuses_the_write(
    world: Any, tmp_path: Path, level: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    outside = tmp_path / "outside"
    fw.write_files(outside, {"out/keep.txt": b"keep\n"})
    before_outside = fw.snapshot(outside)

    def swap() -> None:
        (tree.root / level).rename(tree.root / "aside")
        (tree.root / level).symlink_to(outside / "out" if level.endswith("out") else outside)

    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), swap)
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    _definite(st, attempt, result)
    assert fw.snapshot(outside) == before_outside


# ================================================================ exception injection


def _fail_on(
    monkeypatch: pytest.MonkeyPatch, name: str, *, when: Any = None, err: int = errno.EIO
) -> None:
    real = getattr(os, name)

    def failing(*args: Any, **kwargs: Any) -> Any:
        if when is None or when(*args, **kwargs):
            raise OSError(err, f"synthetic {name} failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(os, name, failing)


def _is_dir_fd(fd: int, *_a: Any, **_k: Any) -> bool:
    return stat.S_ISDIR(os.fstat(fd).st_mode)


def _is_file_fd(fd: int, *_a: Any, **_k: Any) -> bool:
    return not _is_dir_fd(fd)


@pytest.mark.parametrize(
    ("name", "when", "err"),
    [
        pytest.param("write", None, errno.ENOSPC, id="write-no-space"),
        pytest.param("fsync", _is_file_fd, errno.EIO, id="file-fsync"),
    ],
)
@pytest.mark.parametrize("mode", ["create", "replace"])
def test_failures_before_the_commit_change_nothing(
    world: Any, monkeypatch: pytest.MonkeyPatch, name: str, when: Any, err: int, mode: str
) -> None:
    st, tree = world
    inp, data = (
        (_create(), fw.NEW_CONTENT.encode())
        if mode == "create"
        else (_replace(), b"synthetic replaced\n")
    )
    attempt = fw.consumed(st, tree, write=True, canonical_input=inp)
    before = fw.snapshot(tree.root, dir_times=False)
    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: _fail_on(monkeypatch, name, when=when, err=err)
    )
    result = _run(st, tree, attempt, data, files=files)
    monkeypatch.undo()
    _definite(st, attempt, result)
    # The target is unchanged and the temporary file is gone again (only the
    # directory's times show that it existed).
    assert fw.snapshot(tree.root, dir_times=False) == before
    assert _temps(tree.root) == []


def test_a_write_returning_zero_is_a_definite_failure(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    before = fw.snapshot(tree.root, dir_times=False)
    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: monkeypatch.setattr(os, "write", lambda f, d: 0)
    )
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    monkeypatch.undo()
    _definite(st, attempt, result)
    assert fw.snapshot(tree.root, dir_times=False) == before


def test_a_target_appearing_before_the_link_is_not_overwritten(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception injection: another writer creates the target between the check and
    the link (outside the single-writer assumption); link refuses with EEXIST."""
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    real_link = os.link

    def link(*args: Any, **kwargs: Any) -> None:
        (tree.root / NEW).write_bytes(b"theirs\n")
        real_link(*args, **kwargs)

    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: monkeypatch.setattr(os, "link", link)
    )
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    monkeypatch.undo()
    _definite(st, attempt, result)
    assert (tree.root / NEW).read_bytes() == b"theirs\n"
    assert _temps(tree.root) == []


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        pytest.param("rename", "replace", id="rename-fails"),
        pytest.param("link", "create", id="link-fails"),
    ],
)
def test_a_failing_commit_is_an_unknown_outcome(
    world: Any, monkeypatch: pytest.MonkeyPatch, name: str, mode: str
) -> None:
    st, tree = world
    inp, data = (
        (_create(), fw.NEW_CONTENT.encode())
        if mode == "create"
        else (_replace(), b"synthetic replaced\n")
    )
    attempt = fw.consumed(st, tree, write=True, canonical_input=inp)
    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), lambda: _fail_on(monkeypatch, name))
    with pytest.raises(FileOperationFailedError) as excinfo:
        _run(st, tree, attempt, data, files=files)
    monkeypatch.undo()
    assert excinfo.value.effect is FileEffect.UNKNOWN
    _unknown(st, attempt)
    # Nothing is restored by guessing; the temporary file is left for recovery.
    assert len(_temps(tree.root)) == 1
    assert (tree.root / OLD).read_bytes() == OLD_BYTES


@pytest.mark.parametrize(
    "fault",
    [
        pytest.param("dir-fsync", id="directory-fsync-after-commit"),
        pytest.param("temp-unlink", id="temporary-link-removal-after-create"),
        pytest.param("verify", id="content-differs-after-commit"),
    ],
)
def test_a_failure_after_the_commit_is_an_unknown_outcome(
    world: Any, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    real_link = os.link

    def arm() -> None:
        if fault == "dir-fsync":
            _fail_on(monkeypatch, "fsync", when=_is_dir_fd)
        elif fault == "temp-unlink":
            _fail_on(monkeypatch, "unlink")
        else:

            def link(*args: Any, **kwargs: Any) -> None:
                real_link(*args, **kwargs)
                with (tree.root / NEW).open("ab") as handle:  # changed right after
                    handle.write(b"tampered\n")

            monkeypatch.setattr(os, "link", link)

    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), arm)
    with pytest.raises(FileOperationFailedError) as excinfo:
        _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    monkeypatch.undo()
    assert excinfo.value.effect is FileEffect.UNKNOWN
    _unknown(st, attempt)
    assert (tree.root / NEW).exists()  # committed: kept as it is, never rolled back


def test_an_unknown_outcome_is_not_retried(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace())
    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), lambda: _fail_on(monkeypatch, "rename"))
    with pytest.raises(FileOperationFailedError):
        _run(st, tree, attempt, b"synthetic replaced\n", files=files)
    monkeypatch.undo()
    after_failure = fw.snapshot(tree.root)
    spy = fw.Spy(monkeypatch)
    with pytest.raises((PermitRejectedError, IllegalTransitionError)):
        _run(st, tree, st.sm.state.attempts[attempt.attempt_id], b"synthetic replaced\n")
    monkeypatch.undo()
    assert spy.changes() == []
    assert fw.snapshot(tree.root) == after_failure
    _unknown(st, attempt)


# ================================================================ concurrency, stop, revocation


def test_a_second_writer_is_refused_while_a_write_runs(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    seen: list[tuple[str, ...]] = []
    real_fsync = os.fsync
    other = fw.broker(st.keys.owner, tree)
    descriptor = st.journal.recorded_descriptor(attempt.attempt_id)

    def fsync(fd: int) -> None:
        if not seen:
            try:
                other.prepare(descriptor, st.journal.policy(b.TENANT)).close()
                seen.append(("prepared",))
            except FileOperationRefusedError as exc:
                seen.append(exc.reasons)
        real_fsync(fd)

    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: monkeypatch.setattr(os, "fsync", fsync)
    )
    result = _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files)
    monkeypatch.undo()
    assert result.attempt.state is A.COMPLETED
    assert seen == [("WORKSPACE_BUSY",)]
    other.prepare(descriptor, st.journal.policy(b.TENANT)).close()  # free again


@pytest.mark.parametrize("change", ["stop", "revoke"])
def test_a_stop_or_revocation_after_preparing_prevents_the_write(world: Any, change: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    gate = lw.gate(st)
    before = fw.snapshot(tree.root)
    run_id = st.sm.state.steps[attempt.step_id].run_id

    def act() -> None:
        if change == "stop":
            gate.stop(st.signed(lw.stop_op(StopScopeType.RUN, str(run_id))))
        else:
            st.owner(RevokeAccess(op="revoke_access", tenant_id=b.TENANT))

    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), act)
    with pytest.raises(PermitRejectedError):
        _run(st, tree, attempt, fw.NEW_CONTENT.encode(), files=files, gate=gate)
    assert fw.snapshot(tree.root) == before
    current = st.sm.state.attempts[attempt.attempt_id]
    assert current.state in (A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED)
    # Not started: the live gate reports it and the hold is released.
    aborted = gate.abort_not_dispatched(current)
    assert aborted.state is A.ABORTED_BEFORE_DISPATCH
    assert st.journal.reservation(lw.rsv(attempt)).hold_micro == 0
    # The lock went with the prepared tree.
    fw.broker(st.keys.owner, tree).prepare(
        st.journal.recorded_descriptor(attempt.attempt_id), st.journal.policy(b.TENANT)
    ).close()


# ================================================================ temporary files


def _policy_now(st: Any) -> Any:
    return st.journal.policy(b.TENANT)


def _temp_named(tree: fw.Tree, where: str, name: str, data: bytes = b"left over\n") -> Path:
    path = tree.root / where / name
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def test_only_temporaries_of_listed_receipts_are_recovered(world: Any) -> None:
    """WP08-R07: a name of the right form is not enough. Only a temporary file whose
    tag names a receipt the caller listed (from the ledger) is removed."""
    st, tree = world
    mine, other = b.new_id(ReceiptId), b.new_id(ReceiptId)
    listed = _temp_named(tree, "workspace/out", TEMP_FILE_PREFIX + temp_tag(mine) + "0" * 16)
    unlisted = _temp_named(tree, "workspace/out", TEMP_FILE_PREFIX + temp_tag(other) + "1" * 16)
    untagged = _temp_named(tree, "workspace", TEMP_FILE_PREFIX + "2" * 32)
    lookalike = _temp_named(tree, "workspace", TEMP_FILE_PREFIX + "not-hex")
    linked = tree.root / "workspace" / (TEMP_FILE_PREFIX + temp_tag(mine) + "3" * 16)
    linked.symlink_to(tree.root / OLD)
    before = {p: (tree.root / p).read_bytes() for p in fw.FILES}
    result = fw.broker(st.keys.owner, tree).recover_temporaries(
        policy=_policy_now(st), tenant_id=b.TENANT, receipts=(mine,)
    )
    assert result.removed == (str(listed.relative_to(tree.root)),)
    assert set(result.kept) == {str(p.relative_to(tree.root)) for p in (unlisted, untagged, linked)}
    assert not listed.exists()
    assert unlisted.exists() and untagged.exists() and lookalike.exists()  # noqa: PT018
    assert linked.is_symlink()  # only regular files of ours are ever removed
    assert {p: (tree.root / p).read_bytes() for p in fw.FILES} == before


@pytest.mark.parametrize("policy", ["none", "untrusted", "other-tenant"])
def test_recovery_needs_the_current_policy_to_trust_the_tree(world: Any, policy: str) -> None:
    st, tree = world
    receipt = b.new_id(ReceiptId)
    temp = _temp_named(tree, "workspace/out", TEMP_FILE_PREFIX + temp_tag(receipt) + "0" * 16)
    chosen = {
        "none": None,
        "untrusted": s.policy(trusted_synthetic_workspaces=()),
        "other-tenant": s.policy(
            tenant_id=b.new_id(TenantId), trusted_synthetic_workspaces=(tree.digest,)
        ),
    }[policy]
    with pytest.raises(WorkspaceRejectedError) as excinfo:
        fw.broker(st.keys.owner, tree).recover_temporaries(
            policy=chosen, receipts=(receipt,), tenant_id=b.TENANT
        )
    assert excinfo.value.reasons == ("WORKSPACE_NOT_TRUSTED",)
    assert temp.exists()


def test_recovery_waits_for_the_writer_lock(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_create())
    broker = fw.broker(st.keys.owner, tree)
    with (
        broker.prepare(st.journal.recorded_descriptor(attempt.attempt_id), _policy_now(st)),
        pytest.raises(FileOperationRefusedError) as excinfo,
    ):
        fw.broker(st.keys.owner, tree).recover_temporaries(
            policy=_policy_now(st), tenant_id=b.TENANT, receipts=()
        )
    assert excinfo.value.reasons == ("WORKSPACE_BUSY",)


def test_a_left_temporary_is_tagged_with_its_receipt(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, write=True, canonical_input=_replace())
    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), lambda: _fail_on(monkeypatch, "rename"))
    with pytest.raises(FileOperationFailedError):
        _run(st, tree, attempt, b"synthetic replaced\n", files=files)
    monkeypatch.undo()
    (left,) = _temps(tree.root)
    assert Path(left).name.startswith(TEMP_FILE_PREFIX + temp_tag(str(attempt.receipt_id)))
    # The runtime lists exactly the unknown file operations of this tree.
    attempts = list(st.sm.state.attempts.values())
    receipts = recovery_receipts(ledger=st.journal, attempts=attempts, workspace_ref=tree.digest)
    assert receipts == (str(attempt.receipt_id),)
    result = fw.broker(st.keys.owner, tree).recover_temporaries(
        policy=_policy_now(st), tenant_id=b.TENANT, receipts=receipts
    )
    assert result.removed == (left,)
    assert (tree.root / OLD).read_bytes() == OLD_BYTES  # the target is never touched
    _unknown(st, attempt)  # recovery settles nothing and retries nothing


# ================================================================ real process stop


def _run_child(st: Any, tree: fw.Tree, point: str, attempt: Any, data: bytes) -> Any:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests"))
    canonical = st.contents[attempt.attempt_id].canonical_input
    stdin = "\n".join(
        (
            st.keys.owner.reveal().hex(),
            st.keys.permit.reveal().hex(),
            canonical.encode().hex(),
            data.hex(),
        )
    )
    return subprocess.run(
        [
            sys.executable,
            str(CHILD),
            point,
            str(st.path),
            str(attempt.attempt_id),
            str(st.pep),
            str(tree.area),
            tree.workspace_id,
        ],
        input=stdin + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def _before_permit(st: Any, tree: fw.Tree, canonical_input: str) -> Any:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(
        st,
        step,
        scopes,
        registration=s.WRITE_TOOL,
        workspace_ref=tree.digest,
        canonical_input=canonical_input,
    )
    return lw.intent(st, lw.reserve(st, attempt, 1_000))


POINTS = ["after-temp-create", "after-temp-write", "before-commit", "after-commit"]


@pytest.mark.parametrize("mode", ["create", "replace"])
@pytest.mark.parametrize("point", POINTS)
def test_a_process_stopped_during_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str, mode: str
) -> None:
    st, tree = fw.world(tmp_path)
    inp, data, target = (
        (_create(), fw.NEW_CONTENT.encode(), NEW)
        if mode == "create"
        else (_replace(), b"synthetic replaced\n", OLD)
    )
    attempt = _before_permit(st, tree, inp)
    st.journal.close()
    result = _run_child(st, tree, point, attempt, data)
    assert result.returncode == -9, result.stdout + result.stderr[-2000:]
    assert "stopping" in result.stdout

    st2 = st.reopen()
    try:
        stopped = st2.sm.state.attempts[attempt.attempt_id]
        # The start was committed before the callback; the outcome is not known.
        assert stopped.state is A.DISPATCH_STARTED
        st2.journal.apply_ledger((RecoverInFlight(op="recover_in_flight"),))
        view = st2.journal.reservation(lw.rsv(attempt))
        assert view is not None
        assert (view.state, view.hold_micro) == (R.UNKNOWN, 1_000)

        temps = _temps(tree.root)
        committed = point == "after-commit"
        if mode == "create":
            assert (tree.root / target).exists() is committed
        else:
            expected = data if committed else OLD_BYTES
            assert (tree.root / target).read_bytes() == expected
        # A temporary file is left unless REPLACE's rename already consumed it.
        assert len(temps) == (0 if (committed and mode == "replace") else 1)

        # Recovery removes only the broker's temporary files; never the target,
        # and nothing is written again.
        receipts = recovery_receipts(
            ledger=st2.journal,
            attempts=list(st2.sm.state.attempts.values()),
            workspace_ref=tree.digest,
        )
        assert receipts == (str(stopped.receipt_id),)
        recovered = fw.broker(st2.keys.owner, tree).recover_temporaries(
            policy=st2.journal.policy(b.TENANT), tenant_id=b.TENANT, receipts=receipts
        )
        assert sorted(recovered.removed) == temps
        assert recovered.kept == ()
        assert _temps(tree.root) == []
        if committed:
            assert (tree.root / target).read_bytes() == data
            assert os.lstat(tree.root / target).st_nlink == 1
        elif mode == "create":
            assert not (tree.root / target).exists()
        else:
            assert (tree.root / target).read_bytes() == OLD_BYTES
        spy = fw.Spy(monkeypatch)
        with pytest.raises((PermitRejectedError, IllegalTransitionError)):
            run_file_operation(
                gate=lw.gate(st2),
                ledger=st2.journal,
                files=fw.broker(st2.keys.owner, tree),
                registry=s.REGISTRY,
                attempt=st2.sm.state.attempts[attempt.attempt_id],
                content=ToolContent(kind="tool", canonical_input=inp),
                registration=s.WRITE_TOOL,
                write_content=data,
            )
        monkeypatch.undo()
        assert spy.changes() == []
    finally:
        st2.journal.close()
