"""WP2-03: the Docker sandbox in P2 (``sandbox`` category: E-1 to E-4, D17).

Runs only the project's fixed diagnostic programs, through the Phase 1 gate
path and P2 (P1 never talks to Docker). This environment has no AppArmor or
SELinux: these results are **development checks only**; they are not a safety
pass and not SG-1.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import ReceiptId
from agent_core.types.records import AttemptState as A
from agent_core.types.sandbox import Containment, SandboxState
from agent_core_broker.docker_api import DockerError
from agent_core_broker.docker_backend import program_ref
from support import broker_world as bw
from support import builders as b
from support import docker_world as dw
from support import sandbox_world as sw

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _no_leftover_containers() -> Iterator[None]:
    dw.remove_managed_containers()
    yield
    dw.remove_managed_containers()


def _setup(tmp_path: Path) -> tuple[object, object]:
    st, _fake = sw.setup(tmp_path)
    _fake.close()
    return st, dw.broker(tmp_path / "p2d")


# ---------------------------------------------------------------- self-diagnosis


def test_self_diagnosis_has_nine_checks_and_no_mac_here(tmp_path: Path) -> None:
    diagnosis = dw.sandbox(tmp_path).self_diagnose()
    assert len(diagnosis.checks) == 9
    failing = {k for k, v in diagnosis.checks.items() if not v}
    assert failing <= {"mac"}  # this environment: no AppArmor or SELinux
    assert diagnosis.fixed_programs_allowed is (failing <= {"mac"})
    assert not diagnosis.untrusted_allowed  # never offered without a MAC


def test_a_failed_self_diagnosis_refuses_every_start(tmp_path: Path) -> None:
    backend = dw.sandbox(tmp_path, diagnose=False)  # not diagnosed: not ready
    st, _ = _setup(tmp_path)
    p2 = dw.broker(tmp_path / "p2x", backend=backend)
    attempt = dw.consumed(st, "diag.exit0")  # type: ignore[arg-type]
    done = dw.run(st, p2, attempt)  # type: ignore[arg-type]
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH  # refused at prepare
    assert not backend._ids  # no container was made


# ---------------------------------------------------------------- the gate path with Docker


def test_a_fixed_program_runs_through_the_gate_and_p2(tmp_path: Path) -> None:
    st, p2 = _setup(tmp_path)
    attempt = dw.consumed(st, "diag.exit0")  # type: ignore[arg-type]
    done = dw.run(st, p2, attempt)  # type: ignore[arg-type]
    assert done.attempt.state is A.COMPLETED
    assert done.reap_error is None
    p2.close()  # type: ignore[attr-defined]


def test_a_failing_fixed_program_is_a_definite_failure(tmp_path: Path) -> None:
    st, p2 = _setup(tmp_path)
    done = dw.run(st, p2, dw.consumed(st, "diag.exit1"))  # type: ignore[arg-type]
    assert done.attempt.state is A.FAILED_DEFINITE
    p2.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("diag.identity", b"uid=65534 gid=65534"),
        ("diag.network", b"no-network"),
        ("diag.rootfs", b"read-only"),
        ("diag.tmpfs_limit", b"limited="),
        ("diag.tmpfs_noexec", b"noexec"),
        ("diag.pids_limit", b"pids.max=64"),
        ("diag.memory_limit", b"memory.limit=268435456"),
        ("diag.status", b"CapEff:\t0000000000000000\nNoNewPrivs:\t1\nSeccomp:\t2"),
    ],
)
def test_the_isolation_checks_hold_inside_the_container(
    tmp_path: Path, name: str, expected: bytes
) -> None:
    backend = dw.sandbox(tmp_path)
    receipt = b.new_id(ReceiptId)
    assert backend.start(receipt, program_ref(name), 30_000) is Containment.CONTAINED
    deadline = time.monotonic() + 40
    seen = backend.observe(receipt)
    while seen is not None and seen.state is SandboxState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.2)
        seen = backend.observe(receipt)
    assert seen is not None
    assert seen.state is SandboxState.FINISHED
    assert seen.exit_code == 0, backend.outputs[receipt]
    assert expected in backend.outputs[receipt]
    backend.reap(receipt)


# ---------------------------------------------------------------- per-container settings


@pytest.mark.parametrize(
    "override",
    [
        {"NetworkMode": "bridge"},
        {"ReadonlyRootfs": False},
        {"CapDrop": []},
        {"Privileged": True},
        {"PidsLimit": 4096},
        {"Memory": 0, "MemorySwap": 0},
        {"SecurityOpt": []},
    ],
)
def test_a_container_that_differs_from_the_profile_never_starts(
    tmp_path: Path, override: dict[str, object]
) -> None:
    backend = dw.sandbox(tmp_path)
    receipt = b.new_id(ReceiptId)
    # Refused by the per-container check, or already by the daemon (Privileged
    # with userns-remap): either way nothing starts.
    with pytest.raises(DockerError):
        backend.start(receipt, program_ref("diag.exit0"), 30_000, overrides=override)
    assert dw.remove_managed_containers() == 0  # removed before any start
    assert backend.observe(receipt) is None


def test_only_fixed_programs_are_known(tmp_path: Path) -> None:
    backend = dw.sandbox(tmp_path)
    assert not backend.knows_program(b.digest("any other program"))
    assert not backend.knows_program(bw.fake_ref("exit0"))


# ---------------------------------------------------------------- stop, deadline, supervisor


def test_a_stop_is_confirmed_and_a_contained_stop_is_definite(tmp_path: Path) -> None:
    st, p2 = _setup(tmp_path)
    done = dw.run(st, p2, dw.consumed(st, "diag.sleep"), max_polls=2)  # type: ignore[arg-type]
    assert done.attempt.state is A.FAILED_DEFINITE
    p2.close()  # type: ignore[attr-defined]


def test_a_program_ignoring_sigterm_is_killed_after_the_grace(tmp_path: Path) -> None:
    backend = dw.sandbox(tmp_path)
    receipt = b.new_id(ReceiptId)
    backend.start(receipt, program_ref("diag.ignore_term"), 60_000)
    time.sleep(0.5)
    began = time.monotonic()
    assert backend.stop(receipt, 1_000)
    assert time.monotonic() - began < 15
    seen = backend.observe(receipt)
    assert seen is not None
    assert seen.state is SandboxState.STOPPED
    backend.reap(receipt)


def test_p2_stops_a_container_past_its_deadline(tmp_path: Path) -> None:
    clock = bw.ManualMonotonic()
    p2 = dw.broker(tmp_path / "p2", clock=clock)
    start = bw.start_request(program="hang")
    start = start.model_copy(
        update={
            "program_ref": program_ref("diag.sleep"),
            "sandbox_profile_hash": dw.PROFILE.profile_hash(),
            "deadline_ms": 2_000,
        }
    )
    bw.make_client(p2).start(start)
    clock.now += 2_001
    p2.tick()
    assert p2.record_state(start.receipt_id) is SandboxState.STOPPED
    p2.close()


def test_the_in_container_supervisor_kills_at_its_own_limit(tmp_path: Path) -> None:
    """Auxiliary only (not a protection basis): with no P2 tick at all, the
    ``timeout -s KILL`` wrapper ends the program at its deadline."""
    backend = dw.sandbox(tmp_path)
    receipt = b.new_id(ReceiptId)
    backend.start(receipt, program_ref("diag.sleep"), 1_000)
    deadline = time.monotonic() + 20
    seen = backend.observe(receipt)
    while seen is not None and seen.state is SandboxState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.2)
        seen = backend.observe(receipt)
    assert seen is not None
    assert seen.state is SandboxState.FINISHED
    assert seen.exit_code == 137  # SIGKILL from the supervisor
    backend.reap(receipt)


# ---------------------------------------------------------------- input staging


def test_input_is_copied_safely_and_mounted_read_only(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("synthetic input")
    backend = dw.sandbox(tmp_path)
    receipt = b.new_id(ReceiptId)
    staged = backend.stage_input(receipt, source)
    backend.start(receipt, program_ref("diag.input"), 30_000, input_dir=staged)
    deadline = time.monotonic() + 30
    seen = backend.observe(receipt)
    while seen is not None and seen.state is SandboxState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.2)
        seen = backend.observe(receipt)
    assert seen is not None
    assert seen.exit_code == 0, backend.outputs[receipt]
    assert b"synthetic input" in backend.outputs[receipt]
    assert b"IN_WRITABLE" not in backend.outputs[receipt]
    backend.reap(receipt)


@pytest.mark.parametrize("bad", ["symlink", "fifo", "large"])
def test_unsafe_input_is_refused(tmp_path: Path, bad: str) -> None:
    import os

    source = tmp_path / "src"
    source.mkdir()
    if bad == "symlink":
        (source / "link").symlink_to("/etc/passwd")
    elif bad == "fifo":
        os.mkfifo(source / "pipe")
    else:
        (source / "big").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match=r"regular file|too large"):
        dw.sandbox(tmp_path).stage_input(b.new_id(ReceiptId), source)


# ---------------------------------------------------------------- crash recovery


def test_after_a_p2_crash_leftovers_are_adopted_and_deadlines_still_apply(tmp_path: Path) -> None:
    first = dw.broker(tmp_path / "p2")
    sleeping = bw.start_request().model_copy(
        update={
            "program_ref": program_ref("diag.sleep"),
            "sandbox_profile_hash": dw.PROFILE.profile_hash(),
            "deadline_ms": 1_500,
        }
    )
    finished = bw.start_request().model_copy(
        update={
            "program_ref": program_ref("diag.exit0"),
            "sandbox_profile_hash": dw.PROFILE.profile_hash(),
        }
    )
    client = bw.make_client(first)
    client.start(sleeping)
    client.start(finished)
    time.sleep(1.0)
    first.close()  # P2 "crashes": nothing is stopped or removed
    second = dw.broker(tmp_path / "p2")
    actions = second.recover_backend()
    assert any("kept" in a for a in actions)
    time.sleep(1.0)
    second.tick()  # the deadline (wall clock from the record) has passed
    assert second.record_state(sleeping.receipt_id) is SandboxState.STOPPED
    second.close()


def test_p1_cannot_reach_docker_through_the_broker_client_module() -> None:
    import agent_core.adapters.broker_client as client

    text = Path(client.__file__).read_text()
    assert "docker" not in text.lower()
    with pytest.raises(SandboxUnavailableError):
        raise SandboxUnavailableError("placeholder for the OS test", sent=False)
