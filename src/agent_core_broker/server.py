"""The P2 broker: request handling and the AF_UNIX serve loop (sandbox design 7.6).

Order of every request (``handle``), refusing at the first failure with
nothing changed:

1. the peer is the P1 user (``SO_PEERCRED``, checked before reading);
2. one frame within the size limit; strict JSON; a MAC that verifies;
   the schema, the fixed kinds, the typed body; envelope and body agree;
3. the execution record is usable (opened, locked, not broken);
4. the owner epoch is not older than the highest one recorded;
5. the kind's own rules (profile and program allowed; at most one start per
   Receipt; collect only when finished; reap only when nothing runs).

Then the epoch high-water mark is raised (an authenticated request only) and,
for a start, ``ACCEPTED`` is recorded and synced before the backend is asked
to run. Deadlines are P2's: an execution past its ``deadline_ms`` (P2's own
monotonic clock) is stopped, independently of P1, by ``tick``. ``tick`` also
runs on its own cycle (``start_watchdog``); requests and ticks share one lock,
so a tick waits at most for the request being handled, whose backend calls
and socket operations all have timeouts (B1A-05).

Containment (B1A-03) is decided at the start from what the backend verified
and kept in the record; every answer about a Receipt carries it.

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
import threading
import time
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
    Containment,
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
    _pid, uid, _gid = _PEERCRED.unpack(
        sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    )
    return int(uid)


class Watchdog:
    """Runs ``tick`` every ``cycle_s``; records how late each tick started."""

    def __init__(self, broker: Broker, cycle_s: float) -> None:
        self._broker = broker
        self._cycle = cycle_s
        self._stop = threading.Event()
        self.ticks = 0
        self.max_delay_s = 0.0
        self.errors: list[str] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            scheduled = time.monotonic() + self._cycle
            if self._stop.wait(self._cycle):
                return
            try:
                self._broker.tick()
            except ExecutionRecordError as exc:
                self.errors.append(str(exc))  # the record is then unusable: starts refused
            self.ticks += 1
            self.max_delay_s = max(self.max_delay_s, time.monotonic() - scheduled)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class Broker:
    def __init__(
        self,
        *,
        record: ExecutionRecord | None,
        record_error: str | None,
        sandbox: Any,
        key: SecretKey,
        p1_uid: int,
        instance_id: BrokerInstanceId,
        clock: Monotonic,
        allowed_profiles: frozenset[str],
    ) -> None:
        self._record = record
        self._opened = record  # closed by ``close`` even after the record broke
        self.record_error = record_error
        self.sandbox: Any = sandbox  # a SandboxBackend (fake or Docker)
        self._key = key
        self._p1_uid = p1_uid
        self._instance = instance_id
        self._clock = clock
        self._profiles = allowed_profiles
        self._lock = threading.Lock()
        # Connections that ended before the answer was sent (last 100 kinds).
        self.connection_errors: list[str] = []

    @classmethod
    def open(
        cls,
        *,
        record_path: Path,
        sandbox: Any,
        key: SecretKey,
        p1_uid: int,
        instance_id: BrokerInstanceId,
        clock: Monotonic,
        allowed_profiles: frozenset[str],
    ) -> Broker:
        """A broker whose record cannot be opened (missing, broken, held by another
        broker) still answers, refusing everything."""
        try:
            record: ExecutionRecord | None = ExecutionRecord.open(record_path, key)
            error = None
        except ExecutionRecordError as exc:
            record, error = None, f"{exc.code}: {exc}"
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

    def close(self) -> None:
        with self._lock:
            if self._opened is not None:
                self._opened.close()
            self._record = None

    def record_state(self, receipt_id: ReceiptId) -> SandboxState:
        with self._lock:
            record = self._opened
            return SandboxState.NOT_SEEN if record is None else record.state(receipt_id)

    def start_watchdog(self, cycle_s: float) -> Watchdog:
        return Watchdog(self, cycle_s)

    def recover_backend(self) -> list[str]:
        """After a restart: reap the backend's leftovers whose result is recorded
        (or that the record does not know); keep the others, whose deadlines
        ``tick`` enforces by wall clock. Returns what was done, for the log."""
        adopt = getattr(self.sandbox, "adopt", None)
        if adopt is None:
            return []
        done: list[str] = []
        with self._lock:
            record = self._usable_record()
            for receipt, _cid, running, _deadline in adopt():
                state = record.state(receipt)
                if state is SandboxState.ACCEPTED:
                    done.append(f"kept {receipt} ({'running' if running else 'exited'})")
                    continue
                if running:
                    self.sandbox.stop(receipt, 0)
                self.sandbox.reap(receipt)
                done.append(f"reaped {receipt} ({state.value})")
        return done

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
        attempt = None if request is None or request.attempt_id is None else request.attempt_id
        data: dict[str, Any] = {
            "version": ipc.PROTOCOL_VERSION,
            "kind": None if request is None else request.kind.value,
            "request_id": None if request is None else str(request.request_id),
            "attempt_id": None if attempt is None else str(attempt),
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
        with self._lock:
            try:
                body = self._body(request)
                record = self._usable_record()
                if request.owner_epoch < record.epoch:
                    raise _RefusedError(ipc.RefusalCode.STALE_EPOCH)
                self._tick(record)
                result = self._dispatch(request, body, record)
            except _RefusedError as refused:
                return self._response(request, refusal=refused.code)
            except ExecutionRecordError as exc:
                self._record, self.record_error = None, f"{exc.code}: {exc}"
                return self._response(request, refusal=ipc.RefusalCode.RECORD_UNAVAILABLE)
        return self._response(request, body=result)

    def _usable_record(self) -> ExecutionRecord:
        if self._record is None or self._record.broken is not None:
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
        if body is None:
            record.raise_epoch(request.owner_epoch)
            return {"record_usable": True}
        if isinstance(body, StartRequest):
            return self._start(body, record)  # raises the epoch itself, before ACCEPTED
        handlers: dict[ipc.RequestKind, Callable[[Any, ExecutionRecord], BaseModel]] = {
            ipc.RequestKind.PREPARE: self._prepare,
            ipc.RequestKind.STATUS: self._status,
            ipc.RequestKind.STOP: self._stop,
            ipc.RequestKind.COLLECT: self._collect,
            ipc.RequestKind.REAP: self._reap,
        }
        result = handlers[request.kind](body, record)
        record.raise_epoch(request.owner_epoch)
        return result

    # ------------------------------------------------------------ kinds

    def _prepare(self, body: PrepareRequest, record: ExecutionRecord) -> PrepareResult:
        del record
        ready = bool(getattr(self.sandbox, "ready", True))  # Docker: self-diagnosis passed
        return PrepareResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            prepared=ready and body.sandbox_profile_hash in self._profiles,
        )

    @staticmethod
    def _containment(receipt_id: ReceiptId, record: ExecutionRecord) -> Containment:
        found = record.latest(receipt_id)
        if found is None:
            return Containment.UNKNOWN
        return Containment(found.get("containment", Containment.UNKNOWN.value))

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
        record.accept(  # synced before the run
            body,
            started_ms=self._clock.monotonic_ms(),
            accepted_by=str(self._instance),
            deadline_unix_ms=int(time.time() * 1000) + body.deadline_ms,
        )
        # From here on the backend may have run something: no failure may be
        # answered as "not run" (RECORD_UNAVAILABLE is for before this point).
        try:
            containment = self.sandbox.start(body.receipt_id, body.program_ref, body.deadline_ms)
            record.update(body.receipt_id, containment=containment.value)
            state = self._observe(body.receipt_id, record)
        except Exception as exc:
            with contextlib.suppress(ExecutionRecordError):  # best effort; the reply says it
                record.update(body.receipt_id, start_error=type(exc).__name__)
            if record.broken is not None:
                self._record, self.record_error = None, record.broken
            raise _RefusedError(ipc.RefusalCode.SANDBOX_FAILURE) from exc
        return StartResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            receipt_id=body.receipt_id,
            status=StartStatus.STARTED,
            state=state,
        )

    def _observe(self, receipt_id: ReceiptId, record: ExecutionRecord) -> SandboxState:
        """The record's state, completed with what the live backend reports."""
        state = record.state(receipt_id)
        if state is not SandboxState.ACCEPTED:
            return state
        seen = self.sandbox.observe(receipt_id)
        if seen is None:
            return state  # nothing live (e.g. after a restart): stays ACCEPTED (unknown)
        if seen.state is SandboxState.FINISHED:
            if seen.exit_code is None or seen.output_ref is None or seen.wall_ms is None:
                return SandboxState.RUNNING  # not complete yet: not reported as finished
            record.finished(
                receipt_id,
                exit_code=seen.exit_code,
                output_ref=seen.output_ref,
                wall_ms=seen.wall_ms,
            )
            return SandboxState.FINISHED
        if seen.state is SandboxState.STOPPED:
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
            containment=self._containment(body.receipt_id, record),
        )

    def _stop(self, body: StopRequest, record: ExecutionRecord) -> StopResult:
        self._known(body.receipt_id, body.attempt_id, record)
        state = self._observe(body.receipt_id, record)
        live = self.sandbox.observe(body.receipt_id) is not None
        if (
            state is SandboxState.RUNNING
            and live
            and self.sandbox.stop(body.receipt_id, body.grace_ms)
        ):
            state = self._observe(body.receipt_id, record)
        return StopResult(
            request_id=body.request_id,
            attempt_id=body.attempt_id,
            state=state,
            stop_confirmed=state in NOTHING_RUNS,
            containment=self._containment(body.receipt_id, record),
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
            containment=self._containment(body.receipt_id, record),
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
        with self._lock:
            record = self._record
            if record is not None and record.broken is None:
                self._tick(record)

    def _tick(self, record: ExecutionRecord) -> None:
        now = self._clock.monotonic_ms()
        for entry in record.accepted():
            if entry["state"] != SandboxState.ACCEPTED.value:
                continue
            receipt = ReceiptId(entry["receipt_id"])
            seen = self.sandbox.observe(receipt)
            if seen is None or seen.state is not SandboxState.RUNNING:
                continue
            if entry.get("accepted_by", str(self._instance)) == str(self._instance):
                expired = now - int(entry["started_ms"]) >= int(entry["deadline_ms"])
            else:  # accepted before a restart: P2's monotonic clock restarted too
                expired = time.time() * 1000 >= int(entry["deadline_unix_ms"])
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

    def serve(
        self,
        path: Path,
        *,
        ready: Callable[[], None] | None = None,
        tick_cycle_s: float = 1.0,
    ) -> None:
        """Serve on a socket path that only the P1 and P2 users can use.

        One broker per socket: an exclusive lock on ``<socket>.lock`` is held while
        serving (released by the OS when the process dies). Only the lock holder
        removes a socket file left by a broker that was killed. The record has its
        own lock (B1A-02)."""
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
        watchdog = self.start_watchdog(tick_cycle_s)
        try:
            server.listen(8)
            server.settimeout(0.2)
            if ready is not None:
                ready()
            while True:
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                self.serve_connection(conn)
        finally:
            watchdog.stop()
            server.close()
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            os.close(lock)
