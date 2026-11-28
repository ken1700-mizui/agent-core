"""P2 <-> Deadline Supervisor messages: length-framed JSON with an HMAC per
direction (domain separated from P1 <-> P2), over AF_UNIX with SO_PEERCRED."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import struct
from typing import Any

from agent_core.types.broker_ipc import frame, read_frame, unframe

_DOMAIN = b"agent-core/p2-ds/v1\x00"
_PEERCRED = struct.Struct("3i")


class DsIpcError(ValueError):
    pass


def _mac(key: bytes, direction: bytes, data: dict[str, Any]) -> str:
    body = json.dumps({k: v for k, v in data.items() if k != "mac"}, sort_keys=True)
    return hmac.new(key, _DOMAIN + direction + body.encode(), hashlib.sha256).hexdigest()


def seal(key: bytes, direction: bytes, data: dict[str, Any]) -> bytes:
    signed = dict(data, mac=_mac(key, direction, data))
    return frame(json.dumps(signed, sort_keys=True).encode())


def open_sealed(key: bytes, direction: bytes, raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DsIpcError("MALFORMED") from exc
    if not isinstance(data, dict) or not isinstance(data.get("mac"), str):
        raise DsIpcError("MALFORMED")
    if not hmac.compare_digest(data["mac"], _mac(key, direction, data)):
        raise DsIpcError("AUTHENTICATION")
    return data


def read_payload(sock: socket.socket) -> bytes:
    return unframe(read_frame(sock))


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    return int(_PEERCRED.unpack(creds)[1])


REQUEST = b"request\x00"
RESPONSE = b"response\x00"
__all__ = [
    "REQUEST",
    "RESPONSE",
    "DsIpcError",
    "open_sealed",
    "peer_uid",
    "read_frame",
    "read_payload",
    "seal",
]
