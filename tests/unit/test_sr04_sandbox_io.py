"""S-R04 (D20): files into a sandbox and back, through the WP-08 paths only.

All data is the developer's fixed synthetic tree (``files_world``). Nothing
runs untrusted code: the sandbox is the FakeSandbox, whose "outputs" a test
sets. The reflection into the worktree is the normal WP-08 write path (Policy,
owner approval -- the write tool is irreversible --, reservation, Permit,
Receipt, the File Broker's safe write).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.runtime.file_execution import run_file_operation
from agent_core.runtime.sandbox_io import (
    SandboxIoError,
    bundle_from_reads,
    check_outputs,
    require_input_matches,
    write_proposals,
)
from agent_core.safety.workspace import file_digest
from agent_core.types.errors import FileOperationFailedError
from agent_core.types.records import AttemptState as A
from agent_core.types.sandbox_io import BundleFile, InputBundle, IoLimits, OutputFile
from agent_core_broker.bundle import BundleRefusedError, read_bundle
from support import files_world as fw
from support import ledger_world as lw
from support import safety_world as s
from support import sandbox_world as sw

READ = "workspace/a.txt"


def _read(st: Any, tree: fw.Tree) -> Any:
    attempt = fw.consumed(st, tree)
    return run_file_operation(
        gate=lw.gate(st),
        ledger=st.journal,
        files=fw.broker(st.keys.owner, tree),
        registry=s.REGISTRY,
        attempt=attempt,
        write_content=None,
        **lw.actual(st, attempt),
    )


@pytest.fixture
def world(tmp_path: Path) -> Any:
    st, tree = fw.world(tmp_path)
    yield st, tree
    if not st.journal.closed:
        st.journal.close()


# ---------------------------------------------------------------- input


def test_the_bundle_is_built_from_completed_wp08_reads(world: Any) -> None:
    st, tree = world
    result = _read(st, tree)
    assert result.attempt.state is A.COMPLETED
    bundle = bundle_from_reads([(READ, result)])
    assert bundle.files[0].data == (tree.root / READ).read_bytes()
    assert bundle.bundle_ref() == bundle_from_reads([(READ, result)]).bundle_ref()
    require_input_matches(bundle, bundle.bundle_ref())
    with pytest.raises(SandboxIoError, match="INPUT_MISMATCH"):
        require_input_matches(bundle, "sha256:" + "0" * 64)


def test_a_read_that_did_not_complete_is_refused(world: Any) -> None:
    st, tree = world
    result = _read(st, tree)
    aborted = result._replace(
        attempt=result.attempt.model_copy(update={"state": A.FAILED_DEFINITE})
    )
    with pytest.raises(SandboxIoError, match="READ_NOT_COMPLETED"):
        bundle_from_reads([(READ, aborted)])


@pytest.mark.parametrize(
    ("files", "limits", "code"),
    [
        ([("a", b"x"), ("a", b"y")], IoLimits(), "DUPLICATE_PATH"),
        ([("a", b"x"), ("b", b"y")], IoLimits(max_files=1), "TOO_MANY_FILES"),
        ([("a", b"x" * 20)], IoLimits(max_file_bytes=10), "FILE_TOO_LARGE"),
        ([("a", b"x" * 8), ("b", b"y" * 8)], IoLimits(max_total_bytes=10), "TOTAL_TOO_LARGE"),
    ],
)
def test_output_limits(files: list[tuple[str, bytes]], limits: IoLimits, code: str) -> None:
    with pytest.raises(SandboxIoError, match=code):
        check_outputs([OutputFile.of(p, d) for p, d in files], limits=limits)


@pytest.mark.parametrize("path", ["../x", "/abs", "a/./b", "a//b", "a\\b", "", "a/../b"])
def test_unsafe_paths_are_refused(path: str) -> None:
    with pytest.raises(ValidationError):
        OutputFile.of(path, b"x")


def test_content_must_match_its_digest() -> None:
    good = OutputFile.of("a.txt", b"x")
    with pytest.raises(ValidationError):
        OutputFile(path="a.txt", content_b64=good.content_b64, digest=file_digest(b"other"))


def test_p2_checks_the_staged_bundle(tmp_path: Path) -> None:
    bundle = InputBundle(files=(BundleFile.of("a.txt", b"synthetic"),))
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json())
    assert read_bundle(path, bundle.bundle_ref()) == bundle
    with pytest.raises(BundleRefusedError, match="INPUT_MISMATCH"):
        read_bundle(path, "sha256:" + "1" * 64)
    os.symlink(path, tmp_path / "link.json")
    with pytest.raises(BundleRefusedError, match="UNREADABLE"):
        read_bundle(tmp_path / "link.json", bundle.bundle_ref())
    other = BundleFile.of("a.txt", b"tampered")
    path.write_text(path.read_text().replace(bundle.files[0].content_b64, other.content_b64))
    with pytest.raises(BundleRefusedError, match="INVALID"):
        read_bundle(path, bundle.bundle_ref())


def test_a_mismatched_bundle_never_reaches_the_sandbox(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    bundle = InputBundle(files=(BundleFile.of("a.txt", b"synthetic"),))
    attempt = sw.consumed(st)  # its program's workspace_ref is another digest
    done = sw.run_with(st, broker, attempt, input_bundle=bundle)
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


# ---------------------------------------------------------------- output


def test_outputs_come_back_checked_and_untrusted(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    broker.sandbox.outputs[attempt.receipt_id] = (OutputFile.of("out/new.txt", b"made\n"),)
    done = sw.run_with(st, broker, attempt)
    assert done.attempt.state is A.COMPLETED
    assert done.outputs is not None
    assert done.outputs.trust == "D4_UNTRUSTED_TOOL_OUTPUT"
    assert [f.path for f in done.outputs.files] == ["out/new.txt"]
    broker.close()


def test_outputs_over_p2s_limits_are_not_copied_and_not_run_again(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    broker.sandbox.outputs[attempt.receipt_id] = tuple(
        OutputFile.of(f"f{i}.txt", b"x") for i in range(40)
    )
    with pytest.raises(Exception):  # noqa: B017,PT011 (P2 refuses the copy-out after the run)
        sw.run_with(st, broker, attempt)
    found = st.sm.state.attempts[attempt.attempt_id]
    assert found.state is A.OUTCOME_UNKNOWN  # ran; the copy-out failed: unknown, hold kept
    view = st.journal.reservation(lw.rsv(found))
    assert view is not None
    assert view.hold_micro > 0
    assert broker.sandbox.runs[attempt.receipt_id] == 1  # never run again
    broker.close()


# ---------------------------------------------------------------- reflection (WP-08 only)


def _outputs(*files: tuple[str, bytes]) -> Any:
    return check_outputs([OutputFile.of(p, d) for p, d in files])


def _write(st: Any, tree: fw.Tree, proposal: Any, content: bytes | None = None) -> Any:
    attempt = fw.consumed(st, tree, write=True, canonical_input=proposal.canonical_input)
    return attempt, run_file_operation(
        gate=lw.gate(st),
        ledger=st.journal,
        files=fw.broker(st.keys.owner, tree),
        registry=s.REGISTRY,
        attempt=attempt,
        write_content=proposal.content if content is None else content,
        **lw.actual(st, attempt),
    )


def test_nothing_is_written_without_the_wp08_path(world: Any) -> None:
    st, tree = world
    before = fw.snapshot(tree.root)
    proposals = write_proposals(
        _outputs(("out/new.txt", b"made\n")), {"workspace/out/new.txt": None}
    )
    assert len(proposals) == 1
    assert fw.snapshot(tree.root) == before  # proposing writes nothing
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(
        st,
        step,
        scopes,
        workspace_ref=tree.digest,
        canonical_input=proposals[0].canonical_input,
        registration=s.WRITE_TOOL,
    )
    assert lw.allowed(st, attempt).approval_required  # the owner must approve the change


def test_an_approved_reflection_writes_exactly_the_output(world: Any) -> None:
    st, tree = world
    proposal = write_proposals(
        _outputs(("out/new.txt", b"made\n")), {"workspace/out/new.txt": None}
    )[0]
    _, result = _write(st, tree, proposal)
    assert result.attempt.state is A.COMPLETED
    assert (tree.root / "workspace/out/new.txt").read_bytes() == b"made\n"


def test_content_that_differs_from_the_approved_change_is_refused(world: Any) -> None:
    st, tree = world
    proposal = write_proposals(
        _outputs(("out/new.txt", b"made\n")), {"workspace/out/new.txt": None}
    )[0]
    _, result = _write(st, tree, proposal, content=b"something else\n")
    assert result.attempt.state is A.FAILED_DEFINITE  # refused by the File Broker: not written
    assert not (tree.root / "workspace/out/new.txt").exists()


def test_a_replace_of_a_file_that_changed_since_the_proposal_is_refused(world: Any) -> None:
    st, tree = world
    old = (tree.root / "workspace/out/r.txt").read_bytes()
    proposal = write_proposals(
        _outputs(("out/r.txt", b"replaced\n")), {"workspace/out/r.txt": old + b"stale"}
    )[0]
    _, result = _write(st, tree, proposal)
    assert result.attempt.state is A.FAILED_DEFINITE  # the REPLACE check refused it
    assert (tree.root / "workspace/out/r.txt").read_bytes() == old  # before == after


def test_an_unchanged_or_unread_target_is_not_proposed() -> None:
    outputs = _outputs(("a.txt", b"same"))
    assert write_proposals(outputs, {"workspace/a.txt": b"same"}) == ()
    with pytest.raises(SandboxIoError, match="TARGET_NOT_READ"):
        write_proposals(outputs, {})
    with pytest.raises(SandboxIoError, match="NOT_TEXT"):
        write_proposals(_outputs(("b.bin", b"\xff\xfe")), {"workspace/b.bin": None})


def test_a_crash_during_reflection_is_unknown_and_keeps_the_hold(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, tree = world
    proposal = write_proposals(
        _outputs(("out/new.txt", b"made\n")), {"workspace/out/new.txt": None}
    )[0]
    attempt = fw.consumed(st, tree, write=True, canonical_input=proposal.canonical_input)

    def fail() -> None:
        def boom(*_: Any, **__: Any) -> None:
            raise OSError("synthetic failure at the commit")

        monkeypatch.setattr(os, "link", boom)

    files = fw.AfterPrepare(fw.broker(st.keys.owner, tree), fail)
    with pytest.raises(FileOperationFailedError):
        run_file_operation(
            gate=lw.gate(st),
            ledger=st.journal,
            files=files,
            registry=s.REGISTRY,
            attempt=attempt,
            write_content=proposal.content,
            **lw.actual(st, attempt),
        )
    monkeypatch.undo()
    found = st.sm.state.attempts[attempt.attempt_id]
    assert found.state is A.OUTCOME_UNKNOWN  # not guessed either way, never retried
    assert st.journal.reservation(lw.rsv(attempt)).hold_micro == 1_000
