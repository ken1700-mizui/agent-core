"""P2's client of the Deadline Supervisor (a ``SupervisorPort``)."""

from __future__ import annotations

import json
import secrets
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from agent_core.types.ids import ReceiptId
from agent_core.types.supervisor import DsRefusal, DsStatus, Registration, TargetView
from agent_core_supervisor.ipc import (
    REQUEST,
    RESPONSE,
    DsIpcError,
    open_sealed,
    peer_uid,
    read_payload,
    seal,
)
from agent_core_supervisor.supervisor import DsRefusedError


class SupervisorPort(Protocol):
    def status(self) -> DsStatus: ...

    def register(self, reg: Registration) -> TargetView: ...

    def confirm_start(self, receipt: ReceiptId) -> TargetView: ...

    def started(self, receipt: ReceiptId) -> TargetView: ...

    def release(self, receipt: ReceiptId) -> TargetView: ...


def _json(body: dict[str, Any]) -> str:
    return json.dumps(body)


class DsUnavailableError(Exception):
    """The supervisor could not be reached or answered wrongly: nothing starts."""


def unix_connector(path: Path, *, timeout_s: float = 5.0) -> Callable[[], socket.socket]:
    if not path.is_absolute():
        raise ValueError("the supervisor socket path must be absolute")

    def connect() -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        s.connect(str(path))
        return s

    return connect


class SupervisorClient:
    def __init__(self, *, connector: Callable[[], socket.socket], key: bytes, ds_uid: int) -> None:
        self._connect = connector
        self._key = key
        self._ds_uid = ds_uid

    def _call(self, kind: str, **fields: Any) -> dict[str, Any]:
        rid = secrets.token_hex(8)
        try:
            with self._connect() as s:
                if peer_uid(s) != self._ds_uid:
                    raise DsUnavailableError("the peer is not the supervisor's user")
                s.sendall(seal(self._key, REQUEST, {"request_id": rid, "kind": kind, **fields}))
                answer = open_sealed(self._key, RESPONSE, read_payload(s))
        except (OSError, ValueError, DsIpcError) as exc:
            raise DsUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        early = answer.get("request_id") is None and not answer.get("ok")
        if answer.get("request_id") != rid and not early:
            raise DsUnavailableError("the answer is for another request")
        if not answer.get("ok"):
            raise DsRefusedError(
                DsRefusal(str(answer.get("refusal"))), str(answer.get("detail", ""))
            )
        body = answer.get("body")
        if not isinstance(body, dict):
            raise DsUnavailableError("the answer has no body")
        return body

    def status(self) -> DsStatus:
        return DsStatus.model_validate_json(_json(self._call("status")))

    def register(self, reg: Registration) -> TargetView:
        return TargetView.model_validate_json(
            _json(self._call("register", body=reg.model_dump(mode="json")))
        )

    def confirm_start(self, receipt: ReceiptId) -> TargetView:
        return TargetView.model_validate_json(
            _json(self._call("confirm_start", receipt_id=str(receipt)))
        )

    def started(self, receipt: ReceiptId) -> TargetView:
        return TargetView.model_validate_json(_json(self._call("started", receipt_id=str(receipt))))

    def release(self, receipt: ReceiptId) -> TargetView:
        return TargetView.model_validate_json(_json(self._call("release", receipt_id=str(receipt))))
