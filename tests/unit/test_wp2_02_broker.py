"""WP2-02: the P2 broker, its execution record and the P1 client (fake sandbox).

Contract: ``PHASE_2_SANDBOX_DESIGN.md`` 7.6 and 7.6.1 (D16).

- Typed, size-limited, MAC'd requests and responses; ``SO_PEERCRED`` on both
  sides; anything malformed or unauthenticated is refused with no change.
- At most one start per Receipt. The durable record is P2's execution record
  (written and synced before anything runs), not a container label: a reaped
  execution, a restarted broker or a lost response never runs it again.
- ``owner_epoch`` high-water mark is in the record: a restart does not revive
  an older epoch; only authenticated requests raise it.
- A stop request is not a confirmed stop. P2 stops what passes its deadline.
- If the record cannot be used, every start is refused.
- No Docker, no process: the fake sandbox only records what it would do.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core.types import broker_ipc as ipc
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import AttemptId, RequestId
from agent_core.types.sandbox import (
    CollectRequest,
    PrepareRequest,
    ReapRequest,
    SandboxState,
    StartStatus,
    StatusRequest,
    StopRequest,
)
from agent_core_broker.record import ExecutionRecord, ExecutionRecordError, read_entries
from support import broker_world as bw
from support import builders as b

REPO = Path(__file__).resolve().parents[2]


def _status(client: Any, start: Any) -> Any:
    return client.status(
        StatusRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )


def _frame(
    kind: ipc.RequestKind,
    body: dict[str, Any],
    *,
    epoch: int = 1,
    key: Any = bw.KEY,
    request_id: RequestId | None = None,
    version: int = ipc.PROTOCOL_VERSION,
) -> bytes:
    request = ipc.unsigned_request(
        kind=kind,
        request_id=request_id if request_id is not None else RequestId(body["request_id"]),
        attempt_id=AttemptId(body["attempt_id"]) if "attempt_id" in body else None,
        owner_epoch=epoch,
        body=body,
    )
    data = json.loads(ipc.encode_request(ipc.sign_request(request, key)))
    data["version"] = version
    return ipc.frame(json.dumps(data).encode())


def _handle(broker: Any, data: bytes, *, uid: int = bw.UID) -> ipc.BrokerResponse:
    return ipc.decode_response(ipc.unframe(broker.handle(data, peer_uid=uid)), bw.KEY)


def _start_body(start: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(start.model_dump_json())
    return body


# ---------------------------------------------------------------- the normal path


def test_prepare_start_status_collect_reap(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    start = bw.start_request()
    prepared = client.prepare(
        PrepareRequest(
            request_id=b.new_id(RequestId),
            attempt_id=start.attempt_id,
            sandbox_profile_hash=bw.PROFILE,
            image_digest=b.digest("image"),
            workspace_ref=b.digest("workspace"),
        )
    )
    assert prepared.prepared
    started = client.start(start)
    assert (started.status, started.receipt_id) == (StartStatus.STARTED, start.receipt_id)
    assert _status(client, start).state is SandboxState.FINISHED
    collected = client.collect(
        CollectRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )
    assert collected.exit_code == 0
    reaped = client.reap(
        ReapRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )
    assert reaped.state is SandboxState.REAPED
    assert broker.sandbox.runs[start.receipt_id] == 1


def test_an_unknown_profile_is_not_prepared(tmp_path: Path) -> None:
    client = bw.make_client(bw.make_broker(tmp_path))
    result = client.prepare(
        PrepareRequest(
            request_id=b.new_id(RequestId),
            attempt_id=b.new_id(AttemptId),
            sandbox_profile_hash=b.digest("another profile"),
            image_digest=b.digest("image"),
            workspace_ref=b.digest("workspace"),
        )
    )
    assert not result.prepared


def test_a_start_with_an_unknown_profile_or_program_is_refused(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    for start in (
        bw.start_request(sandbox_profile_hash=b.digest("another profile")),
        bw.start_request(program_ref=b.digest("not a fixed program")),
    ):
        with pytest.raises(SandboxUnavailableError):
            client.start(start)
        assert start.receipt_id not in broker.sandbox.runs
    assert len(read_entries(bw.record_path(tmp_path))) == 1  # genesis only


# ---------------------------------------------------------------- malformed and unauthenticated


@pytest.mark.parametrize(
    "damage",
    [
        "garbage",
        "oversize",
        "version",
        "unknown_kind",
        "bad_mac",
        "wrong_key",
        "missing_field",
        "epoch_mismatch",
        "extra_field",
    ],
)
def test_malformed_requests_are_refused_without_any_change(tmp_path: Path, damage: str) -> None:
    broker = bw.make_broker(tmp_path)
    before = bw.record_path(tmp_path).read_bytes()
    start = bw.start_request()
    body = _start_body(start)
    if damage == "garbage":
        data = ipc.frame(b"\xff not json")
    elif damage == "oversize":
        data = struct.pack(">I", ipc.MAX_MESSAGE_BYTES + 1) + b"x" * 16
    elif damage == "version":
        data = _frame(ipc.RequestKind.START, body, version=99)
    elif damage == "unknown_kind":
        raw = json.loads(ipc.unframe(_frame(ipc.RequestKind.START, body)))
        raw["kind"] = "EXEC"
        data = ipc.frame(json.dumps(raw).encode())
    elif damage == "bad_mac":
        raw = json.loads(ipc.unframe(_frame(ipc.RequestKind.START, body)))
        raw["mac"] = "0" * 64
        data = ipc.frame(json.dumps(raw).encode())
    elif damage == "wrong_key":
        data = _frame(ipc.RequestKind.START, body, key=bw.OTHER_KEY)
    elif damage == "missing_field":
        del body["receipt_id"]
        data = _frame(ipc.RequestKind.START, body)
    elif damage == "epoch_mismatch":
        data = _frame(ipc.RequestKind.START, body, epoch=2)  # body says epoch 1
    else:
        body["command"] = "sh -c id"
        data = _frame(ipc.RequestKind.START, body)
    response = ipc.decode_response(ipc.unframe(broker.handle(data, peer_uid=bw.UID)), bw.KEY)
    assert not response.ok
    assert response.refusal in (ipc.RefusalCode.MALFORMED, ipc.RefusalCode.AUTHENTICATION)
    assert start.receipt_id not in broker.sandbox.runs
    assert bw.record_path(tmp_path).read_bytes() == before


def test_a_request_from_another_user_is_refused(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path, p1_uid=bw.UID + 1)
    start = bw.start_request()
    response = _handle(broker, _frame(ipc.RequestKind.START, _start_body(start)))
    assert response.refusal is ipc.RefusalCode.PEER
    assert start.receipt_id not in broker.sandbox.runs


def test_the_client_refuses_a_broker_of_another_user(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker, p2_uid=bw.UID + 1)
    with pytest.raises(SandboxUnavailableError, match="peer"):
        client.start(bw.start_request())


def test_the_client_refuses_an_unauthenticated_or_mismatched_answer(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    start = bw.start_request()

    def forged(response_for: Any) -> Any:
        def connect() -> socket.socket:
            ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

            def answer() -> None:
                ipc.unframe(ipc.read_frame(theirs))
                theirs.sendall(response_for())
                theirs.close()

            threading.Thread(target=answer, daemon=True).start()
            return ours

        return connect

    def wrong_mac() -> bytes:
        good = json.loads(
            ipc.unframe(
                broker.handle(
                    _frame(
                        ipc.RequestKind.STATUS,
                        {
                            "request_id": start.request_id,
                            "attempt_id": start.attempt_id,
                            "receipt_id": start.receipt_id,
                        },
                    ),
                    peer_uid=bw.UID,
                )
            )
        )
        good["mac"] = "f" * 64
        return ipc.frame(json.dumps(good).encode())

    def other_request() -> bytes:
        other = b.new_id(RequestId)
        return broker.handle(
            _frame(
                ipc.RequestKind.STATUS,
                {
                    "request_id": other,
                    "attempt_id": start.attempt_id,
                    "receipt_id": start.receipt_id,
                },
            ),
            peer_uid=bw.UID,
        )

    for response_for in (wrong_mac, other_request):
        client = bw.make_client(broker, connector=forged(response_for))
        with pytest.raises(SandboxUnavailableError):
            _status(client, start)


# ---------------------------------------------------------------- authority (owner_epoch)


def test_an_older_owner_epoch_is_refused_and_stays_refused_after_a_restart(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    bw.make_client(broker, epoch=3).start(bw.start_request(epoch=3))
    old = bw.start_request(epoch=2)
    response = _handle(broker, _frame(ipc.RequestKind.START, _start_body(old), epoch=2))
    assert response.refusal is ipc.RefusalCode.STALE_EPOCH
    broker.close()  # a restart: the old broker is gone (it held the record's lock)
    restarted = bw.make_broker(tmp_path)
    response = _handle(restarted, _frame(ipc.RequestKind.START, _start_body(old), epoch=2))
    assert response.refusal is ipc.RefusalCode.STALE_EPOCH
    assert old.receipt_id not in restarted.sandbox.runs


def test_an_unauthenticated_request_cannot_raise_the_epoch(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    forged = bw.start_request(epoch=9)
    response = _handle(
        broker, _frame(ipc.RequestKind.START, _start_body(forged), epoch=9, key=bw.OTHER_KEY)
    )
    assert response.refusal is ipc.RefusalCode.AUTHENTICATION
    assert bw.make_client(broker, epoch=1).start(bw.start_request(epoch=1)).status is (
        StartStatus.STARTED
    )


# ---------------------------------------------------------------- at most one start per Receipt


def test_a_receipt_starts_once(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    start = bw.start_request()
    assert client.start(start).status is StartStatus.STARTED
    again = client.start(start.model_copy(update={"request_id": b.new_id(RequestId)}))
    assert again.status is StartStatus.DUPLICATE
    assert broker.sandbox.runs[start.receipt_id] == 1


def test_a_reaped_receipt_never_starts_again(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    start = bw.start_request()
    client.start(start)
    client.reap(
        ReapRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )
    assert broker.sandbox.containers == {}  # nothing left that a label could be read from
    again = client.start(start.model_copy(update={"request_id": b.new_id(RequestId)}))
    assert again.status is StartStatus.DUPLICATE
    assert again.state is SandboxState.REAPED
    assert broker.sandbox.runs[start.receipt_id] == 1


def test_a_lost_response_never_leads_to_a_second_run(tmp_path: Path) -> None:
    """P2 handled the start, but P1 never got the answer (the answer is dropped)."""
    broker = bw.make_broker(tmp_path)
    start = bw.start_request()
    broker.handle(_frame(ipc.RequestKind.START, _start_body(start)), peer_uid=bw.UID)  # answer lost
    client = bw.make_client(broker)
    assert _status(client, start).state is SandboxState.FINISHED
    again = client.start(start.model_copy(update={"request_id": b.new_id(RequestId)}))
    assert again.status is StartStatus.DUPLICATE
    assert broker.sandbox.runs[start.receipt_id] == 1


def test_a_closed_connection_without_an_answer_is_unknown_to_the_client(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)

    def hang_up() -> socket.socket:
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        theirs.close()
        return ours

    client = bw.make_client(broker, connector=hang_up)
    with pytest.raises(SandboxUnavailableError) as excinfo:
        client.start(bw.start_request())
    assert excinfo.value.sent  # the start may have arrived: its outcome is unknown


def test_an_unreachable_broker_refuses_before_sending(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)

    def unreachable() -> socket.socket:
        raise FileNotFoundError("no broker socket")

    client = bw.make_client(broker, connector=unreachable)
    with pytest.raises(SandboxUnavailableError) as excinfo:
        client.start(bw.start_request())
    assert not excinfo.value.sent


# ---------------------------------------------------------------- restart and the record


def test_a_restarted_broker_remembers_every_receipt(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    start = bw.start_request()
    bw.make_client(broker).start(start)
    broker.close()  # a restart: the old broker is gone (it held the record's lock)
    restarted = bw.make_broker(tmp_path)  # same record, empty fake sandbox
    client = bw.make_client(restarted)
    again = client.start(start.model_copy(update={"request_id": b.new_id(RequestId)}))
    assert again.status is StartStatus.DUPLICATE
    assert start.receipt_id not in restarted.sandbox.runs
    collected = client.collect(
        CollectRequest(
            request_id=b.new_id(RequestId), attempt_id=start.attempt_id, receipt_id=start.receipt_id
        )
    )
    assert collected.exit_code == 0  # from the record


def test_an_accepted_start_without_a_result_stays_unknown_after_a_restart(tmp_path: Path) -> None:
    """The record says ACCEPTED (written before the run); the run may or may not
    have happened. After a restart it is never run again and never reported done."""
    path = bw.record_path(tmp_path)
    record = ExecutionRecord.create(path, bw.KEY)
    start = bw.start_request()
    record.accept(start)
    record.close()  # the broker that accepted it is gone
    restarted = bw.make_broker(tmp_path)
    client = bw.make_client(restarted)
    assert _status(client, start).state is SandboxState.ACCEPTED
    assert client.start(start).status is StartStatus.DUPLICATE
    with pytest.raises(SandboxUnavailableError):
        client.collect(
            CollectRequest(
                request_id=b.new_id(RequestId),
                attempt_id=start.attempt_id,
                receipt_id=start.receipt_id,
            )
        )


@pytest.mark.parametrize("damage", ["missing", "truncated", "edited", "reordered"])
def test_an_unusable_record_refuses_every_start(tmp_path: Path, damage: str) -> None:
    broker = bw.make_broker(tmp_path)
    bw.make_client(broker).start(bw.start_request())
    bw.make_client(broker).start(bw.start_request())
    broker.close()
    path = bw.record_path(tmp_path)
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "missing":
        path.unlink()
    elif damage == "truncated":
        path.write_bytes(b"".join(lines)[:-7])
    elif damage == "edited":
        path.write_bytes(b"".join(lines).replace(b'"ACCEPTED"', b'"REAPED"', 1))
    else:
        path.write_bytes(b"".join([lines[0], lines[2], lines[1], *lines[3:]]))
    with pytest.raises(ExecutionRecordError):
        ExecutionRecord.open(path, bw.KEY)
    restarted = bw.make_broker(tmp_path, create=False)
    start = bw.start_request()
    response = _handle(restarted, _frame(ipc.RequestKind.START, _start_body(start)))
    assert response.refusal is ipc.RefusalCode.RECORD_UNAVAILABLE
    assert start.receipt_id not in restarted.sandbox.runs


def test_the_record_is_synced_before_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = bw.make_broker(tmp_path)
    order: list[str] = []
    real_fsync = os.fsync
    original = broker.sandbox.start

    def fsync(fd: int) -> None:
        order.append("fsync")
        real_fsync(fd)

    def start(*args: Any, **kwargs: Any) -> Any:
        order.append("run")
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(broker.sandbox, "start", start)
    bw.make_client(broker).start(bw.start_request())
    assert order.index("fsync") < order.index("run")


# ---------------------------------------------------------------- stop and deadline


def test_a_stop_request_is_not_a_confirmed_stop(tmp_path: Path) -> None:
    broker = bw.make_broker(tmp_path)
    client = bw.make_client(broker)
    stubborn = bw.start_request(program="stubborn")
    client.start(stubborn)
    result = client.stop(
        StopRequest(
            request_id=b.new_id(RequestId),
            attempt_id=stubborn.attempt_id,
            receipt_id=stubborn.receipt_id,
            grace_ms=100,
        )
    )
    assert not result.stop_confirmed
    assert result.state is SandboxState.RUNNING
    hang = bw.start_request(program="hang")
    client.start(hang)
    result = client.stop(
        StopRequest(
            request_id=b.new_id(RequestId),
            attempt_id=hang.attempt_id,
            receipt_id=hang.receipt_id,
            grace_ms=100,
        )
    )
    assert result.stop_confirmed
    assert result.state is SandboxState.STOPPED


def test_p2_stops_an_execution_past_its_deadline(tmp_path: Path) -> None:
    clock = bw.ManualMonotonic()
    broker = bw.make_broker(tmp_path, clock=clock)
    client = bw.make_client(broker)
    hang = bw.start_request(program="hang", deadline_ms=1_000)
    client.start(hang)
    clock.now += 999
    broker.tick()
    assert _status(client, hang).state is SandboxState.RUNNING
    clock.now += 2
    broker.tick()
    assert _status(client, hang).state is SandboxState.STOPPED
    last = read_entries(bw.record_path(tmp_path))[-1]
    assert (last["state"], last["stop_reason"]) == ("STOPPED", "deadline")


# ---------------------------------------------------------------- static boundaries


def test_no_internet_socket_in_the_client_or_the_broker() -> None:
    for path in [
        REPO / "src/agent_core/adapters/broker_client.py",
        *(REPO / "src/agent_core_broker").glob("*.py"),
    ]:
        text = path.read_text()
        for word in ("AF_INET", "AF_INET6", "getaddrinfo", "create_connection", "subprocess"):
            assert word not in text, (path.name, word)


def test_the_client_only_opens_unix_sockets(tmp_path: Path) -> None:
    from agent_core.adapters.broker_client import unix_connector

    with pytest.raises(ValueError, match="absolute"):
        unix_connector(Path("relative.sock"))
