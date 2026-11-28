"""Batch 1-A remaining fixes (D17): B1A-01 to B1A-05.

B1A-01 durability of P2's execution record. What the record finds, and what it
does not claim:

- an entry changed in the middle: hash chain and per-entry MAC (broker key);
- whole entries lost at the end: the separate head file (seq, hash, MAC);
- a write or fsync failure: the record stops being used in that process, and
  what was written is checked again at the next open; a partial last line is
  cut off (it was never acknowledged);
- a replaced file: record_id (in the genesis entry and the head) and MACs;
- a crash between an entry and its head: the head may be one entry behind and
  is moved forward; anything else refuses the record.
- NOT found: a consistent roll-back of both files to an older genuine copy, or
  any change by someone holding the key (root). Not claimed.

B1A-02 one writer: an exclusive lock on the record, taken before it is read.
B1A-03 a stop or a refusal is a definite result only with P2's evidence that
nothing left the sandbox; otherwise OUTCOME_UNKNOWN with the hold kept.
B1A-04 the program run is the one approved: it is read from the approved
content (sandbox tool input), and a different expectation is refused before
dispatch.
B1A-05 monitoring cannot stop indefinitely: P2's deadline tick runs on its own
cycle; its delay is bounded by the longest request under the lock.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime.sandbox_execution import SandboxProgram, sandbox_program_of
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import RequestId
from agent_core.types.records import AttemptState as A
from agent_core.types.safety import ToolContent
from agent_core.types.sandbox import Containment, SandboxState, StartStatus, StatusRequest
from agent_core_broker.record import (
    ExecutionRecord,
    ExecutionRecordBusyError,
    ExecutionRecordError,
    read_entries,
)
from support import broker_world as bw
from support import builders as b
from support import safety_world as s
from support import sandbox_world as sw


def _status(client: Any, start: Any) -> Any:
    return client.status(
        StatusRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )


def _two_starts(tmp_path: Path) -> list[Any]:
    broker = bw.make_broker(tmp_path)
    starts = [bw.start_request(), bw.start_request()]
    for start in starts:
        bw.make_client(broker).start(start)
    broker.close()
    return starts


# ---------------------------------------------------------------- B1A-01


def test_whole_entries_lost_at_the_end_are_found_by_the_head(tmp_path: Path) -> None:
    _two_starts(tmp_path)
    path = bw.record_path(tmp_path)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-2]))  # a valid chain, shorter than the head
    with pytest.raises(ExecutionRecordError, match="head"):
        ExecutionRecord.open(path, bw.KEY)


def test_a_missing_or_forged_head_is_refused(tmp_path: Path) -> None:
    _two_starts(tmp_path)
    head = bw.head_path(tmp_path)
    data = json.loads(head.read_text())
    data["seq"] -= 1
    head.write_text(json.dumps(data))
    with pytest.raises(ExecutionRecordError, match="head"):
        ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)
    head.unlink()
    with pytest.raises(ExecutionRecordError, match="head"):
        ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)


def test_entries_rewritten_without_the_key_are_refused(tmp_path: Path) -> None:
    """Recomputing the chain hashes is not enough: each entry carries a MAC."""
    from agent_core_broker import record as rec

    _two_starts(tmp_path)
    path = bw.record_path(tmp_path)
    entries = [json.loads(x) for x in path.read_bytes().splitlines()]
    entries[-1]["state"] = "REAPED"
    previous = entries[-2]["hash"]
    entries[-1]["prev"] = previous
    entries[-1]["hash"] = rec.entry_hash(entries[-1])
    path.write_bytes(b"".join(json.dumps(e, sort_keys=True).encode() + b"\n" for e in entries))
    with pytest.raises(ExecutionRecordError):
        ExecutionRecord.open(path, bw.KEY)


def test_another_genuine_record_cannot_be_swapped_in(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _two_starts(first)
    _two_starts(second)
    bw.record_path(first).write_bytes(bw.record_path(second).read_bytes())
    with pytest.raises(ExecutionRecordError, match=r"record_id|head"):
        ExecutionRecord.open(bw.record_path(first), bw.KEY)


def test_a_crash_between_an_entry_and_its_head_moves_the_head_forward(tmp_path: Path) -> None:
    from agent_core_broker import record as rec

    starts = _two_starts(tmp_path)
    entries = read_entries(bw.record_path(tmp_path))
    behind = rec.head_document(bw.KEY, entries[0]["record_id"], entries[-2])
    bw.head_path(tmp_path).write_text(json.dumps(behind))  # the crash window
    record = ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)
    try:
        assert record.state(starts[1].receipt_id) is not SandboxState.NOT_SEEN
        assert json.loads(bw.head_path(tmp_path).read_text())["seq"] == len(entries)
    finally:
        record.close()


def test_a_head_more_than_one_entry_behind_is_refused(tmp_path: Path) -> None:
    head = bw.head_path(tmp_path)
    broker = bw.make_broker(tmp_path)
    broker.close()
    early = head.read_text()
    _two_starts(tmp_path)
    head.write_text(early)
    with pytest.raises(ExecutionRecordError, match="head"):
        ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)


def test_a_partial_last_line_is_cut_off_and_nothing_else_changes(tmp_path: Path) -> None:
    starts = _two_starts(tmp_path)
    path = bw.record_path(tmp_path)
    whole = path.read_bytes()
    path.write_bytes(whole + b'{"seq": 99, "kind": "sta')  # a write that never finished
    record = ExecutionRecord.open(path, bw.KEY)
    try:
        assert record.repaired_partial_tail
        assert record.state(starts[1].receipt_id) is not SandboxState.NOT_SEEN
    finally:
        record.close()
    assert path.read_bytes() == whole


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_a_failed_write_or_fsync_refuses_the_start_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    # The supervisor's record is kept in memory: the fault is P2's record only.
    broker = bw.make_broker(tmp_path, supervisor="memory")
    client = bw.make_client(broker)
    client.start(bw.start_request())  # epoch entry written already
    start = bw.start_request()

    def broken(*_args: Any) -> Any:
        raise OSError("synthetic I/O failure")

    monkeypatch.setattr(os, failure, broken)
    with pytest.raises(SandboxUnavailableError) as excinfo:
        client.start(start)
    monkeypatch.undo()
    assert excinfo.value.refusal == "RECORD_UNAVAILABLE"
    assert start.receipt_id not in broker.sandbox.runs
    # The record is no longer used by this broker, even for a new request.
    with pytest.raises(SandboxUnavailableError):
        client.start(bw.start_request())
    broker.close()


def test_after_an_fsync_failure_a_restart_keeps_what_reached_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ACCEPTED line was written but its fsync failed: after the restart the
    Receipt counts as used (never run again), not as unseen."""
    broker = bw.make_broker(tmp_path, supervisor="memory")  # the fault is P2's record only
    client = bw.make_client(broker)
    client.start(bw.start_request())
    start = bw.start_request()
    real = os.fsync
    calls = {"n": 0}

    def fail_first(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("synthetic fsync failure")
        real(fd)

    monkeypatch.setattr(os, "fsync", fail_first)
    with pytest.raises(SandboxUnavailableError):
        client.start(start)
    monkeypatch.undo()
    broker.close()
    restarted = bw.make_broker(tmp_path)
    again = bw.make_client(restarted).start(start)
    assert again.status is StartStatus.DUPLICATE
    assert start.receipt_id not in restarted.sandbox.runs
    restarted.close()


def test_a_consistent_rollback_of_both_files_is_not_detected(tmp_path: Path) -> None:
    """Documented limit: an older genuine copy of the record and its head together
    opens. This is why the record is not a defence against root or storage rollback."""
    broker = bw.make_broker(tmp_path)
    broker.close()
    saved = (bw.record_path(tmp_path).read_bytes(), bw.head_path(tmp_path).read_bytes())
    starts = _two_starts(tmp_path)
    bw.record_path(tmp_path).write_bytes(saved[0])
    bw.head_path(tmp_path).write_bytes(saved[1])
    record = ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)
    try:
        assert record.state(starts[0].receipt_id) is SandboxState.NOT_SEEN  # not found
    finally:
        record.close()


# ---------------------------------------------------------------- B1A-02


def test_one_writer_per_record(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    with pytest.raises(ExecutionRecordBusyError):
        ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)
    second = bw.make_broker(tmp_path)  # a second broker on the same record
    start = bw.start_request()
    with pytest.raises(SandboxUnavailableError) as excinfo:
        bw.make_client(second).start(start)
    assert excinfo.value.refusal == "RECORD_UNAVAILABLE"
    broker.close()
    reopened = bw.make_broker(tmp_path)  # the lock is taken again after the first ends
    assert bw.make_client(reopened).start(start).status is StartStatus.STARTED
    reopened.close()


def test_the_lock_is_taken_before_the_record_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = bw.make_broker(tmp_path)
    reads: list[Path] = []
    original = Path.read_bytes

    def spy(self: Path) -> bytes:
        reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    with pytest.raises(ExecutionRecordBusyError):
        ExecutionRecord.open(bw.record_path(tmp_path), bw.KEY)
    assert bw.record_path(tmp_path) not in reads
    monkeypatch.undo()
    broker.close()


def test_entries_can_be_read_for_diagnosis_without_the_lock(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    bw.make_client(broker).start(bw.start_request())
    assert read_entries(bw.record_path(tmp_path))[-1]["state"] == "FINISHED"
    broker.close()


# ---------------------------------------------------------------- B1A-03


def test_p2_reports_whether_effects_stayed_inside(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    contained, leaky = bw.start_request(), bw.start_request(program="leaky")
    client.start(contained)
    client.start(leaky)
    assert _status(client, contained).containment is Containment.CONTAINED
    assert _status(client, leaky).containment is Containment.UNKNOWN
    broker.close()


def test_a_stop_without_containment_evidence_is_an_unknown_outcome(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="leaky_hang")
    with pytest.raises(SandboxUnavailableError):
        sw.run(st, broker, attempt, max_polls=1)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    broker.close()


def test_a_finished_program_without_containment_evidence_is_unknown(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="leaky")
    with pytest.raises(SandboxUnavailableError):
        sw.run(st, broker, attempt)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    broker.close()


def test_a_contained_stop_is_a_definite_failure(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    done = sw.run(st, broker, sw.consumed(st, program="hang"), max_polls=1)
    assert done.attempt.state is A.FAILED_DEFINITE
    broker.close()


def test_an_authenticated_refusal_before_acceptance_is_a_definite_failure(tmp_path: Path) -> None:
    """P2 refused the start before recording it: nothing ran (its record shows it)."""
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    broker.sandbox.refuse_next_program = True  # the program is withdrawn after prepare
    done = sw.run(st, broker, attempt)
    assert done.attempt.state is A.FAILED_DEFINITE
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


# ---------------------------------------------------------------- B1A-04


def test_the_program_is_read_from_the_approved_content() -> None:
    program = sw.program()
    content = ToolContent(kind="tool", canonical_input=sw.program_input(program))
    assert sandbox_program_of(content, sw.SANDBOX_TOOL) == program
    with pytest.raises(ValueError, match="sandbox tool"):
        sandbox_program_of(content, s.SYNTH_TOOL)  # not the sandbox tool
    with pytest.raises(ValueError, match="fields"):
        sandbox_program_of(
            ToolContent(kind="tool", canonical_input='{"value":"x"}'), sw.SANDBOX_TOOL
        )


@pytest.mark.parametrize(
    "field",
    [
        "program_ref",
        "image_digest",
        "sandbox_profile_hash",
        "workspace_ref",
        "deadline_ms",
        "grace_ms",
    ],
)
def test_an_expectation_other_than_the_approved_program_is_refused_before_dispatch(
    tmp_path: Path, field: str
) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    approved = sw.program()
    other: dict[str, Any] = {
        "program_ref": bw.fake_ref("exit1"),
        "image_digest": b.digest("another image"),
        "sandbox_profile_hash": b.digest("another profile"),
        "workspace_ref": b.digest("another workspace"),
        "deadline_ms": approved.deadline_ms + 1,
        "grace_ms": approved.grace_ms + 1,
    }
    expected = SandboxProgram(**{**approved.__dict__, field: other[field]})
    done = sw.run(st, broker, attempt, program=expected)
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


def test_the_approved_program_runs(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    done = sw.run(st, broker, sw.consumed(st))
    assert done.attempt.state is A.COMPLETED
    broker.close()


# ---------------------------------------------------------------- B1A-05


def test_the_deadline_tick_runs_on_its_own_cycle(tmp_path: Path) -> None:
    clock = bw.ManualMonotonic()
    broker = bw.make_broker(tmp_path, clock=clock)
    hang = bw.start_request(program="hang", deadline_ms=1_000)
    bw.make_client(broker).start(hang)
    clock.now += 2_000
    watchdog = broker.start_watchdog(cycle_s=0.02)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if broker.record_state(hang.receipt_id) is SandboxState.STOPPED:
                break
            time.sleep(0.01)
        assert broker.record_state(hang.receipt_id) is SandboxState.STOPPED
    finally:
        watchdog.stop()
    broker.close()


def test_the_tick_delay_is_bounded_by_the_longest_request(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    broker.sandbox.start_delay_s = 0.3  # a slow backend call under the lock
    watchdog = broker.start_watchdog(cycle_s=0.02)
    try:
        thread = threading.Thread(target=bw.make_client(broker).start, args=(bw.start_request(),))
        thread.start()
        thread.join(timeout=5)
        deadline = time.monotonic() + 2  # the tick waiting for the lock finishes after it
        while watchdog.ticks == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert watchdog.ticks > 0
        assert watchdog.max_delay_s < 0.3 + 0.02 + 0.5
    finally:
        watchdog.stop()
    broker.close()
