"""WP2-02: P2 as a separate process (``python -m agent_core_broker``).

- P2 is a separate process serving an AF_UNIX socket path; P1 (a child here,
  because the pytest process refuses connect and bind) talks to it.
- SIGKILL and a restart of P2: the Receipt is never run again, the older
  ``owner_epoch`` stays refused.
- A broker that is not running: the client refuses before sending.

Not verified here: P1 and P2 as **different OS users** (needs E-2, OS users,
not approved). The tests run P1 and P2 as the same user and check the peer UID
rule with a configured mismatching UID instead. Recorded as not run.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent_core.types.ids import RequestId
from support import broker_world as bw
from support import builders as b

REPO = Path(__file__).resolve().parents[2]
CHILD = REPO / "tests" / "support" / "broker_child.py"
TIMEOUT = 30


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / p) for p in ("src", "tests"))
    return env


@contextmanager
def _short_dir() -> Iterator[Path]:
    # AF_UNIX paths are short (108 bytes); pytest's tmp_path can be longer.
    with tempfile.TemporaryDirectory(prefix="b2-") as name:
        yield Path(name)


def _init(directory: Path) -> None:
    key = directory / "broker.key"
    key.write_bytes(bw.KEY.reveal().hex().encode())
    key.chmod(0o600)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_core_broker",
            "init",
            "--record",
            str(directory / "exec.log"),
            "--key-file",
            str(key),
        ],
        check=True,
        env=_env(),
        timeout=TIMEOUT,
        capture_output=True,
    )


@contextmanager
def _broker(directory: Path, *, p1_uid: int = bw.UID) -> Iterator[subprocess.Popen[str]]:
    socket_path = directory / "p2.sock"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_core_broker",
            "serve",
            "--socket",
            str(socket_path),
            "--record",
            str(directory / "exec.log"),
            "--key-file",
            str(directory / "broker.key"),
            "--diagnostics-without-supervisor",  # dev mode (no DS here, D20)
            "--p1-uid",
            str(p1_uid),
            "--profile",
            bw.PROFILE,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        assert line.startswith("serving"), (line, process.stderr.read() if process.stderr else "")
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=TIMEOUT)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _client(directory: Path, epoch: int, request: dict[str, Any]) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, str(CHILD), str(directory / "p2.sock"), str(epoch), json.dumps(request)],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=TIMEOUT,
        check=True,
    )
    result: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return result


def _start_json(start: Any) -> dict[str, Any]:
    return {"kind": "start", **json.loads(start.model_dump_json())}


def test_p2_runs_as_its_own_process_and_survives_sigkill() -> None:
    with _short_dir() as d:
        _init(d)
        start = bw.start_request(epoch=4)
        with _broker(d) as p2:
            first = _client(d, 4, _start_json(start))
            assert first["status"] == "STARTED"
            assert p2.pid != os.getpid()
            p2.send_signal(signal.SIGKILL)
            p2.wait(timeout=TIMEOUT)
        with _broker(d):
            again = _client(d, 4, {**_start_json(start), "request_id": str(b.new_id(RequestId))})
            assert again["status"] == "DUPLICATE"
            old = bw.start_request(epoch=3)
            refused = _client(d, 3, _start_json(old))
            assert refused["error"] == "SANDBOX_UNAVAILABLE"
            assert "STALE_EPOCH" in refused["detail"]
            status = _client(
                d,
                4,
                {
                    "kind": "status",
                    "request_id": str(b.new_id(RequestId)),
                    "attempt_id": str(start.attempt_id),
                    "receipt_id": str(start.receipt_id),
                },
            )
            assert status["state"] in ("FINISHED", "ACCEPTED")


def test_a_broker_that_is_not_running_is_refused_before_sending() -> None:
    with _short_dir() as d:
        _init(d)
        result = _client(d, 1, _start_json(bw.start_request()))
        assert result["error"] == "SANDBOX_UNAVAILABLE"
        assert result["sent"] is False


def test_a_peer_of_another_uid_is_refused_by_p2() -> None:
    """Same OS user here; P2 is configured to expect another UID (the E-2 rule)."""
    with _short_dir() as d:
        _init(d)
        with _broker(d, p1_uid=bw.UID + 1):
            result = _client(d, 1, _start_json(bw.start_request()))
            assert result["error"] == "SANDBOX_UNAVAILABLE"
            assert "PEER" in result["detail"]


def test_p2_refuses_to_serve_without_its_record() -> None:
    with _short_dir() as d:
        key = d / "broker.key"
        key.write_bytes(bw.KEY.reveal().hex().encode())
        key.chmod(0o600)
        with _broker(d):
            result = _client(d, 1, _start_json(bw.start_request()))
            assert "RECORD_UNAVAILABLE" in result["detail"]


def test_the_socket_is_private_to_its_users() -> None:
    with _short_dir() as d:
        _init(d)
        with _broker(d):
            mode = (d / "p2.sock").stat().st_mode & 0o777
            assert mode & 0o007 == 0  # no access for others


def test_only_one_broker_serves_a_socket() -> None:
    with _short_dir() as d:
        _init(d)
        with _broker(d):
            second = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_core_broker",
                    "serve",
                    "--socket",
                    str(d / "p2.sock"),
                    "--record",
                    str(d / "exec.log"),
                    "--key-file",
                    str(d / "broker.key"),
                    "--diagnostics-without-supervisor",  # dev mode (no DS here, D20)
                    "--p1-uid",
                    str(bw.UID),
                ],
                capture_output=True,
                text=True,
                env=_env(),
                timeout=TIMEOUT,
                check=False,
            )
            assert second.returncode != 0
            assert "another broker" in second.stderr
            result = _client(d, 1, _start_json(bw.start_request()))
            assert result["status"] == "STARTED"  # the first broker still serves
