"""P1-P2 IPC messages (WP2-02; sandbox design 7.6, 7.6.1). Pure: no socket here.

Shared by the P1 client (``agent_core.adapters.broker_client``) and the P2
broker (``agent_core_broker``), so the message types live in one place.

- One request and one response per connection, each a frame: 4-byte big-endian
  length, then UTF-8 JSON. At most ``MAX_MESSAGE_BYTES``.
- Request: ``version``, ``kind`` (fixed set), ``request_id``, ``attempt_id``,
  ``owner_epoch``, ``body`` (the typed request of ``agent_core.types.sandbox``)
  and ``mac``. Response: the same ``kind``, ``request_id`` and ``attempt_id``,
  ``broker_instance_id``, ``ok``, ``refusal`` or ``body``, and ``mac``.
- MAC: HMAC-SHA256 with the broker key (P1 and P2 only) over a domain, the
  direction and the canonical JSON of every other field. Compared in constant
  time. Only identifiers, digests and small numbers: no file content, no secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import JsonValue, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import AttemptId, BrokerInstanceId, RequestId
from agent_core.types.safety import SecretKey
from agent_core.types.serialization import StrictJsonError, parse_strict_json
from agent_core.types.values import Epoch

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
_HEADER = struct.Struct(">I")
_DOMAIN = b"agent-core/p1-p2/v1\x00"


class IpcFormatError(ValueError):
    """A frame or message that is not a valid P1-P2 message."""


class IpcAuthenticationError(ValueError):
    """A message whose MAC does not verify."""


class RequestKind(StrEnum):
    PREPARE = "PREPARE"
    START = "START"
    STATUS = "STATUS"
    STOP = "STOP"
    COLLECT = "COLLECT"
    REAP = "REAP"
    SELF_DIAGNOSE = "SELF_DIAGNOSE"


class RefusalCode(StrEnum):
    MALFORMED = "MALFORMED"
    AUTHENTICATION = "AUTHENTICATION"
    PEER = "PEER"
    STALE_EPOCH = "STALE_EPOCH"
    RECORD_UNAVAILABLE = "RECORD_UNAVAILABLE"
    UNKNOWN_RECEIPT = "UNKNOWN_RECEIPT"
    NOT_FINISHED = "NOT_FINISHED"
    PROFILE_REFUSED = "PROFILE_REFUSED"
    PROGRAM_REFUSED = "PROGRAM_REFUSED"
    SANDBOX_FAILURE = "SANDBOX_FAILURE"


class BrokerRequest(FrozenModel):
    version: Literal[1]
    kind: RequestKind
    request_id: RequestId
    attempt_id: AttemptId | None
    owner_epoch: Epoch
    body: dict[str, JsonValue]
    mac: str


class BrokerResponse(FrozenModel):
    """``kind`` and ``request_id`` are None only when the request could not be read."""

    version: Literal[1]
    kind: RequestKind | None
    request_id: RequestId | None
    attempt_id: AttemptId | None
    broker_instance_id: BrokerInstanceId
    ok: bool
    refusal: RefusalCode | None
    body: dict[str, JsonValue] | None
    mac: str

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.ok != (self.refusal is None) or self.ok != (self.body is not None):
            raise ValueError("a response has a body exactly when it is not refused")
        return self


def _canonical(data: dict[str, Any]) -> bytes:
    fields = {k: v for k, v in data.items() if k != "mac"}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _mac(key: SecretKey, direction: bytes, data: dict[str, Any]) -> str:
    return hmac.new(
        key.reveal(), _DOMAIN + direction + _canonical(data), hashlib.sha256
    ).hexdigest()


def unsigned_request(
    *,
    kind: RequestKind,
    request_id: RequestId,
    attempt_id: AttemptId | None,
    owner_epoch: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "kind": kind.value,
        "request_id": str(request_id),
        "attempt_id": None if attempt_id is None else str(attempt_id),
        "owner_epoch": owner_epoch,
        "body": json.loads(json.dumps(body, default=str)),
    }


def sign_request(data: dict[str, Any], key: SecretKey) -> dict[str, Any]:
    return {**data, "mac": _mac(key, b"request\x00", data)}


def sign_response(data: dict[str, Any], key: SecretKey) -> dict[str, Any]:
    return {**data, "mac": _mac(key, b"response\x00", data)}


def encode_request(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _parse(data: bytes) -> dict[str, Any]:
    try:
        parsed = parse_strict_json(data)
    except (StrictJsonError, UnicodeDecodeError, ValueError) as exc:
        raise IpcFormatError("not a strict JSON message") from exc
    if not isinstance(parsed, dict):
        raise IpcFormatError("a message is a JSON object")
    return parsed


def _verify(parsed: dict[str, Any], key: SecretKey, direction: bytes) -> None:
    mac = parsed.get("mac")
    if not isinstance(mac, str) or not hmac.compare_digest(mac, _mac(key, direction, parsed)):
        raise IpcAuthenticationError("the message MAC does not verify")


def decode_request(data: bytes, key: SecretKey) -> BrokerRequest:
    """Parse, check the MAC, then validate. Raises IpcFormatError or
    IpcAuthenticationError; never returns an unauthenticated request."""
    parsed = _parse(data)
    _verify(parsed, key, b"request\x00")
    try:
        return BrokerRequest.model_validate_json(data)
    except ValueError as exc:
        raise IpcFormatError("the request does not match its schema") from exc


def decode_response(data: bytes, key: SecretKey) -> BrokerResponse:
    parsed = _parse(data)
    _verify(parsed, key, b"response\x00")
    try:
        return BrokerResponse.model_validate_json(data)
    except ValueError as exc:
        raise IpcFormatError("the response does not match its schema") from exc


def frame(payload: bytes) -> bytes:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise IpcFormatError("message too large")
    return _HEADER.pack(len(payload)) + payload


def unframe(data: bytes) -> bytes:
    if len(data) < _HEADER.size:
        raise IpcFormatError("frame too short")
    (length,) = _HEADER.unpack_from(data)
    if length > MAX_MESSAGE_BYTES:
        raise IpcFormatError("message too large")
    if len(data) != _HEADER.size + length:
        raise IpcFormatError("frame length does not match")
    return data[_HEADER.size :]


def read_frame(sock: Any) -> bytes:
    """Read one whole frame (header and payload) from a stream socket."""
    header = _read_exactly(sock, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length > MAX_MESSAGE_BYTES:
        raise IpcFormatError("message too large")
    return header + _read_exactly(sock, length)


def _read_exactly(sock: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(min(remaining, 65_536))
        if not chunk:
            raise IpcFormatError("connection closed before the message ended")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
