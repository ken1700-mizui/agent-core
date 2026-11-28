"""Sandbox remaining issues (D18): S-R02 deadlines after a P2 restart, S-R03 the
liveness record (heartbeat) and its use before Permits and sandbox starts.

S-R02: after a restart, P2 measures elapsed time with the kernel's boot clock
(CLOCK_BOOTTIME and the boot ID), which wall-clock changes do not move. If the
deadline cannot be restored safely (another boot, no clock), P2 stops at once.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.heartbeat_file import HeartbeatFile
from agent_core.runtime.monitor import (
    DeadlineMonitor,
    SandboxAdmissionError,
    assess,
    sandbox_admission,
)
from agent_core.runtime.pipeline import AttemptPipeline
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import BrokerInstanceId, PepInstanceId
from agent_core.types.monitor import HeartbeatVerdict, MonitorConfig
from agent_core.types.records import AttemptState as A
from agent_core.types.safety import Consumer
from agent_core.types.sandbox import SandboxState
from agent_core_broker.fake import FakeSandbox
from support import broker_world as bw
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import privileged
from support import sandbox_world as sw

CONFIG = MonitorConfig()


class Boot:
    def __init__(self, boot_id: str = "boot-a", now: int = 10_000) -> None:
        self.boot_id, self.now, self.broken = boot_id, now, False

    def __call__(self) -> tuple[str, int]:
        if self.broken:
            raise OSError("synthetic: no boot clock")
        return self.boot_id, self.now


def _restart(tmp_path: Path, first: Any, sandbox: FakeSandbox, boot: Boot) -> Any:
    first.close()
    return bw.make_broker(tmp_path, sandbox=sandbox, boot=boot)


# ---------------------------------------------------------------- S-R02


def test_a_wall_clock_moved_back_does_not_extend_a_deadline_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, boot = FakeSandbox(), Boot()
    first = bw.make_broker(tmp_path, sandbox=sandbox, boot=boot)
    hang = bw.start_request(program="hang", deadline_ms=1_000)
    bw.make_client(first).start(hang)
    second = _restart(tmp_path, first, sandbox, boot)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 86_400)  # a day back
    boot.now += 500
    second.tick()
    assert second.record_state(hang.receipt_id) is SandboxState.ACCEPTED  # not yet
    boot.now += 600
    second.tick()
    assert second.record_state(hang.receipt_id) is SandboxState.STOPPED
    second.close()


@pytest.mark.parametrize("case", ["other_boot", "no_clock"])
def test_a_deadline_that_cannot_be_restored_stops_at_once(tmp_path: Path, case: str) -> None:
    sandbox, boot = FakeSandbox(), Boot()
    first = bw.make_broker(tmp_path, sandbox=sandbox, boot=boot)
    hang = bw.start_request(program="hang", deadline_ms=3_600_000)
    bw.make_client(first).start(hang)
    later = Boot("boot-b") if case == "other_boot" else Boot()
    later.broken = case == "no_clock"
    second = _restart(tmp_path, first, sandbox, later)
    second.tick()
    assert second.record_state(hang.receipt_id) is SandboxState.STOPPED
    second.close()


def test_without_a_boot_clock_p2_refuses_starts_before_recording(tmp_path: Path) -> None:
    boot = Boot()
    boot.broken = True
    broker = bw.make_broker(tmp_path, boot=boot)
    start = bw.start_request()
    with pytest.raises(SandboxUnavailableError) as excinfo:
        bw.make_client(broker).start(start)
    assert excinfo.value.refusal == "DEADLINE_UNAVAILABLE"
    assert broker.record_state(start.receipt_id) is SandboxState.NOT_SEEN
    broker.close()


def test_an_unstoppable_execution_after_a_restart_stays_unknown(tmp_path: Path) -> None:
    """A stop that is not confirmed after a restart: P2 records nothing as stopped;
    P1 keeps OUTCOME_UNKNOWN with the hold (Phase 1)."""
    sandbox, boot = FakeSandbox(), Boot()
    first = bw.make_broker(tmp_path, sandbox=sandbox, boot=boot)
    stubborn = bw.start_request(program="stubborn", deadline_ms=1_000)
    bw.make_client(first).start(stubborn)
    later = Boot("boot-b")
    second = _restart(tmp_path, first, sandbox, later)
    second.tick()
    assert second.record_state(stubborn.receipt_id) is SandboxState.ACCEPTED
    second.close()


# ---------------------------------------------------------------- S-R03


def _store(tmp_path: Path, st: lw.Setup) -> HeartbeatFile:
    return HeartbeatFile(tmp_path / "heartbeat", st.keys.owner)


def _verdict(store: HeartbeatFile, st: lw.Setup, monitor: DeadlineMonitor, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "owner_epoch": st.journal.owner_epoch,
        "monitor_instance": monitor.instance,
        "now_ms": st.journal.observe_time(st.clock.now_ms()),
        "config": CONFIG,
    }
    args.update(kw)
    return assess(store, **args)


def test_another_broker_instance_and_a_future_time_are_not_trusted(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    p2 = b.new_id(BrokerInstanceId)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store, p2_instance=p2)
    monitor.cycle()
    assert _verdict(store, st, monitor, p2_instance=p2) is HeartbeatVerdict.TRUSTED
    other = b.new_id(BrokerInstanceId)
    assert _verdict(store, st, monitor, p2_instance=other) is HeartbeatVerdict.OTHER_BROKER
    beat, _ = store.read()
    assert beat is not None
    store.write(beat.model_copy(update={"at_ms": beat.at_ms + 60_000}))
    assert _verdict(store, st, monitor, p2_instance=p2) is HeartbeatVerdict.FUTURE


@pytest.mark.parametrize(
    "damage",
    [
        "mode",
        # As nobody, chown to 65534 would be a no-op: this case needs root.
        pytest.param("owner", marks=privileged.CHOWN),
        "symlink",
        "garbage",
    ],
)
def test_a_record_with_the_wrong_owner_mode_or_content_is_not_trusted(
    tmp_path: Path, damage: str
) -> None:
    st = lw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)
    monitor.cycle()
    path = tmp_path / "heartbeat"
    if damage == "mode":
        path.chmod(0o644)
    elif damage == "owner":
        assert os.geteuid() != 65534, "the owner case runs as root (privileged)"
        os.chown(path, 65534, 65534)
    elif damage == "symlink":
        real = tmp_path / "real"
        path.rename(real)
        path.symlink_to(real)
    else:
        path.write_bytes(b"\x00 not a record")
        path.chmod(0o600)
    assert _verdict(store, st, monitor) is HeartbeatVerdict.UNAUTHENTIC


def test_the_record_is_private_and_replaced_atomically(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    beats = tmp_path / "beats"
    beats.mkdir()
    store = HeartbeatFile(beats / "heartbeat", st.keys.owner)
    DeadlineMonitor(iw.deps(st), CONFIG, store).cycle()
    assert [p.name for p in beats.iterdir()] == ["heartbeat"]  # no temporary file left
    assert (beats / "heartbeat").stat().st_mode & 0o777 == 0o600


def test_a_failed_write_refuses_admission_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = lw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)
    monitor.cycle()
    check = sandbox_admission(monitor, store, p2_instance=None)
    check()  # trusted
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError, match="synthetic"):
        monitor.cycle()
    monkeypatch.undo()
    with pytest.raises(SandboxAdmissionError, match="failed"):
        check()


def test_admission_refuses_the_permit_before_anything_is_written(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)  # never cycled: no record
    check = sandbox_admission(monitor, store, p2_instance=None)
    pipeline = AttemptPipeline(
        sm=st.sm,
        ledger=st.journal,
        clock=st.clock,
        pep_instance_id=b.new_id(PepInstanceId),
        new_id=b.new_id,
        admission=lambda _attempt: check(),
    )
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    reserved = lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000)
    head = st.journal.head(b.TENANT)
    with pytest.raises(SandboxAdmissionError, match="ABSENT"):
        pipeline.authorize(reserved, Consumer.BROKER)
    assert st.journal.head(b.TENANT) == head
    assert st.sm.state.attempts[reserved.attempt_id].state is A.BUDGET_RESERVED


def test_admission_refuses_the_sandbox_start(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)
    attempt = sw.consumed(st)
    done = sw.run_with(
        st, broker, attempt, admission=sandbox_admission(monitor, store, p2_instance=None)
    )
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert attempt.receipt_id not in broker.sandbox.runs
    monitor.cycle()
    attempt2 = sw.consumed(st)
    ok = sw.run_with(
        st, broker, attempt2, admission=sandbox_admission(monitor, store, p2_instance=None)
    )
    assert ok.attempt.state is A.COMPLETED
    broker.close()


# ---------------------------------------------------------------- D19: admission cannot be omitted


def test_the_sandbox_run_and_the_client_need_an_admission() -> None:
    import inspect

    from agent_core.adapters.broker_client import BrokerClient
    from agent_core.runtime.sandbox_execution import run_sandbox_operation

    for target in (run_sandbox_operation, BrokerClient.__init__):
        parameter = inspect.signature(target).parameters["admission"]
        assert parameter.default is inspect.Parameter.empty, target
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_client_refuses_a_start_before_sending_it(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)  # never cycled: no trusted beat
    client = bw.make_client(
        broker,
        epoch=st.journal.owner_epoch,
        admission=sandbox_admission(monitor, store, p2_instance=None),
    )
    with pytest.raises(SandboxUnavailableError) as refused:
        client.start(bw.start_request(epoch=st.journal.owner_epoch))
    assert refused.value.sent is False
    assert refused.value.refusal == "SANDBOX_ADMISSION_REFUSED"
    assert broker.sandbox.runs == {}
    broker.close()


def test_a_start_refused_by_the_client_is_definitely_not_run(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    calls = {"n": 0}

    def second_call_refuses() -> None:  # the run's check passes; the client's fails
        calls["n"] += 1
        if calls["n"] > 2:
            raise SandboxAdmissionError("no trusted heartbeat")

    client = bw.make_client(broker, epoch=st.journal.owner_epoch, admission=second_call_refuses)
    attempt = sw.consumed(st)
    done = sw.run_with(st, broker, attempt, client=client, admission=second_call_refuses)
    assert done.attempt.state is A.FAILED_DEFINITE
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


# ---------------------------------------------------------------- D20: the whole path with the Deadline Supervisor


def _monitored(tmp_path: Path) -> tuple[Any, Any, Any]:
    st, broker = sw.setup(tmp_path)  # the broker has the real supervisor (fake cgroups)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)
    monitor.cycle()
    admission = sandbox_admission(monitor, store, p2_instance=None)
    client = bw.make_client(broker, epoch=st.journal.owner_epoch, admission=admission)
    return st, broker, (admission, client)


def test_the_formal_path_checks_the_heartbeat_and_the_supervisor(tmp_path: Path) -> None:
    from agent_core.types.supervisor import TargetState

    st, broker, (admission, client) = _monitored(tmp_path)
    attempt = sw.consumed(st)
    done = sw.run_with(st, broker, attempt, client=client, admission=admission)
    assert done.attempt.state is A.COMPLETED
    # registered before the start, released after the reap
    assert broker.ds.view(attempt.receipt_id).state is TargetState.RELEASED
    broker.close()


def test_a_supervisor_refusal_ends_the_attempt_not_run_and_it_is_not_retried(
    tmp_path: Path,
) -> None:
    st, broker, (admission, client) = _monitored(tmp_path)
    broker.ds_cgroups.reason = "no cgroup v2"
    attempt = sw.consumed(st)
    done = sw.run_with(st, broker, attempt, client=client, admission=admission)
    assert done.attempt.state is A.FAILED_DEFINITE  # DEADLINE_UNAVAILABLE: before acceptance
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.ds_cgroups.reason = None
    with pytest.raises(Exception):  # noqa: B017,PT011 (the Receipt is used: never run again)
        sw.run_with(st, broker, attempt, client=client, admission=admission)
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


def test_without_a_trusted_heartbeat_nothing_reaches_p2_or_the_supervisor(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    store = _store(tmp_path, st)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)  # never cycled
    admission = sandbox_admission(monitor, store, p2_instance=None)
    client = bw.make_client(broker, epoch=st.journal.owner_epoch, admission=admission)
    attempt = sw.consumed(st)
    done = sw.run_with(st, broker, attempt, client=client, admission=admission)
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert broker.ds.status().active == 0
    broker.close()


def test_the_supervisor_stops_a_run_that_p1_and_p2_abandoned(tmp_path: Path) -> None:
    from agent_core.types.supervisor import TargetState

    broker = bw.make_broker(tmp_path)
    start = bw.start_request(program="hang", deadline_ms=1_000, grace_ms=0)
    assert bw.make_client(broker).start(start).status.value == "STARTED"
    # P1 and P2 do nothing more; the supervisor alone, on its boot clock
    ds = broker.ds
    for _ in range(200):  # at most 10 s; the deadline is 1 s plus the 1 s margin
        if ds.view(start.receipt_id).state is TargetState.KILLED:
            break
        ds.tick()
        time.sleep(0.05)
    assert ds.view(start.receipt_id).state is TargetState.KILLED
    assert broker.sandbox.containers[start.receipt_id].state is SandboxState.STOPPED
    broker.close()
