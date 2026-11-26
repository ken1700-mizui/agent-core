"""WP-08 stage 2 (WP08-R03, R04): reading a real file of a managed synthetic tree
through the execution gate, and binding the execution to the ticket and to the
trusted executable (``artifact_hash``).

Exception injection (monkeypatched os calls) is marked as such; there is no
process stop in this stage (stage 3).
"""

from __future__ import annotations

import os
import signal
import stat
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import file_broker
from agent_core.adapters.file_broker import SyntheticFileBroker, builtin_artifact_hash
from agent_core.runtime.file_execution import run_file_operation
from agent_core.safety.descriptor import pin_of
from agent_core.tool.registry import ToolRegistry, TrustedToolCatalog
from agent_core.types.cost import ReservationState
from agent_core.types.records import AttemptState
from agent_core.types.safety import ReceiptState
from agent_core.types.tools import canonical_json
from agent_core.types.transitions import AbortReason
from support import builders as b
from support import files_world as fw
from support import ledger_world as lw
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = ReservationState
TARGET = "workspace/a.txt"


@pytest.fixture
def world(tmp_path: Path) -> Any:
    st, tree = fw.world(tmp_path)
    yield st, tree
    if not st.journal.closed:
        st.journal.close()


@pytest.fixture(autouse=True)
def _no_hang() -> Any:
    """A FIFO or device opened by mistake must fail the test, not hang it."""
    previous = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("blocked on a file"))
    signal.alarm(20)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def _run(st: Any, tree: fw.Tree, attempt: Any, files: Any = None, gate: Any = None) -> Any:
    return run_file_operation(
        gate=gate if gate is not None else lw.gate(st),
        ledger=st.journal,
        files=files if files is not None else fw.broker(st.keys.owner, tree),
        registry=s.REGISTRY,
        attempt=attempt,
        write_content=None,
        **lw.actual(st, attempt),
    )


def _leaf_opens(spy: fw.Spy, name: str) -> int:
    return sum(1 for n, p, _d in spy.calls if n == "open" and p == name)


def _definite(st: Any, attempt: Any, result: Any) -> None:
    assert result.attempt.state is A.FAILED_DEFINITE
    assert result.data is None
    assert result.output is None
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    view = st.journal.reservation(attempt.reservation_id)
    assert (view.hold_micro, view.spent_micro) == (0, 0)


# ================================================================ normal read


def test_a_file_is_read_through_the_gate(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    before = fw.snapshot(tree.root)
    result = _run(st, tree, attempt)
    assert result.attempt.state is A.COMPLETED
    assert result.data == fw.FILES[TARGET]
    expected = canonical_json({"content": "synthetic a\n", "truncated": False}).decode()
    assert result.output.canonical_output == expected
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    view = st.journal.reservation(attempt.reservation_id)
    assert view.hold_micro == 0
    assert fw.snapshot(tree.root) == before  # reading changes nothing


def test_the_ticket_is_redeemed_before_any_part_of_the_target_is_opened(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    gate = lw.gate(st)
    redeem = gate.redeem
    holder: list[fw.Spy] = []

    def recorded(ticket: Any, operation: Any) -> Any:
        holder[0].calls.append(("redeem", operation.path, None))
        return redeem(ticket, operation)

    monkeypatch.setattr(gate, "redeem", recorded)
    holder.append(fw.Spy(monkeypatch))
    result = _run(st, tree, attempt, gate=gate)
    monkeypatch.undo()
    assert result.attempt.state is A.COMPLETED
    names = [(n, p) for n, p, _d in holder[0].calls if n in ("open", "redeem")]
    position = names.index(("redeem", TARGET))
    assert ("open", "workspace") not in names[:position]
    # After the redeem: one name at a time from fds, the leaf looked at, then read.
    assert names[position + 1 :] == [("open", "workspace"), ("open", "a.txt"), ("open", "a.txt")]
    assert holder[0].changes() == []


def test_a_read_in_a_moved_tree_reads_the_verified_tree(world: Any) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    real = fw.broker(st.keys.owner, tree)

    class _MoveAfterPrepare:
        def prepare(self, descriptor: Any, policy: Any) -> Any:
            prepared = real.prepare(descriptor, policy)
            tree.root.rename(tree.area / "moved")
            fw.write_files(tree.root, {TARGET: b"decoy\n"})
            return prepared

    result = _run(st, tree, attempt, files=_MoveAfterPrepare())
    assert result.attempt.state is A.COMPLETED
    assert result.data == fw.FILES[TARGET]  # not the decoy at the old path


# ================================================================ refused reads (no effect)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "socket", "char-device", "directory", "missing"]
)
def test_only_a_regular_file_with_one_link_is_read(
    world: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    target = tree.root / TARGET
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside\n")
    if kind == "hardlink":
        os.link(target, tree.root / "workspace/second.txt")
    else:
        target.unlink()
        if kind == "symlink":
            target.symlink_to(outside)
        elif kind == "fifo":
            os.mkfifo(target, 0o600)
        elif kind == "socket":
            os.mknod(target, stat.S_IFSOCK | 0o600)
        elif kind == "char-device":
            os.mknod(target, stat.S_IFCHR | 0o600, os.makedev(1, 3))
        elif kind == "directory":
            target.mkdir(mode=0o700)
    before = fw.snapshot(tree.root)
    spy = fw.Spy(monkeypatch)
    result = _run(st, tree, attempt)
    monkeypatch.undo()
    _definite(st, attempt, result)
    # Looked at with O_PATH only; never opened for reading.
    assert _leaf_opens(spy, "a.txt") == 1
    assert fw.snapshot(tree.root) == before


@pytest.mark.parametrize("level", ["first", "second"])
def test_a_link_at_any_level_refuses_the_read(world: Any, tmp_path: Path, level: str) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree, canonical_input='{"path":"workspace/out/r.txt"}')
    outside = tmp_path / "outside"
    fw.write_files(outside, {"out/r.txt": b"outside\n"})
    if level == "first":
        (tree.root / "workspace").rename(tree.root / "aside")
        (tree.root / "workspace").symlink_to(outside)
    else:
        os.rename(tree.root / "workspace/out", tree.root / "aside")
        (tree.root / "workspace/out").symlink_to(outside / "out")
    result = _run(st, tree, attempt)
    _definite(st, attempt, result)


def test_a_file_over_the_limit_is_not_opened_for_reading(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / TARGET).write_bytes(b"x" * (file_broker.MAX_READ_BYTES + 1))
    spy = fw.Spy(monkeypatch)
    result = _run(st, tree, attempt)
    monkeypatch.undo()
    _definite(st, attempt, result)
    assert _leaf_opens(spy, "a.txt") == 1


def test_growth_during_the_read_is_stopped_at_the_limit(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception injection: fstat reports a small file, the reads go past the limit."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / TARGET).write_bytes(b"x" * (file_broker.MAX_READ_BYTES + 10))
    real_fstat = os.fstat
    target_ino = os.lstat(tree.root / TARGET).st_ino

    class _Small:
        def __init__(self, info: Any) -> None:
            self._info = info

        def __getattr__(self, name: str) -> Any:
            return 10 if name == "st_size" else getattr(self._info, name)

    def fstat(fd: int) -> Any:
        info = real_fstat(fd)
        return _Small(info) if info.st_ino == target_ino else info

    monkeypatch.setattr(os, "fstat", fstat)
    result = _run(st, tree, attempt)
    monkeypatch.undo()
    _definite(st, attempt, result)


def test_a_change_during_the_read_is_refused(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception injection: the file is appended to while it is being read."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    target = tree.root / TARGET
    real_read = os.read
    done: list[bool] = []

    def read(fd: int, n: int) -> bytes:
        data = real_read(fd, n)
        if data and not done:
            done.append(True)
            with target.open("ab") as handle:
                handle.write(b"more\n")
        return data

    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: monkeypatch.setattr(os, "read", read)
    )
    result = _run(st, tree, attempt, files=files)
    monkeypatch.undo()
    _definite(st, attempt, result)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\xff\xfe not utf-8", id="not-utf-8"),
        pytest.param(b'"' * 40_000, id="output-over-max-output-bytes"),
    ],
)
def test_the_output_schema_and_size_are_enforced(world: Any, data: bytes) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    (tree.root / TARGET).write_bytes(data)
    result = _run(st, tree, attempt)
    _definite(st, attempt, result)


def test_an_io_error_while_reading_is_a_definite_failure(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception injection: reading does not change the tree, so it is definite."""
    st, tree = world
    attempt = fw.consumed(st, tree)

    def read(fd: int, n: int) -> bytes:
        raise OSError(5, "synthetic I/O error")

    files = fw.AfterPrepare(
        fw.broker(st.keys.owner, tree), lambda: monkeypatch.setattr(os, "read", read)
    )
    result = _run(st, tree, attempt, files=files)
    monkeypatch.undo()
    _definite(st, attempt, result)


# ================================================================ R04: the trusted executable


def test_the_artifact_hash_is_the_loaded_broker_code() -> None:
    source = Path(file_broker.__file__).read_bytes()
    assert builtin_artifact_hash() == file_broker.artifact_hash_of(source)
    assert file_broker.artifact_hash_of(source + b"\n# changed\n") != builtin_artifact_hash()
    assert s.READ_TOOL.artifact_hash == builtin_artifact_hash()
    assert s.WRITE_TOOL.artifact_hash == builtin_artifact_hash()


def test_the_broker_cannot_be_extended() -> None:
    with pytest.raises(TypeError):

        class _Other(SyntheticFileBroker):
            pass


def _pinned_to(artifact: str) -> Any:
    """read_file registered with another artifact hash (an older or a changed build)."""
    catalog = TrustedToolCatalog(entries=(tw.entry(s.READ_MANIFEST, artifact),))
    registry = ToolRegistry(catalog)
    return registry.register(s.READ_MANIFEST, artifact_hash=artifact)


def test_a_registration_for_another_executable_is_refused_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = lw.new_keys()
    tree = fw.make_tree(tmp_path, keys.owner)
    changed = _pinned_to(b.digest("another-build"))
    pins = (pin_of(changed),)
    st = lw.setup(
        tmp_path,
        keys=keys,
        policy=s.policy(tools=pins, trusted_synthetic_workspaces=(tree.digest,)),
    )
    try:
        monkeypatch.setattr(s, "ALL_PINS", pins)  # the run grant pins the same build
        attempt = fw.consumed(st, tree, registration=changed)
        spy = fw.Spy(monkeypatch)
        result = run_file_operation(
            gate=lw.gate(st),
            ledger=st.journal,
            files=fw.broker(st.keys.owner, tree),
            registry=s.REGISTRY,
            attempt=attempt,
            write_content=None,
            **lw.actual(st, attempt),
        )
        monkeypatch.undo()
        assert result.attempt.state is A.ABORTED_BEFORE_DISPATCH
        assert fw.history(st, attempt)[-1].evidence.reason is AbortReason.ARTIFACT_MISMATCH
        view = st.journal.reservation(lw.rsv(attempt))
        assert view is not None
        assert view.state is R.RELEASED
        assert [c for c in spy.calls if c[0] == "open"] == []
    finally:
        st.journal.close()


def test_a_changed_broker_source_is_refused(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception injection: the broker's code on disk differs from the code that was
    loaded (a later process would run other code than the pinned one)."""
    st, tree = world
    attempt = fw.consumed(st, tree)
    loaded = file_broker.current_source()
    monkeypatch.setattr(file_broker, "current_source", lambda: loaded + b"\n# changed\n")
    result = _run(st, tree, attempt)
    monkeypatch.undo()
    assert result.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert fw.history(st, attempt)[-1].evidence.reason is AbortReason.ARTIFACT_MISMATCH


def test_only_the_builtin_file_tools_run_in_the_broker(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    attempt = fw.consumed(st, tree)
    monkeypatch.setattr(file_broker, "BUILTIN_FILE_TOOLS", {})
    result = _run(st, tree, attempt)
    monkeypatch.undo()
    assert result.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert fw.history(st, attempt)[-1].evidence.reason is AbortReason.ARTIFACT_MISMATCH


def test_a_prepared_tree_serves_only_its_own_operation(world: Any) -> None:
    st, tree = world
    first = fw.consumed(st, tree)
    second = fw.consumed(st, tree)
    real = fw.broker(st.keys.owner, tree)
    descriptor = st.journal.recorded_descriptor(second.attempt_id)

    class _OtherOperation:
        def prepare(self, _descriptor: Any, policy: Any) -> Any:
            return real.prepare(descriptor, policy)  # prepared for "second"

    result = _run(st, tree, first, files=_OtherOperation())
    _definite(st, first, result)
