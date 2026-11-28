"""P1's broker client: the ``SandboxPort`` over AF_UNIX (WP2-02; S-5, D16).

The only module of Agent Core allowed to use ``socket`` (import contract
exception, AF_UNIX only). It opens Unix stream sockets to the P2 broker and
nothing else: no internet family, no name resolution.

Every call is one connection: check that the peer is the P2 user
(``SO_PEERCRED``) before sending; send one MAC'd request; read one response;
verify its MAC, kind, ``request_id`` and ``attempt_id`` (and the Receipt where
there is one). Any failure raises ``SandboxUnavailableError``; ``sent`` tells
whether the request may have reached P2 (then a start's outcome is unknown).
P1 never learns more than the verified answer says: a refused start is not
done, a stop request is not a confirmed stop.
"""

from __future__ import annotations

import json
import socket
import struct
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_core.types import broker_ipc as ipc
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import AttemptId, RequestId
from agent_core.types.safety import SecretKey
from agent_core.types.sandbox import (
    CollectRequest,
    CollectResult,
    PrepareRequest,
    PrepareResult,
    ReapRequest,
    ReapResult,
    StartRequest,
    StartResult,
    StatusRequest,
    StatusResult,
    StopRequest,
    StopResult,
)

_PEERCRED = struct.Struct("3i")
R = TypeVar("R", bound=BaseModel)
Connector = Callable[[], socket.socket]


def unix_connector(path: Path, *, timeout_s: float = 5.0) -> Connector:
    """Connects to the broker socket at an absolute path (AF_UNIX only)."""
    if not path.is_absolute():
        raise ValueError("the broker socket path must be absolute")

    def connect() -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect(str(path))
        except BaseException:
            sock.close()
            raise
        return sock

    return connect


def _peer_uid(sock: socket.socket) -> int:
    _pid, uid, _gid = _PEERCRED.unpack(
        sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    )
    return int(uid)


class BrokerClient:
    def __init__(
        self,
        *,
        connector: Connector,
        key: SecretKey,
        p2_uid: int,
        owner_epoch: Callable[[], int],
        timeout_s: float = 5.0,
    ) -> None:
        self._connect = connector
        self._key = key
        self._p2_uid = p2_uid
        self._epoch = owner_epoch
        self._timeout = timeout_s

    def _call(
        self,
        kind: ipc.RequestKind,
        request: BaseModel,
        request_id: RequestId,
        attempt_id: AttemptId,
        result_type: type[R],
    ) -> R:
        data = ipc.sign_request(
            ipc.unsigned_request(
                kind=kind,
                request_id=request_id,
                attempt_id=attempt_id,
                owner_epoch=self._epoch(),
                body=json.loads(request.model_dump_json()),
            ),
            self._key,
        )
        try:
            sock = self._connect()
        except OSError as exc:
            raise SandboxUnavailableError("the broker cannot be reached", sent=False) from exc
        sent = False
        try:
            sock.settimeout(self._timeout)
            if sock.family != socket.AF_UNIX:
                raise SandboxUnavailableError("the broker connection is not AF_UNIX", sent=False)
            if _peer_uid(sock) != self._p2_uid:
                raise SandboxUnavailableError("the broker peer is not the P2 user", sent=False)
            sent = True
            sock.sendall(ipc.frame(ipc.encode_request(data)))
            response = ipc.decode_response(ipc.unframe(ipc.read_frame(sock)), self._key)
        except (OSError, ipc.IpcFormatError, ipc.IpcAuthenticationError) as exc:
            raise SandboxUnavailableError("no valid answer from the broker", sent=sent) from exc
        finally:
            sock.close()
        if not response.ok or response.body is None:
            # A verified refusal on this connection: nothing was done for it.
            refusal = None if response.refusal is None else response.refusal.value
            raise SandboxUnavailableError(
                "the broker refused the request", sent=True, refusal=refusal
            )
        if (response.kind, response.request_id, response.attempt_id) != (
            kind,
            request_id,
            attempt_id,
        ):
            raise SandboxUnavailableError("the broker's answer is for another request", sent=True)
        try:
            result = result_type.model_validate_json(json.dumps(response.body))
        except ValidationError as exc:
            raise SandboxUnavailableError("the broker's answer is malformed", sent=True) from exc
        same = getattr(result, "request_id", None) == request_id and (
            getattr(result, "attempt_id", None) == attempt_id
        )
        receipt = getattr(request, "receipt_id", None)
        if not same or (receipt is not None and getattr(result, "receipt_id", receipt) != receipt):
            raise SandboxUnavailableError("the broker's answer names another request", sent=True)
        return result

    def prepare(self, request: PrepareRequest) -> PrepareResult:
        return self._call(
            ipc.RequestKind.PREPARE, request, request.request_id, request.attempt_id, PrepareResult
        )

    def start(self, request: StartRequest) -> StartResult:
        return self._call(
            ipc.RequestKind.START, request, request.request_id, request.attempt_id, StartResult
        )

    def status(self, request: StatusRequest) -> StatusResult:
        return self._call(
            ipc.RequestKind.STATUS, request, request.request_id, request.attempt_id, StatusResult
        )

    def stop(self, request: StopRequest) -> StopResult:
        return self._call(
            ipc.RequestKind.STOP, request, request.request_id, request.attempt_id, StopResult
        )

    def collect(self, request: CollectRequest) -> CollectResult:
        return self._call(
            ipc.RequestKind.COLLECT, request, request.request_id, request.attempt_id, CollectResult
        )

    def reap(self, request: ReapRequest) -> ReapResult:
        return self._call(
            ipc.RequestKind.REAP, request, request.request_id, request.attempt_id, ReapResult
        )
