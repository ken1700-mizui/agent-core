"""The Deadline Supervisor's socket: only P2's OS user, only MAC'd requests."""

from __future__ import annotations

import json
import socket
from typing import Any

from pydantic import ValidationError

from agent_core.types.ids import ReceiptId
from agent_core.types.supervisor import DsRefusal, Registration
from agent_core_supervisor.ipc import (
    REQUEST,
    RESPONSE,
    DsIpcError,
    open_sealed,
    peer_uid,
    read_payload,
    seal,
)
from agent_core_supervisor.supervisor import DsRefusedError, Supervisor


class SupervisorServer:
    def __init__(self, supervisor: Supervisor, *, key: bytes, p2_uid: int) -> None:
        self._ds = supervisor
        self._key = key
        self._p2_uid = p2_uid

    def _answer(self, request_id: Any, **body: Any) -> bytes:
        return seal(self._key, RESPONSE, {"request_id": request_id, **body})

    def handle(self, raw: bytes, uid: int) -> bytes:
        if uid != self._p2_uid:
            return self._answer(None, ok=False, refusal=DsRefusal.PEER.value)
        try:
            data = open_sealed(self._key, REQUEST, raw)
        except DsIpcError as exc:
            return self._answer(None, ok=False, refusal=str(exc))
        rid = data.get("request_id")
        try:
            kind = data.get("kind")
            if kind == "status":
                return self._answer(rid, ok=True, body=self._ds.status().model_dump(mode="json"))
            if kind == "register":
                view = self._ds.register(
                    Registration.model_validate_json(json.dumps(data.get("body")))
                )
            elif kind in ("confirm_start", "started", "release"):
                receipt = ReceiptId(str(data.get("receipt_id")))
                view = getattr(self._ds, kind)(receipt)
            else:
                return self._answer(rid, ok=False, refusal=DsRefusal.MALFORMED.value)
        except DsRefusedError as exc:
            return self._answer(rid, ok=False, refusal=exc.code.value, detail=str(exc))
        except (ValidationError, ValueError):
            return self._answer(rid, ok=False, refusal=DsRefusal.MALFORMED.value)
        return self._answer(rid, ok=True, body=view.model_dump(mode="json"))

    def serve_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                uid = peer_uid(conn)
                conn.sendall(self.handle(read_payload(conn), uid))
            except (OSError, ValueError):
                return
