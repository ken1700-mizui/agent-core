"""The P2 broker: request handling and the AF_UNIX serve loop (sandbox design 7.6).

Order of every request (``handle``), refusing at the first failure with
nothing changed:

1. the peer is the P1 user (``SO_PEERCRED``, checked before reading);
2. one frame within the size limit; strict JSON; a MAC that verifies;
   the schema, the fixed kinds, the typed body; envelope and body agree;
3. the execution record is usable;
4. the owner epoch is not older than the highest one recorded;
5. the kind's own rules (profile and program allowed; at most one start per
   Receipt; collect only when finished; reap only when nothing runs).

Then the epoch high-water mark is raised (an authenticated request only) and,
for a start, ``ACCEPTED`` is recorded and synced before the sandbox is asked to
run. Deadlines are P2's: an execution past its ``deadline_ms`` (P2's own
monotonic clock) is stopped, independently of P1.

P2 never reads or writes the Journal or the ledger.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
import stat
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from agent_core.types import broker_ipc as ipc
from agent_core.types.ids import AttemptId, BrokerInstanceId, ReceiptId
from agent_core.types.safety import SecretKey
from agent_core.types.sandbox import (
    NOTHING_RUNS,
    CollectRequest,
    CollectResult,
    PrepareRequest,
    PrepareResult,
    ReapRequest,
    ReapResult,
    SandboxState,
    StartRequest,
    StartResult,
    StartStatus,
    StatusRequest,
    StatusResult,
    StopRequest,
    StopResult,
)
from agent_core_broker.fake import FakeSandbox
from agent_core_broker.record import ExecutionRecord, ExecutionRecordError

_PEERCRED = struct.Struct("3i")
CONNECTION_TIMEOUT_S = 5.0


class Monotonic(Protocol):
    def monotonic_ms(self) -> int: ...


class _RefusedError(Exception):
    def __init__(self, code: ipc.RefusalCode) -> None:
        super().__init__(code.value)
        self.code = code


_BODIES: dict[ipc.RequestKind, type[BaseModel]] = {
    ipc.RequestKind.PREPARE: PrepareRequest,
    ipc.RequestKind.START: StartRequest,
    ipc.RequestKind.STATUS: StatusRequest,
    ipc.RequestKind.STOP: StopRequest,
    ipc.RequestKind.COLLECT: CollectRequest,
    ipc.RequestKind.REAP: ReapRequest,
}


def peer_uid(sock: socket.socket) -> int:
    pid, uid, gid = _PEERCRED.unpack(
        sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    )
    del pid, gid
    return int(uid)


class Broker:
    def __init__(
        self,
        *,
        record: ExecutionRecord | None,
        record_error: str | None,
        sandbox: FakeSandbox,
        key: SecretKey,
        p1_uid: int,
        instance_id: BrokerInstanceId,
        clock: Monotonic,
        allowed_profiles: frozenset[str],
    ) -> None:
        self._record = record
        self.record_error = record_error
        self.sandbox = sandbox
        self._key = key
        self._p1_uid = p1_uid
        self._instance = instance_id
        self._clock = clock
        self._profiles = allowed_profiles
        # Connections that ended before the answer was sent (last 100 kinds).
        self.connection_errors: list[str] = []

    @classmethod
    def open(
        cls,
        *,
        record_path: Path,
        sandbox: FakeSandbox,
        key: SecretKey,
        p1_uid: int,
        instance_id: BrokerInstanceId,
        clock: Monotonic,
        allowed_profiles: frozenset[str],
    ) -> Broker:
        """A broker whose record cannot be opened still answers, refusing everything."""
        try:
            record: ExecutionRecord | None = ExecutionRecord.open(record_path)
            error = None
        except ExecutionRecordError as exc:
            record, error = None, str(exc)
        return cls(
            record=record,
            record_error=error,
            sandbox=sandbox,
            key=key,
            p1_uid=p1_uid,
            instance_id=instance_id,
            clock=clock,
            allowed_profiles=allowed_profiles,
        )

    # ------------------------------------------------------------ responses

    def _response(
        self,
        request: ipc.BrokerRequest | None,
        *,
        refusal: ipc.RefusalCode | None = None,
        body: BaseModel | dict[str, Any] | None = None,
    ) -> bytes:
        if isinstance(body, BaseModel):
            body = body.model_dump(mode="json")
        data: dict[str, Any] = {
            "version": ipc.PROTOCOL_VERSION,
            "kind": None if request is None else request.kind.value,
            "request_id": None if request is None else str(request.request_id),
            "attempt_id": None
            if request is None or request.attempt_id is None
            else str(request.attempt_id),
            "broker_instance_id": str(self._instance),
            "ok": refusal is None,
            "refusal": None if refusal is None else refusal.value,
            "body": body if refusal is None else None,
        }
        return ipc.frame(ipc.encode_request(ipc.sign_response(data, self._key)))

    # ------------------------------------------------------------ handling

    def handle(self, data: bytes, *, peer_uid: int) -> bytes:
        if peer_uid != self._p1_uid:
            return self._response(None, refusal=ipc.RefusalCode.PEER)
        try:
            request = ipc.decode_request(ipc.unframe(data), self._key)
        except ipc.IpcAuthenticationError:
            return self._response(None, refusal=ipc.RefusalCode.AUTHENTICATION)
        except ipc.IpcFormatError:
            return self._response(None, refusal=ipc.RefusalCode.MALFORMED)
        try:
            body = self._body(request)
            record = self._usable_record()
            if request.owner_epoch < record.epoch:
                raise _RefusedError(ipc.RefusalCode.STALE_EPOCH)
            self.tick()
            result = self._dispatch(request, body, record)
        except _RefusedError as refused:
            return self._response(request, refusal=refused.code)
        except ExecutionRecordError as exc:
            self._record, self.record_error = None, str(exc)
            return self._response(request, refusal=ipc.RefusalCode.RECORD_UNAVAILABLE)
        return self._response(request, body=result)

    def _usable_record(self) -> ExecutionRecord:
        if self._record is None:
            raise _RefusedError(ipc.RefusalCode.RECORD_UNAVAILABLE)
        return self._record

    def _body(self, request: ipc.BrokerRequest) -> BaseModel | None:
        model = _BODIES.get(request.kind)
        if model is None:  # SELF_DIAGNOSE has no body fields
            if request.body:
                raise _RefusedError(ipc.RefusalCode.MALFORMED)
            return None
        try:
            body = model.model_validate_json(json.dumps(request.body))
        except ValidationError as exc:
            raise _RefusedError(ipc.RefusalCode.MALFORMED) from exc
        same_ids = (
            getattr(body, "request_id", None) == request.request_id
            and getattr(body, "attempt_id", None) == request.attempt_id
        )
        if not same_ids:
            raise _RefusedError(ipc.RefusalCode.MALFORMED)
        if isinstance(body, StartRequest) and body.owner_epoch != request.owner_epoch:
            raise _RefusedError(ipc.RefusalCode.MALFORMED)
        return body

    def _dispatch(
        self, request: ipc.BrokerRequest, body: BaseModel | None, record: ExecutionRecord
    ) -> BaseModel | dict[str, Any]:
        handlers: dict[ipc.RequestKind, Callable[[Any, ExecutionRecord], BaseModel]] = {
            ipc.RequestKind.PREPARE: self._prepare,
            ipc.RequestKind.START: self._start,
            ipc.RequestKind.STATUS: self._status,
            ipc.RequestKind.STOP: self._stop,
            ipc.RequestKind.COLLECT: self._collect,
            ipc.RequestKind.REAP: self._reap,
        }
        if body is None:
            record.raise_epoch(request.owner_epoch)
            return {"record_usable": True, "backend": "fake"}
        if isinstance(body, StartRequest):
            return self._start(body, record)  # raises the epoch itself, before ACCEPTED
        result = handlers[request.kind](body, record)
        record.raise_epoch(request.owner_epoch)
        return result

    # ------------------------------------------------------------ kinds

    def _prepare(self, body: PrepareRequest, record: ExecutionRecord) -> PrepareResult:
        del record
        return PrepareResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            prepared=body.sandbox_profile_hash in self._profiles,
        )

    def _start(self, body: StartRequest, record: ExecutionRecord) -> StartResult:
        known = record.latest(body.receipt_id)
        if known is not None:
            if known["attempt_id"] != str(body.attempt_id):
                raise _RefusedError(ipc.RefusalCode.MALFORMED)
            return StartResult(
                request_id=body.request_id,
                attempt_id=body.attempt_id,
                receipt_id=body.receipt_id,
                status=StartStatus.DUPLICATE,
                state=self._observe(body.receipt_id, record),
            )
        if body.sandbox_profile_hash not in self._profiles:
            raise _RefusedError(ipc.RefusalCode.PROFILE_REFUSED)
        if not self.sandbox.knows_program(body.program_ref):
            raise _RefusedError(ipc.RefusalCode.PROGRAM_REFUSED)
        record.raise_epoch(body.owner_epoch)
        record.accept(body, started_ms=self._clock.monotonic_ms())  # synced before the run
        self.sandbox.start(body.receipt_id, body.program_ref)
        return StartResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            receipt_id=body.receipt_id,
            status=StartStatus.STARTED,
            state=self._observe(body.receipt_id, record),
        )

    def _observe(self, receipt_id: ReceiptId, record: ExecutionRecord) -> SandboxState:
        """The record's state, completed with what the live sandbox reports."""
        state = record.state(receipt_id)
        container = self.sandbox.containers.get(receipt_id)
        if state is not SandboxState.ACCEPTED or container is None:
            return state
        if container.state is SandboxState.FINISHED:
            assert container.program.exit_code is not None  # noqa: S101 (finished)
            record.finished(
                receipt_id,
                exit_code=container.program.exit_code,
                output_ref=container.output_ref,
                wall_ms=container.program.wall_ms,
            )
            return SandboxState.FINISHED
        if container.state is SandboxState.STOPPED:
            record.stopped(receipt_id, reason="stop")
            return SandboxState.STOPPED
        return SandboxState.RUNNING

    def _known(self, receipt_id: ReceiptId, attempt_id: AttemptId, record: ExecutionRecord) -> None:
        found = record.latest(receipt_id)
        if found is None or found["attempt_id"] != str(attempt_id):
            raise _RefusedError(ipc.RefusalCode.UNKNOWN_RECEIPT)

    def _status(self, body: StatusRequest, record: ExecutionRecord) -> StatusResult:
        state = self._observe(body.receipt_id, record)
        found = record.latest(body.receipt_id)
        if found is not None and found["attempt_id"] != str(body.attempt_id):
            raise _RefusedError(ipc.RefusalCode.UNKNOWN_RECEIPT)
        exit_code = None
        if found is not None and state in (SandboxState.FINISHED, SandboxState.REAPED):
            exit_code = found.get("exit_code")
        return StatusResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            receipt_id=body.receipt_id,
            state=state,
            exit_code=exit_code,
        )

    def _stop(self, body: StopRequest, record: ExecutionRecord) -> StopResult:
        self._known(body.receipt_id, body.attempt_id, record)
        state = self._observe(body.receipt_id, record)
        container = self.sandbox.containers.get(body.receipt_id)
        running = state is SandboxState.RUNNING and container is not None
        if running and self.sandbox.stop(body.receipt_id, body.grace_ms):
            state = self._observe(body.receipt_id, record)
        return StopResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            state=state,
            stop_confirmed=state in NOTHING_RUNS,
        )

    def _collect(self, body: CollectRequest, record: ExecutionRecord) -> CollectResult:
        self._known(body.receipt_id, body.attempt_id, record)
        self._observe(body.receipt_id, record)
        found = record.latest(body.receipt_id)
        if found is None or "exit_code" not in found:
            raise _RefusedError(ipc.RefusalCode.NOT_FINISHED)
        return CollectResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            receipt_id=body.receipt_id,
            exit_code=found["exit_code"],
            output_ref=found["output_ref"],
            wall_ms=found["wall_ms"],
        )

    def _reap(self, body: ReapRequest, record: ExecutionRecord) -> ReapResult:
        self._known(body.receipt_id, body.attempt_id, record)
        state = self._observe(body.receipt_id, record)
        if state not in NOTHING_RUNS:
            raise _RefusedError(ipc.RefusalCode.NOT_FINISHED)
        if state is not SandboxState.REAPED:
            self.sandbox.reap(body.receipt_id)
            record.reaped(body.receipt_id)
        return ReapResult(
            request_id=body.request_id, attempt_id=body.attempt_id, state=SandboxState.REAPED
        )

    # ------------------------------------------------------------ deadlines

    def tick(self) -> None:
        """Stop every live execution past its deadline (P2's own clock)."""
        record = self._record
        if record is None:
            return
        now = self._clock.monotonic_ms()
        for entry in record.accepted():
            receipt = ReceiptId(entry["receipt_id"])
            container = self.sandbox.containers.get(receipt)
            if entry["state"] != SandboxState.ACCEPTED.value or container is None:
                continue
            if container.state is not SandboxState.RUNNING:
                continue
            expired = now - int(entry["started_ms"]) >= int(entry["deadline_ms"])
            if expired and self.sandbox.stop(receipt, 0):
                record.stopped(receipt, reason="deadline")

    # ------------------------------------------------------------ sockets

    def serve_connection(self, sock: socket.socket) -> None:
        """One request and its response on a connected AF_UNIX stream socket."""
        try:
            sock.settimeout(CONNECTION_TIMEOUT_S)
            uid = peer_uid(sock)
            if uid != self._p1_uid:
                sock.sendall(self._response(None, refusal=ipc.RefusalCode.PEER))
                return
            try:
                data = ipc.read_frame(sock)
            except ipc.IpcFormatError:
                sock.sendall(self._response(None, refusal=ipc.RefusalCode.MALFORMED))
                return
            sock.sendall(self.handle(data, peer_uid=uid))
        except OSError as exc:
            # The peer went away (the answer may be lost). Recorded, not retried:
            # the execution record already says what was done; P1 asks with status.
            self.connection_errors.append(type(exc).__name__)
            del self.connection_errors[:-100]
        finally:
            sock.close()

    def serve(self, path: Path, *, ready: Callable[[], None] | None = None) -> None:
        """Serve on a socket path that only the P1 and P2 users can use.

        One broker per socket: an exclusive lock on ``<socket>.lock`` is held while
        serving (released by the OS when the process dies). Only the lock holder
        removes a socket file left by a broker that was killed."""
        lock = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock)
            raise SystemExit("another broker serves this socket") from exc
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                os.close(lock)
                raise SystemExit("the socket path is not a socket")
            path.unlink()  # left by a broker that was killed (we hold the lock)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o117)
        try:
            server.bind(str(path))
        finally:
            os.umask(previous)
        try:
            server.listen(8)
            server.settimeout(0.2)
            if ready is not None:
                ready()
            while True:
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    self.tick()
                    continue
                self.serve_connection(conn)
        finally:
            server.close()
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            os.close(lock)
