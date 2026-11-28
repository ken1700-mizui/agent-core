"""WP2-04: P-5 items that need no Docker (P-4 design 5, 7.4-7.7; D17).

Items here: 1 (escalation in one cycle), 2 (the monitor changes nothing
else), 4 (an owner stop leaves the attempt unknown with its hold), 5 (a child
failure is unknown, never retried), 6 (a clock moved back or a restarted
monitor does not extend a deadline), 7 (a monitor failure changes nothing more),
C (one liveness record, overwritten), F (untrusted liveness records).
The Docker items are in ``test_wp2_04_p5_docker.py``. Values are candidates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.heartbeat_file import HeartbeatFile
from agent_core.runtime.monitor import DeadlineMonitor, assess
from agent_core.types.cost import ReservationState
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.monitor import Heartbeat, HeartbeatVerdict, MonitorConfig
from agent_core.types.records import AttemptState as A
from agent_core.types.records import RunStatus as R
from support import broker_world as bw
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import sandbox_world as sw

CONFIG = MonitorConfig()


def _cancelling(st: lw.Setup) -> Any:
    run = lw.run_for_task(st)
    return st.sm.set_run_status(st.sm.state.runs[run.run_id], R.CANCELLING)


def _monitor(st: lw.Setup, tmp_path: Path) -> tuple[DeadlineMonitor, HeartbeatFile]:
    store = HeartbeatFile(tmp_path / "heartbeat", st.keys.owner)
    return DeadlineMonitor(iw.deps(st), CONFIG, store), store


def test_candidate_values_are_the_ones_approved_for_tests() -> None:
    assert (CONFIG.sandbox_limit_ms, CONFIG.sandbox_cycle_ms, CONFIG.run_cycle_ms) == (
        300_000,
        1_000,
        10_000,
    )
    assert (CONFIG.cancelling_ms, CONFIG.recovering_ms, CONFIG.stop_grace_ms) == (
        60_000,
        600_000,
        10_000,
    )


def test_item1_an_overdue_run_escalates_within_one_cycle(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run = _cancelling(st)
    monitor, _ = _monitor(st, tmp_path)
    assert monitor.cycle().escalated == []
    st.clock.now += CONFIG.cancelling_ms + 1
    report = monitor.cycle()
    assert report.escalated == [run.run_id]
    assert st.sm.state.runs[run.run_id].status is R.RECONCILIATION_REQUIRED


def test_item2_the_monitor_changes_nothing_else(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    attempt = lw.issued(st, lw.step_in(st))  # a held reservation and an issued Permit
    run_id = st.sm.state.steps[attempt.step_id].run_id
    st.sm.set_run_status(st.sm.state.runs[run_id], R.CANCELLING)
    before = st.journal.reservation(lw.rsv(attempt))
    attempts = dict(st.sm.state.attempts)
    monitor, _ = _monitor(st, tmp_path)
    st.clock.now += CONFIG.cancelling_ms + 1
    monitor.cycle()
    assert st.journal.reservation(lw.rsv(attempt)) == before  # no release, no settlement
    assert dict(st.sm.state.attempts) == attempts  # no result, no retry
    assert st.sm.state.runs[run_id].status is R.RECONCILIATION_REQUIRED  # the only change


def test_item6_a_clock_moved_back_or_a_new_monitor_does_not_extend_a_deadline(
    tmp_path: Path,
) -> None:
    st = lw.setup(tmp_path)
    run = _cancelling(st)
    first, _ = _monitor(st, tmp_path)
    st.clock.now += CONFIG.cancelling_ms - 1_000
    first.cycle()  # the ledger's time is now at this point
    st.clock.now -= 10 * CONFIG.cancelling_ms  # the wall clock goes back
    second, _ = _monitor(st, tmp_path)  # and the monitor restarts
    st.clock.now += 10 * CONFIG.cancelling_ms + 2_000
    assert second.cycle().escalated == [run.run_id]


def test_item7_a_failing_cycle_records_nothing_more(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    run = _cancelling(st)

    class Broken:
        def write(self, beat: Heartbeat) -> None:
            raise OSError("synthetic: the liveness record cannot be written")

        def read(self) -> tuple[Heartbeat | None, bool]:
            return None, True

    monitor = DeadlineMonitor(iw.deps(st), CONFIG, Broken())
    st.clock.now += CONFIG.cancelling_ms + 1
    head = st.journal.head(b.TENANT)
    with pytest.raises(OSError, match="liveness"):
        monitor.cycle()
    after = st.journal.head(b.TENANT)
    assert after is not None
    assert head is not None
    assert after.tenant_seq == head.tenant_seq + 1  # the escalation only
    assert st.sm.state.runs[run.run_id].status is R.RECONCILIATION_REQUIRED


def test_item_c_one_liveness_record_overwritten(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    beats = tmp_path / "beats"
    beats.mkdir()
    store = HeartbeatFile(beats / "heartbeat", st.keys.owner)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)
    for _ in range(5):
        monitor.cycle()
    assert [p.name for p in beats.iterdir()] == ["heartbeat"]
    beat, authentic = store.read()
    assert authentic
    assert beat is not None
    assert beat.cycle == 5


def test_item_f_liveness_records_that_are_not_trusted(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    store = HeartbeatFile(tmp_path / "heartbeat", st.keys.owner)
    monitor = DeadlineMonitor(iw.deps(st), CONFIG, store)

    def verdict(**overrides: Any) -> HeartbeatVerdict:
        args: dict[str, Any] = {
            "owner_epoch": st.journal.owner_epoch,
            "monitor_instance": monitor.instance,
            "now_ms": st.journal.observe_time(st.clock.now_ms()),
            "config": CONFIG,
        }
        args.update(overrides)
        return assess(store, **args)

    assert verdict() is HeartbeatVerdict.ABSENT
    monitor.cycle()
    assert verdict() is HeartbeatVerdict.TRUSTED
    assert verdict(owner_epoch=st.journal.owner_epoch + 1) is HeartbeatVerdict.OLDER_EPOCH
    assert verdict(monitor_instance="0" * 32) is HeartbeatVerdict.OTHER_MONITOR
    stale = st.journal.observe_time(st.clock.now_ms()) + CONFIG.run_cycle_ms * 3 + 1
    assert verdict(now_ms=stale) is HeartbeatVerdict.STALE
    data = (tmp_path / "heartbeat").read_bytes().replace(b'"cycle\\":1', b'"cycle\\":9')
    (tmp_path / "heartbeat").write_bytes(data)
    assert verdict() is HeartbeatVerdict.UNAUTHENTIC


def test_item4_an_owner_stop_leaves_the_attempt_unknown_with_its_hold(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="hang")
    with pytest.raises(SandboxUnavailableError, match="owner"):
        sw.run_with(st, broker, attempt, stopped=lambda: True)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is ReservationState.UNKNOWN
    assert broker.record_state(sw.receipt_of(attempt)).value == "STOPPED"  # P2 was told to stop
    broker.close()


def test_item5_a_child_failure_after_the_start_is_unknown_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    original = broker.sandbox.start

    def crash(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("synthetic: the child died after it started")

    monkeypatch.setattr(broker.sandbox, "start", crash)
    with pytest.raises(SandboxUnavailableError):
        sw.run(st, broker, attempt)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    assert broker.sandbox.runs[attempt.receipt_id] == 1
    broker.close()


def test_p1_waits_no_longer_than_its_own_limit(tmp_path: Path) -> None:
    """With P2's deadline not acting (fake), P1 stops at deadline + grace + margin."""
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="hang", deadline_ms=1_000, grace_ms=0)
    now = {"ms": 0}

    def tick() -> None:
        now["ms"] += 500  # one sandbox cycle

    done = sw.run_with(
        st,
        broker,
        attempt,
        wait=tick,
        monotonic_ms=lambda: now["ms"],
        max_polls=10_000,
        p1_margin_ms=1_000,
    )
    assert done.attempt.state is A.FAILED_DEFINITE  # a contained, confirmed stop
    assert now["ms"] <= 1_000 + 0 + 1_000 + 500
    broker.close()


def test_the_stop_of_p2_uses_the_programs_grace(tmp_path: Path) -> None:
    clock = bw.ManualMonotonic()
    broker = bw.make_broker(tmp_path, clock=clock)
    hang = bw.start_request(program="hang", deadline_ms=1_000, grace_ms=7_000)
    graces: list[int] = []
    original = broker.sandbox.stop

    def stop(receipt: Any, grace: int) -> bool:
        graces.append(grace)
        return bool(original(receipt, grace))

    broker.sandbox.stop = stop
    bw.make_client(broker).start(hang)
    clock.now += 1_001
    broker.tick()
    assert graces == [7_000]
    broker.close()
