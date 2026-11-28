"""P2 command line (WP2-02): ``init`` the execution record, or ``serve``.

``serve`` runs the broker with the fake backend only (WP2-02: no Docker). The
key file holds the broker key as hex and must not be readable by group or
others. Prints ``serving`` once the socket accepts connections.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import time
from pathlib import Path

from agent_core.types.ids import BrokerInstanceId, SiteId
from agent_core.types.safety import SecretKey
from agent_core_broker.fake import FakeSandbox
from agent_core_broker.record import ExecutionRecord
from agent_core_broker.server import Broker

SITE = SiteId("devp2")


class SystemMonotonic:
    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


def _key(path: Path) -> SecretKey:
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("the broker key file must not be readable by group or others")
    return SecretKey(bytes.fromhex(path.read_text().strip()))


def _instance() -> BrokerInstanceId:
    random_bits = int.from_bytes(os.urandom(10), "big")
    return BrokerInstanceId.compose(SITE, time.time_ns() // 1_000_000, random_bits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_core_broker")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--record", type=Path, required=True)
    init.add_argument("--key-file", type=Path, required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--socket", type=Path, required=True)
    serve.add_argument("--record", type=Path, required=True)
    serve.add_argument("--key-file", type=Path, required=True)
    serve.add_argument("--p1-uid", type=int, required=True)
    serve.add_argument("--profile", action="append", default=[])
    args = parser.parse_args(argv)
    if args.command == "init":
        ExecutionRecord.create(args.record, _key(args.key_file)).close()
        print("record created")
        return 0
    if not args.socket.is_absolute():
        raise SystemExit("the socket path must be absolute")
    broker = Broker.open(
        record_path=args.record,
        sandbox=FakeSandbox(),
        key=_key(args.key_file),
        p1_uid=args.p1_uid,
        instance_id=_instance(),
        clock=SystemMonotonic(),
        allowed_profiles=frozenset(args.profile),
    )
    note = "" if broker.record_error is None else " (execution record unavailable)"
    broker.serve(args.socket, ready=lambda: print("serving" + note, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
