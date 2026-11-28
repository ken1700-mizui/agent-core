"""Process helpers for the E-2 tests: P2 and P1 as their own OS users (sandbox)."""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from support import broker_world as bw
from support import docker_world as dw

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
        # No cgroup v2 / systemd here: the dev fixed-diagnostics mode (D17, D20).
        "--diagnostics-without-supervisor",
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
