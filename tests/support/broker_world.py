"""Helpers for the WP2-02 broker tests (synthetic key, fake sandbox only).

The pytest process refuses ``connect`` and ``bind`` on every socket (conftest),
so in-process tests join the client and the broker with ``socketpair`` (no
connect, no bind). Tests with a socket path run both sides in child processes.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.adapters.broker_client import BrokerClient
from agent_core.types.ids import AttemptId, BrokerInstanceId, PermitId, ReceiptId, RequestId
from agent_core.types.safety import SecretKey
from agent_core.types.sandbox import StartRequest
from agent_core_broker import record as rec
from agent_core_broker.fake import FakeSandbox, program_ref
from agent_core_broker.record import ExecutionRecord
from agent_core_broker.server import Broker
from support import builders as b

KEY = SecretKey(hashlib.sha256(b"wp2-02-synthetic-broker-key").digest())
OTHER_KEY = SecretKey(hashlib.sha256(b"wp2-02-synthetic-other-key").digest())
UID = os.getuid()
PROFILE = b.digest("sandbox-profile")


class ManualMonotonic:
    def __init__(self) -> None:
        self.now = 1_000

    def monotonic_ms(self) -> int:
        return self.now


def record_path(directory: Path) -> Path:
    return directory / "execution.log"


def head_path(directory: Path) -> Path:
    return rec.head_path(record_path(directory))


def fake_ref(name: str) -> str:
    return program_ref(name)


def make_broker(
    directory: Path,
    *,
    clock: ManualMonotonic | None = None,
    p1_uid: int | None = None,
    create: bool = True,
) -> Broker:
    directory.mkdir(parents=True, exist_ok=True)
    path = record_path(directory)
    if create and not path.exists():
        ExecutionRecord.create(path, KEY).close()
    clock = clock if clock is not None else ManualMonotonic()
    return Broker.open(
        record_path=path,
        sandbox=FakeSandbox(),
        key=KEY,
        p1_uid=UID if p1_uid is None else p1_uid,
        instance_id=b.new_id(BrokerInstanceId),
        clock=clock,
        allowed_profiles=frozenset({PROFILE}),
    )


def pair_connector(broker: Broker) -> Callable[[], socket.socket]:
    def connect() -> socket.socket:
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        thread = threading.Thread(target=broker.serve_connection, args=(theirs,), daemon=True)
        thread.start()
        return ours

    return connect


def make_client(
    broker: Broker,
    *,
    epoch: int = 1,
    p2_uid: int | None = None,
    key: SecretKey = KEY,
    connector: Callable[[], socket.socket] | None = None,
) -> BrokerClient:
    return BrokerClient(
        connector=connector if connector is not None else pair_connector(broker),
        key=key,
        p2_uid=UID if p2_uid is None else p2_uid,
        owner_epoch=lambda: epoch,
        timeout_s=5.0,
    )


def start_request(
    *,
    program: str = "exit0",
    epoch: int = 1,
    receipt: ReceiptId | None = None,
    attempt: AttemptId | None = None,
    **overrides: Any,
) -> StartRequest:
    values: dict[str, Any] = {
        "request_id": b.new_id(RequestId),
        "attempt_id": attempt if attempt is not None else b.new_id(AttemptId),
        "permit_id": b.new_id(PermitId),
        "receipt_id": receipt if receipt is not None else b.new_id(ReceiptId),
        "owner_epoch": epoch,
        "sandbox_profile_hash": PROFILE,
        "program_ref": program_ref(program),
        "deadline_ms": 60_000,
        "grace_ms": 100,
    }
    values.update(overrides)
    return StartRequest(**values)
