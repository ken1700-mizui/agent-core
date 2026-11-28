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

import json
import os
import pwd
import signal
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_core.types.ids import RequestId
from support import broker_world as bw
from support import builders as b
from support import docker_world as dw

pytestmark = pytest.mark.sandbox

REPO = Path(__file__).resolve().parents[2]
CHILD = REPO / "tests" / "support" / "broker_child.py"
PYTHON = sys.executable
TIMEOUT = 60


def _uid(name: str) -> int:
    return pwd.getpwnam(name).pw_uid


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{REPO}/src:{REPO}/tests"}
    env["HOME"] = "/tmp"  # noqa: S108 (the service users have no home)
    env.update(extra or {})
    return env


def _as(user: str) -> list[str]:
    group = "nogroup" if user == "nobody" else user
    return ["setpriv", f"--reuid={user}", f"--regid={group}", "--init-groups", "--"]


@contextmanager
def _world() -> Iterator[dict[str, Path]]:
    tag = uuid.uuid4().hex[:8]
    p2 = Path(f"/var/lib/agentcore-p2/t-{tag}")
    p1 = Path(f"/var/lib/agentcore-p1/t-{tag}")
    sock = Path(f"/run/agentcore/t-{tag}.sock")
    for directory, user in ((p2, "agentcore-p2"), (p1, "agentcore-p1")):
        directory.mkdir(mode=0o700)
        os.chown(directory, _uid(user), _uid(user))
    for directory, user in ((p2, "agentcore-p2"), (p1, "agentcore-p1")):
        key = directory / "broker.key"
        key.write_text(bw.KEY.reveal().hex())
        key.chmod(0o600)
        os.chown(key, _uid(user), _uid(user))
    journal = p1 / "journal.db"
    journal.write_bytes(b"synthetic journal placeholder")
    journal.chmod(0o600)
    os.chown(journal, _uid("agentcore-p1"), _uid("agentcore-p1"))
    staging = p2 / "staging"
    staging.mkdir(mode=0o711)
    os.chown(staging, _uid("agentcore-p2"), _uid("agentcore-p2"))
    subprocess.run(
        [
            *_as("agentcore-p2"),
            PYTHON,
            "-m",
            "agent_core_broker",
            "init",
            "--record",
            str(p2 / "exec.log"),
            "--key-file",
            str(p2 / "broker.key"),
        ],
        check=True,
        env=_env(),
        timeout=TIMEOUT,
        capture_output=True,
    )
    try:
        yield {"p1": p1, "p2": p2, "sock": sock, "journal": journal, "staging": staging}
    finally:
        subprocess.run(["rm", "-rf", str(p1), str(p2)], check=False)
        for leftover in (sock, Path(str(sock) + ".lock")):
            leftover.unlink(missing_ok=True)


@contextmanager
def _p2(world: dict[str, Path], *, docker: bool = False) -> Iterator[subprocess.Popen[str]]:
    args = [
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
        "--profile",
        bw.PROFILE,
    ]
    if docker:
        args += [
            "--backend",
            "docker",
            "--image-digest",
            dw.IMAGE_DIGEST,
            "--image-id",
            dw.IMAGE_ID,
            "--staging",
            str(world["staging"]),
        ]
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env()
    )
    try:
        assert process.stdout is not None
        for _ in range(20):
            line = process.stdout.readline()
            if line.startswith("serving") or not line:
                break
        assert line.startswith("serving"), (line, process.stderr.read() if process.stderr else "")
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=TIMEOUT)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _client(
    world: dict[str, Path],
    user: str,
    request: dict[str, Any],
    *,
    epoch: int = 1,
    p2_uid: int | None = None,
) -> dict[str, Any]:
    key_dir = world["p1"] if user == "agentcore-p1" else world["p2"]
    done = subprocess.run(
        [*_as(user), PYTHON, str(CHILD), str(world["sock"]), str(epoch), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
        env=_env(
            {
                "BROKER_CHILD_KEY_FILE": str(key_dir / "broker.key") if user != "nobody" else "",
                "BROKER_CHILD_P2_UID": str(p2_uid if p2_uid is not None else _uid("agentcore-p2")),
            }
        ),
    )
    lines = done.stdout.strip().splitlines()
    assert lines, done.stderr
    result: dict[str, Any] = json.loads(lines[-1])
    return result


def _start_json(start: Any) -> dict[str, Any]:
    return {"kind": "start", **json.loads(start.model_dump_json())}


def _try(user: str, code: str) -> str:
    done = subprocess.run(
        [*_as(user), PYTHON, "-c", code],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=TIMEOUT,
        check=False,
    )
    return (done.stdout + done.stderr).strip().splitlines()[-1]


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
