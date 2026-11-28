"""S-R01 (D20): the Deadline Supervisor's contract, with fake cgroups.

These are contract tests in this dev environment (no cgroup v2, no systemd):
passing them is NOT the safety condition. The six acceptance criteria of
``DEADLINE_SUPERVISOR_DESIGN.md`` section 8 hold only after they are checked
on a conforming host (E-6); here each is exercised with a fake cgroup port.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import AttemptId, BrokerInstanceId, ReceiptId, RequestId
from agent_core.types.sandbox import ReapRequest
from agent_core.types.supervisor import DsHealth, DsRefusal, Registration, TargetState
from agent_core_broker.fake import FakeSandbox
from agent_core_supervisor.cgroups import CgroupError, CgroupV2, mounted_cgroup2
from agent_core_supervisor.client import DsUnavailableError, SupervisorClient
from agent_core_supervisor.server import SupervisorServer
from agent_core_supervisor.supervisor import DsRefusedError, Supervisor
from support import broker_world as bw
from support import builders as b
from support import supervisor_world as dsw


class Boot:
    def __init__(self) -> None:
        self.boot_id, self.ms, self.broken = "boot-a", 1_000_000, False

    def __call__(self) -> tuple[str, int]:
        if self.broken:
            raise OSError("no boot clock")
        return self.boot_id, self.ms


def _reg(receipt: ReceiptId | None = None, *, deadline_ms: int = 5_000) -> Registration:
    receipt = receipt if receipt is not None else b.new_id(ReceiptId)
    return Registration(
        receipt_id=receipt,
        attempt_id=b.new_id(AttemptId),
        target=f"agentcore-{receipt}",
        deadline_ms=deadline_ms,
        grace_ms=1_000,
        p2_instance=b.new_id(BrokerInstanceId),
    )


def _ds(tmp_path: Path, **kw: Any) -> tuple[Supervisor, dsw.SandboxCgroups, FakeSandbox, Boot]:
    sandbox = FakeSandbox()
    cgroups = dsw.SandboxCgroups(sandbox)
    boot = Boot()
    return dsw.supervisor(tmp_path / "ds", cgroups, boot=boot, **kw), cgroups, sandbox, boot


def _run(sandbox: FakeSandbox, reg: Registration, program: str = "hang") -> None:
    from agent_core_broker.fake import program_ref

    sandbox.start(reg.receipt_id, program_ref(program), reg.deadline_ms)


def _started(tmp_path: Path, program: str = "hang") -> tuple[Any, ...]:
    ds, cg, sandbox, boot = _ds(tmp_path)
    reg = _reg()
    ds.register(reg)
    ds.confirm_start(reg.receipt_id)
    _run(sandbox, reg, program)
    assert ds.started(reg.receipt_id).state is TargetState.ATTACHED
    return ds, cg, sandbox, boot, reg


# ---------------------------------------------------------------- criterion 1: P1 and P2 gone


@pytest.mark.parametrize("program", ["hang", "stubborn"])
def test_the_deadline_is_enforced_without_p1_or_p2(tmp_path: Path, program: str) -> None:
    ds, _, sandbox, boot, reg = _started(tmp_path, program)  # nobody calls P2 again
    boot.ms += 5_000
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.ATTACHED  # not yet (grace + margin)
    boot.ms += 2_001
    ds.tick()
    view = ds.view(reg.receipt_id)
    assert (view.stop_requested, view.stop_confirmed) == (True, False)  # a request is not a stop
    ds.tick()
    view = ds.view(reg.receipt_id)
    assert (view.state, view.stop_confirmed) == (TargetState.KILLED, True)
    assert sandbox.containers[reg.receipt_id].state.value == "STOPPED"  # even "stubborn"


# ---------------------------------------------------------------- criterion 2: DS restart


def test_after_a_restart_the_deadlines_come_back_from_the_record(tmp_path: Path) -> None:
    ds, cg, _, boot, reg = _started(tmp_path)
    ds.close()  # the supervisor is gone (SIGKILL in the service)
    boot.ms += 10_000
    again = dsw.supervisor(tmp_path / "ds", cg, boot=boot)
    assert again.view(reg.receipt_id).state is TargetState.ATTACHED
    again.tick()
    again.tick()
    assert again.view(reg.receipt_id).state is TargetState.KILLED


def test_while_the_supervisor_is_down_p2_refuses_starts(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    broker.ds.close()  # type: ignore[attr-defined]
    with pytest.raises(SandboxUnavailableError) as refused:
        bw.make_client(broker).start(bw.start_request())
    assert refused.value.refusal == "DEADLINE_UNAVAILABLE"
    assert broker.sandbox.runs == {}
    broker.close()


# ---------------------------------------------------------------- criterion 3: clocks


def test_the_wall_clock_does_not_matter_and_a_new_boot_stops_at_once(tmp_path: Path) -> None:
    ds, _, _, boot, reg = _started(tmp_path)
    boot.boot_id = "boot-b"  # the deadline cannot be restored on another boot
    ds.tick()
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.KILLED


def test_without_a_boot_clock_nothing_registers_and_running_work_is_stopped(
    tmp_path: Path,
) -> None:
    ds, _, _, boot, reg = _started(tmp_path)
    boot.broken = True
    assert ds.status().health is DsHealth.UNAVAILABLE
    with pytest.raises(DsRefusedError) as refused:
        ds.register(_reg())
    assert refused.value.code is DsRefusal.UNAVAILABLE
    ds.tick()
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.KILLED


# ---------------------------------------------------------------- criterion 4: scope


def test_unregistered_targets_are_never_touched_in_normal_operation(tmp_path: Path) -> None:
    ds, cg, _, boot, _ = _started(tmp_path)
    cg.extra["agentcore-someone-else"] = True
    boot.ms += 60_000
    ds.tick()
    ds.tick()
    assert "agentcore-someone-else" not in cg.kills


def test_a_target_name_cannot_leave_the_delegated_root(tmp_path: Path) -> None:
    from pydantic import ValidationError

    for bad in ("../x", "agentcore-a/../../b", "other", "/sys/fs/cgroup"):
        with pytest.raises(ValidationError):
            _reg().model_copy(update={}).__class__(**{**_reg().model_dump(), "target": bad})
    port = CgroupV2(tmp_path, is_cgroup2=lambda _: True)
    with pytest.raises(CgroupError):
        port.kill("../outside")


def test_the_supervisor_has_no_approval_permit_cost_or_retry_path() -> None:
    import importlib

    modules = [
        "agent_core_supervisor.supervisor",
        "agent_core_supervisor.server",
        "agent_core_supervisor.client",
        "agent_core_supervisor.cgroups",
        "agent_core_supervisor.record",
    ]
    for name in modules:
        source = Path(importlib.import_module(name).__file__ or "").read_text()
        for forbidden in (
            "agent_core.runtime",
            "agent_core.adapters",
            "agent_core_broker",
            "ledger",
        ):
            assert forbidden not in source, (name, forbidden)


# ---------------------------------------------------------------- criterion 5: a broken record


def test_a_broken_record_is_fail_safe(tmp_path: Path) -> None:
    ds, cg, _, _, reg = _started(tmp_path)
    ds.close()
    log = tmp_path / "ds" / "deadline-supervisor.log"
    log.write_bytes(log.read_bytes().replace(b"agentcore", b"agentcorx", 1))
    cg.extra["agentcore-unlisted"] = True
    broken = dsw.supervisor(tmp_path / "ds", cg, boot=Boot())
    assert broken.status().health is DsHealth.FAIL_SAFE
    # FAIL_SAFE: every sandbox-named cgroup under the delegated root is killed
    assert f"agentcore-{reg.receipt_id}" in cg.kills
    assert "agentcore-unlisted" in cg.kills
    with pytest.raises(DsRefusedError) as refused:
        broken.register(_reg())
    assert refused.value.code is DsRefusal.FAIL_SAFE


def test_a_torn_last_line_is_fail_safe(tmp_path: Path) -> None:
    ds, cg, _, _, _ = _started(tmp_path)
    ds.close()
    log = tmp_path / "ds" / "deadline-supervisor.log"
    log.write_bytes(log.read_bytes()[:-5])
    assert dsw.supervisor(tmp_path / "ds", cg).status().health is DsHealth.FAIL_SAFE


# ---------------------------------------------------------------- criterion 6: unconfirmed stops


def test_an_unconfirmed_kill_refuses_new_work_and_keeps_trying(tmp_path: Path) -> None:
    ds, cg, _, boot, reg = _started(tmp_path)
    cg.resist.add(f"agentcore-{reg.receipt_id}")
    boot.ms += 7_001
    ds.tick()  # KILL_REQUESTED
    boot.ms += 2_000
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.KILL_UNCONFIRMED
    assert ds.status().health is DsHealth.UNCONFIRMED_STOP
    with pytest.raises(DsRefusedError):
        ds.register(_reg())
    cg.resist.clear()
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.KILLED
    assert ds.status().health is DsHealth.OK


# ---------------------------------------------------------------- the start contract


def test_a_start_window_that_passed_before_the_start_is_refused(tmp_path: Path) -> None:
    ds, _, _, boot = _ds(tmp_path)
    reg = _reg()
    ds.register(reg)
    boot.ms += 10_001
    with pytest.raises(DsRefusedError) as refused:
        ds.confirm_start(reg.receipt_id)
    assert refused.value.code is DsRefusal.EXPIRED_BEFORE_START
    assert ds.view(reg.receipt_id).state is TargetState.NOT_RUNNING


def test_a_target_that_already_exists_is_a_mismatch(tmp_path: Path) -> None:
    ds, cg, sandbox, _ = _ds(tmp_path)
    reg = _reg()
    cg.extra[reg.target] = True
    with pytest.raises(DsRefusedError) as refused:
        ds.register(reg)
    assert refused.value.code is DsRefusal.STATE_MISMATCH
    del cg.extra[reg.target]
    ds.register(reg)
    _run(sandbox, reg)  # started without a confirmed start
    with pytest.raises(DsRefusedError):
        ds.confirm_start(reg.receipt_id)
    ds.tick()  # a mismatch is killed at once (it is registered, so in scope)
    ds.tick()
    assert ds.view(reg.receipt_id).state is TargetState.KILLED


def test_duplicates_and_release(tmp_path: Path) -> None:
    ds, _, sandbox, _, reg = _started(tmp_path)
    with pytest.raises(DsRefusedError):
        ds.register(reg)
    with pytest.raises(DsRefusedError) as refused:
        ds.release(reg.receipt_id)
    assert refused.value.code is DsRefusal.STILL_RUNNING
    sandbox.stop(reg.receipt_id, 0)
    assert ds.release(reg.receipt_id).state is TargetState.RELEASED


# ---------------------------------------------------------------- the broker path


def test_the_broker_registers_confirms_and_releases(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    start = bw.start_request(program="hang")
    assert client.start(start).status.value == "STARTED"
    ds = broker.ds  # type: ignore[attr-defined]
    assert ds.view(start.receipt_id).state is TargetState.ATTACHED
    broker.sandbox.stop(start.receipt_id, 0)
    client.reap(
        ReapRequest(
            request_id=b.new_id(RequestId),
            attempt_id=start.attempt_id,
            receipt_id=start.receipt_id,
        )
    )
    assert ds.view(start.receipt_id).state is TargetState.RELEASED
    broker.close()


def test_a_broker_without_a_supervisor_refuses_every_start(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path, supervisor=None)
    with pytest.raises(SandboxUnavailableError) as refused:
        bw.make_client(broker).start(bw.start_request())
    assert refused.value.refusal == "DEADLINE_UNAVAILABLE"
    assert broker.sandbox.runs == {}
    broker.close()


def test_a_supervisor_refusal_is_not_run(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    broker.ds_cgroups.reason = "no cgroup v2"  # type: ignore[attr-defined]
    with pytest.raises(SandboxUnavailableError) as refused:
        bw.make_client(broker).start(bw.start_request())
    assert (refused.value.refusal, refused.value.sent) == ("DEADLINE_UNAVAILABLE", True)
    assert broker.sandbox.runs == {}
    broker.close()


# ---------------------------------------------------------------- the real cgroup v2 port


def test_cgroup_v2_files(tmp_path: Path) -> None:
    root = dsw.DirCgroups(tmp_path / "root")
    port = CgroupV2(root.root, is_cgroup2=lambda _: True)
    root.make("agentcore-a", populated=True)
    root.make("agentcore-b", populated=False)
    (root.root / "unrelated").mkdir()
    os.symlink(root.root / "agentcore-a", root.root / "agentcore-link")
    assert port.unavailable() is None
    assert port.targets() == ("agentcore-a", "agentcore-b")
    assert (port.populated("agentcore-a"), port.populated("agentcore-b")) == (True, False)
    port.kill("agentcore-a")
    assert root.killed("agentcore-a")
    with pytest.raises(CgroupError):
        port.exists("agentcore-link")


def test_a_directory_that_is_not_cgroup_v2_is_unavailable(tmp_path: Path) -> None:
    assert not mounted_cgroup2(tmp_path)
    assert CgroupV2(tmp_path).unavailable() == "the delegated root is not on cgroup v2"


def test_the_service_refuses_to_run_without_systemd_and_cgroup_v2(tmp_path: Path) -> None:
    key = tmp_path / "k"
    key.write_text("00" * 32)
    key.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if k != "INVOCATION_ID"}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_core_supervisor",
            "serve",
            "--socket",
            str(tmp_path / "ds.sock"),
            "--record",
            str(tmp_path / "ds.log"),
            "--key-file",
            str(key),
            "--cgroup-root",
            str(tmp_path),
            "--p2-uid",
            "0",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 2
    assert "refused" in done.stderr
    assert not (tmp_path / "ds.sock").exists()


# ---------------------------------------------------------------- the socket


def _pair(server: SupervisorServer) -> Any:
    def connect() -> socket.socket:
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        threading.Thread(target=server.serve_connection, args=(theirs,), daemon=True).start()
        return ours

    return connect


def test_the_socket_needs_p2_and_the_key(tmp_path: Path) -> None:
    ds, _, _, _ = _ds(tmp_path)
    me = os.getuid()
    good = SupervisorClient(
        connector=_pair(SupervisorServer(ds, key=dsw.DS_KEY, p2_uid=me)), key=dsw.DS_KEY, ds_uid=me
    )
    assert good.status().health is DsHealth.OK
    reg = _reg()
    assert good.register(reg).state is TargetState.REGISTERED
    other_peer = SupervisorServer(ds, key=dsw.DS_KEY, p2_uid=me + 1)
    with pytest.raises(DsRefusedError) as refused:
        SupervisorClient(connector=_pair(other_peer), key=dsw.DS_KEY, ds_uid=me).status()
    assert refused.value.code is DsRefusal.PEER
    wrong_key = SupervisorClient(
        connector=_pair(SupervisorServer(ds, key=dsw.DS_KEY, p2_uid=me)),
        key=b"another key that is long enough!",
        ds_uid=me,
    )
    with pytest.raises((DsUnavailableError, DsRefusedError)):
        wrong_key.status()
    with pytest.raises(DsUnavailableError):
        SupervisorClient(
            connector=_pair(SupervisorServer(ds, key=dsw.DS_KEY, p2_uid=me)),
            key=dsw.DS_KEY,
            ds_uid=me + 1,
        ).status()
