"""WP2-02 (run with E-2, D17): P1 and P2 as separate OS users (``sandbox`` category).

P2 runs as ``agentcore-p2`` (in the ``docker`` and ``agentcore-ipc`` groups),
P1 as ``agentcore-p1`` (only ``agentcore-ipc``). The broker socket lives in the
setgid directory ``/run/agentcore`` (``agentcore-p2:agentcore-ipc``, 2750), so
only those two users can reach it.

Two kinds of enforcement, kept apart:

- OS permissions (this file): who can reach the socket, the Docker socket, the
  journal and the key files; the peer UID each side sees.
- Import contracts (``test_import_contracts.py``): which code can import what.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

from agent_core.types.ids import RequestId
from support import broker_world as bw
from support import builders as b
from support import docker_world as dw
from support import os_world as ow

pytestmark = pytest.mark.sandbox

REPO, PYTHON, TIMEOUT = ow.REPO, ow.PYTHON, ow.TIMEOUT
_uid, _env, _as, _world, _p2, _client, _start_json, _try = (
    ow._uid,
    ow._env,
    ow._as,
    ow._world,
    ow._p2,
    ow._client,
    ow._start_json,
    ow._try,
)


# ---------------------------------------------------------------- peers


def test_p1_and_p2_talk_as_separate_users() -> None:
    with _world() as world, _p2(world):
        result = _client(world, "agentcore-p1", _start_json(bw.start_request()))
        assert result["status"] == "STARTED"


def test_p2_refuses_a_peer_that_is_not_p1() -> None:
    """agentcore-p2 can reach the socket (its group), but is not the P1 user."""
    with _world() as world, _p2(world):
        result = _client(world, "agentcore-p2", _start_json(bw.start_request()))
        assert "PEER" in result["detail"]


def test_p1_refuses_a_broker_that_is_not_p2() -> None:
    with _world() as world, _p2(world):
        result = _client(
            world, "agentcore-p1", _start_json(bw.start_request()), p2_uid=_uid("agentcore-p1")
        )
        assert result["error"] == "SANDBOX_UNAVAILABLE"
        assert "peer" in result["detail"]
        assert result["sent"] is False


def test_other_users_cannot_reach_the_socket() -> None:
    with _world() as world, _p2(world):
        out = _try(
            "nobody",
            f"import socket; s=socket.socket(socket.AF_UNIX); s.connect({str(world['sock'])!r})",
        )
        assert "PermissionError" in out


# ---------------------------------------------------------------- OS boundaries


def test_p1_cannot_reach_the_docker_socket() -> None:
    out = _try(
        "agentcore-p1",
        "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/var/run/docker.sock')",
    )
    assert "PermissionError" in out


def test_p2_can_reach_the_docker_socket() -> None:
    out = _try(
        "agentcore-p2",
        "import socket; s=socket.socket(socket.AF_UNIX);"
        " s.connect('/var/run/docker.sock'); print('ok')",
    )
    assert out == "ok"


def test_p2_cannot_read_the_journal_or_p1s_key() -> None:
    with _world() as world:
        for target in (world["journal"], world["p1"] / "broker.key"):
            out = _try("agentcore-p2", f"open({str(target)!r}, 'rb').read()")
            assert "PermissionError" in out
        out = _try("agentcore-p1", f"open({str(world['p2'] / 'broker.key')!r}, 'rb').read()")
        assert "PermissionError" in out


def test_a_key_file_readable_by_others_is_refused() -> None:
    with _world() as world:
        (world["p2"] / "broker.key").chmod(0o644)
        done = subprocess.run(
            [
                *_as("agentcore-p2"),
                PYTHON,
                "-m",
                "agent_core_broker",
                "serve",
                "--socket",
                str(world["sock"]),
                "--record",
                str(world["p2"] / "exec.log"),
                "--key-file",
                str(world["p2"] / "broker.key"),
                "--p1-uid",
                str(_uid("agentcore-p1")),
            ],
            capture_output=True,
            text=True,
            env=_env(),
            timeout=TIMEOUT,
            check=False,
        )
        assert done.returncode != 0
        assert "group or others" in done.stderr


def test_the_record_is_private_to_p2() -> None:
    with _world() as world:
        mode = (world["p2"] / "exec.log").stat().st_mode & 0o777
        assert mode == 0o600
        out = _try("agentcore-p1", f"open({str(world['p2'] / 'exec.log')!r}).read()")
        assert "PermissionError" in out


# ---------------------------------------------------------------- restart


def test_the_contract_holds_after_p2_restarts_as_its_user() -> None:
    with _world() as world:
        start = bw.start_request(epoch=4)
        with _p2(world) as p2:
            assert (
                _client(world, "agentcore-p1", _start_json(start), epoch=4)["status"] == "STARTED"
            )
            p2.send_signal(signal.SIGKILL)
            p2.wait(timeout=TIMEOUT)
        with _p2(world):
            again = {**_start_json(start), "request_id": str(b.new_id(RequestId))}
            assert _client(world, "agentcore-p1", again, epoch=4)["status"] == "DUPLICATE"
            old = _client(world, "agentcore-p1", _start_json(bw.start_request(epoch=3)), epoch=3)
            assert "STALE_EPOCH" in old["detail"]


# ---------------------------------------------------------------- P2 with Docker as its own user


def test_p2_runs_a_fixed_program_in_docker_as_its_own_user() -> None:
    from agent_core_broker.docker_backend import program_ref

    dw.remove_managed_containers()
    with _world() as world, _p2(world, docker=True):
        start = bw.start_request().model_copy(
            update={
                "program_ref": program_ref("diag.exit0"),
                "sandbox_profile_hash": dw.PROFILE.profile_hash(),
            }
        )
        result = _client(world, "agentcore-p1", _start_json(start))
        assert result["status"] == "STARTED", result
    dw.remove_managed_containers()
