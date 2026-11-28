"""``python -m agent_core_supervisor serve``: the Deadline Supervisor service.

It refuses to run (exit 2, nothing served) unless all of these hold, because
without them it cannot guarantee anything and P2 must refuse every start:
- it runs under systemd (``INVOCATION_ID`` set and /run/systemd/system exists),
  so ``Restart=`` and ``WatchdogSec=`` apply;
- the delegated cgroup root is on cgroup v2 (A1; ``deploy/deadline-supervisor``).
There is no option to skip these checks.
"""

from __future__ import annotations

import argparse
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

from agent_core_supervisor.cgroups import CgroupV2
from agent_core_supervisor.server import SupervisorServer
from agent_core_supervisor.supervisor import Supervisor


def systemd_unavailable() -> str | None:
    if not os.environ.get("INVOCATION_ID"):
        return "not started by systemd (no INVOCATION_ID)"
    if not Path("/run/systemd/system").is_dir():
        return "systemd is not the init system"
    return None


def _notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    target = "\0" + address[1:] if address.startswith("@") else address
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.sendto(message.encode(), target)


def _key(path: Path) -> bytes:
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit("the key file must not be readable by group or others")
    return bytes.fromhex(path.read_text().strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_core_supervisor")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--socket", type=Path, required=True)
    serve.add_argument("--record", type=Path, required=True)
    serve.add_argument("--key-file", type=Path, required=True)
    serve.add_argument("--cgroup-root", type=Path, required=True)
    serve.add_argument("--p2-uid", type=int, required=True)
    serve.add_argument("--tick-s", type=float, default=1.0)
    args = parser.parse_args(argv)
    cgroups = CgroupV2(args.cgroup_root)
    for reason in (systemd_unavailable(), cgroups.unavailable()):
        if reason is not None:
            print(f"refused: {reason}", file=sys.stderr, flush=True)
            return 2
    key = _key(args.key_file)
    instance = f"ds-{os.getpid()}-{time.time_ns()}"
    ds = Supervisor.open(record_path=args.record, key=key, cgroups=cgroups, instance_id=instance)
    server = SupervisorServer(ds, key=key, p2_uid=args.p2_uid)

    def enforce() -> None:
        while True:
            ds.tick()
            _notify("WATCHDOG=1")
            time.sleep(args.tick_s)

    threading.Thread(target=enforce, daemon=True).start()
    if args.socket.exists():
        args.socket.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(args.socket))
        os.chmod(args.socket, 0o660)
        listener.listen(16)
        _notify("READY=1")
        print(f"serving ({ds.status().health.value})", flush=True)
        while True:
            conn, _ = listener.accept()
            threading.Thread(target=server.serve_connection, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
