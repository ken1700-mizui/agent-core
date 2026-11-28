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
    sandbox: FakeSandbox | None = None,
    boot: Any = None,
    supervisor: Any = "fake",
) -> Broker:
    """``supervisor="fake"`` (the default): the real Deadline Supervisor with
    cgroups emulated over the FakeSandbox (``broker.ds``, ``broker.ds_cgroups``);
    ``None``: no supervisor (every start refused); any other value is passed."""
    from support import supervisor_world as dsw

    directory.mkdir(parents=True, exist_ok=True)
    path = record_path(directory)
    if create and not path.exists():
        ExecutionRecord.create(path, KEY).close()
    clock = clock if clock is not None else ManualMonotonic()
    fake = sandbox if sandbox is not None else FakeSandbox()
    cgroups = None
    if supervisor in ("fake", "memory"):
        cgroups = dsw.SandboxCgroups(fake)
        extra = {} if boot is None else {"boot": boot}
        supervisor = (
            dsw.supervisor(directory / "ds", cgroups, **extra)
            if supervisor == "fake"
            else dsw.memory_supervisor(cgroups, **extra)
        )
    broker = Broker.open(
        record_path=path,
        sandbox=fake,
        key=KEY,
        p1_uid=UID if p1_uid is None else p1_uid,
        instance_id=b.new_id(BrokerInstanceId),
        clock=clock,
        allowed_profiles=frozenset({PROFILE}),
        supervisor=supervisor,
        **({} if boot is None else {"boot": boot}),
    )
    broker.ds = supervisor  # type: ignore[attr-defined]
    broker.ds_cgroups = cgroups  # type: ignore[attr-defined]
    return broker


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
    admission: Callable[[], None] | None = None,
) -> BrokerClient:
    """``admission`` defaults to ``ADMITTED`` (tests about the broker itself;
    the heartbeat admission is tested with the real monitor)."""
    return BrokerClient(
        connector=connector if connector is not None else pair_connector(broker),
        key=key,
        p2_uid=UID if p2_uid is None else p2_uid,
        owner_epoch=lambda: epoch,
        admission=admission if admission is not None else ADMITTED,
        timeout_s=5.0,
    )


def ADMITTED() -> None:  # noqa: N802 (a constant-like test admission)
    """A test-only admission that always admits. Never used by the product."""


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


def ds_of(broker: Broker) -> Any:
    """The broker's test Deadline Supervisor (``make_broker``)."""
    return broker.ds  # type: ignore[attr-defined]


def cgroups_of(broker: Broker) -> Any:
    return broker.ds_cgroups  # type: ignore[attr-defined]
