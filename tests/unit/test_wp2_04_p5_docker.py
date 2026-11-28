"""WP2-04: P-5 items that need Docker (``sandbox`` category; development checks
only: this environment has no AppArmor or SELinux). P2 runs as ``agentcore-p2``.

Items: 3 (a program ignoring SIGTERM stops after the grace), 8/H/A (with P1
gone, P2's deadline stops the container), 9 (after a restart, leftovers and
their temporary data are reaped), E (P2 down: the attempt's outcome is unknown;
the container is **not** stopped by P2 while it is down; after a restart P2
stops it), B (the code inside can stop the in-container supervisor: a check,
never a protection basis), and the measurement with P1 and P2 both down.

Each measurement says what it shows. "Design guarantee" and "measured here" are
kept apart in ``docs/design/phase-2/WP2-04_P5.md``.
"""

from __future__ import annotations

import signal
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import SandboxState
from agent_core_broker.docker_api import DockerAPI, DockerError
from agent_core_broker.docker_backend import _hex, program_ref
from support import broker_world as bw
from support import builders as b
from support import docker_world as dw
from support import os_world as ow

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    dw.remove_managed_containers()
    yield
    dw.remove_managed_containers()


def _start(name: str, deadline_ms: int, grace_ms: int = 1_000) -> Any:
    return bw.start_request().model_copy(
        update={
            "program_ref": program_ref(name),
            "sandbox_profile_hash": dw.PROFILE.profile_hash(),
            "deadline_ms": deadline_ms,
            "grace_ms": grace_ms,
        }
    )


def _running(receipt: ReceiptId) -> bool | None:
    try:
        found = DockerAPI().call("GET", f"/containers/acsbx-{_hex(receipt)}/json")
    except DockerError as exc:
        if exc.status == 404:
            return None  # removed
        raise
    return bool(found["State"]["Running"])


def _wait_not_running(receipt: ReceiptId, seconds: float) -> float:
    began = time.monotonic()
    while time.monotonic() - began < seconds:
        if not _running(receipt):
            return time.monotonic() - began
        time.sleep(0.2)
    return -1.0


def test_item3_sigterm_is_ignored_then_killed_after_the_grace(tmp_path: Path) -> None:
    clock = bw.ManualMonotonic()
    p2 = dw.broker(tmp_path, clock=clock)
    start = _start("diag.ignore_term", 1_000, grace_ms=1_000)
    bw.make_client(p2).start(start)
    time.sleep(0.5)
    clock.now += 1_001
    began = time.monotonic()
    p2.tick()
    assert p2.record_state(start.receipt_id) is SandboxState.STOPPED
    assert time.monotonic() - began < 15
    assert _running(start.receipt_id) is False
    p2.close()


def test_items_8_h_a_with_p1_gone_p2s_deadline_stops_the_container() -> None:
    with ow._world() as world, ow._p2(world, docker=True):
        start = _start("diag.sleep", 3_000)
        # P1 is a child process that ends right after the start (as if killed).
        assert ow._client(world, "agentcore-p1", ow._start_json(start))["status"] == "STARTED"
        assert _running(start.receipt_id) is True
        took = _wait_not_running(start.receipt_id, 3 + 1 + 1 + 10)
        assert took >= 0, "P2 did not stop the container"


def test_item_e_p2_down_the_container_keeps_running_until_p2_is_back() -> None:
    with ow._world() as world:
        start = _start("diag.sleep", 4_000)
        with ow._p2(world, docker=True) as p2:
            assert ow._client(world, "agentcore-p1", ow._start_json(start))["status"] == "STARTED"
            p2.send_signal(signal.SIGKILL)
            p2.wait(timeout=30)
        time.sleep(5)  # past the deadline; P2 is down
        assert _running(start.receipt_id) is True  # measured: nothing stopped it
        with ow._p2(world, docker=True):  # P2 back: adopts and enforces the deadline
            took = _wait_not_running(start.receipt_id, 15)
            assert took >= 0


def test_both_down_only_the_supervisor_stops_a_cooperative_program() -> None:
    with ow._world() as world:
        start = _start("diag.sleep", 2_000)
        with ow._p2(world, docker=True) as p2:
            assert ow._client(world, "agentcore-p1", ow._start_json(start))["status"] == "STARTED"
            p2.send_signal(signal.SIGKILL)
            p2.wait(timeout=30)
        took = _wait_not_running(start.receipt_id, 2 + 5 + 10)  # deadline + supervisor margin
        assert took >= 0  # measured for a fixed program that leaves its supervisor alone


def test_item_b_code_inside_can_stop_the_supervisor() -> None:
    """With P2 down too, nothing stops a program that killed its supervisor:
    the supervisor is not a protection basis (measured)."""
    with ow._world() as world:
        start = _start("diag.kill_supervisor", 2_000)
        with ow._p2(world, docker=True) as p2:
            assert ow._client(world, "agentcore-p1", ow._start_json(start))["status"] == "STARTED"
            p2.send_signal(signal.SIGKILL)
            p2.wait(timeout=30)
        time.sleep(2 + 5 + 3)
        assert _running(start.receipt_id) is True  # still running: no guarantee without P2


def test_item9_leftovers_and_their_data_are_reaped_after_a_restart(tmp_path: Path) -> None:
    first = dw.broker(tmp_path / "p2")
    done = _start("diag.exit0", 30_000)
    bw.make_client(first).start(done)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("synthetic")
    staged_receipt = b.new_id(ReceiptId)
    staged = first.sandbox.stage_input(staged_receipt, source)
    assert _wait_not_running(done.receipt_id, 15) >= 0  # it ends by itself
    first.close()  # P2 crashes before recording or reaping it
    assert _running(done.receipt_id) is False  # exited, not removed
    second = dw.broker(tmp_path / "p2")
    actions = second.recover_backend()
    assert any("reaped" in a for a in actions)
    assert _running(done.receipt_id) is None  # removed
    second.sandbox.reap(staged_receipt)
    assert not staged.exists()
    second.close()
