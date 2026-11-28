"""Minimal Docker Engine API client over AF_UNIX (WP2-03; D17 import exception).

The only place in Agent Core that talks to Docker. P2 only (import contracts);
P1 has no path to it. Standard library only: ``http.client`` over a Unix
stream socket to the daemon's socket path. No TCP, no TLS, no other host.

Every call has a timeout (B1A-05): a slow or stuck daemon raises, it never
blocks the broker indefinitely. The API version is fixed (``API_VERSION``) and
checked against the daemon's supported range before use.
"""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any

API_VERSION = "1.44"
DEFAULT_SOCKET = Path("/var/run/docker.sock")
DEFAULT_TIMEOUT_S = 10.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class DockerError(Exception):
    """The daemon could not be used or answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("docker", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(str(self._path))
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class DockerAPI:
    def __init__(
        self, path: Path = DEFAULT_SOCKET, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        if not path.is_absolute():
            raise ValueError("the Docker socket path must be absolute")
        self._path = path
        self._timeout = timeout_s

    def raw(
        self,
        method: str,
        endpoint: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        timeout_s: float | None = None,
        versioned: bool = True,
    ) -> tuple[int, bytes]:
        url = f"/v{API_VERSION}{endpoint}" if versioned else endpoint
        conn = _UnixConnection(self._path, timeout_s or self._timeout)
        try:
            headers = {"Content-Type": content_type} if body is not None else {}
            conn.request(method, url, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise DockerError(f"the Docker daemon is not reachable: {type(exc).__name__}") from exc
        finally:
            conn.close()
        if len(data) > MAX_RESPONSE_BYTES:
            raise DockerError("the Docker answer is too large")
        return response.status, data

    def call(
        self,
        method: str,
        endpoint: str,
        payload: Any = None,
        *,
        ok: tuple[int, ...] = (200, 201, 204, 304),
        timeout_s: float | None = None,
        versioned: bool = True,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        status, data = self.raw(
            method, endpoint, body=body, timeout_s=timeout_s, versioned=versioned
        )
        if status not in ok:
            raise DockerError(f"Docker {method} {endpoint.split('?')[0]} answered {status}", status)
        if not data:
            return None
        try:
            return json.loads(data)
        except ValueError as exc:
            raise DockerError("the Docker answer is not JSON") from exc

    def version(self) -> dict[str, Any]:
        found = self.call("GET", "/version", versioned=False)
        if not isinstance(found, dict):
            raise DockerError("unexpected /version answer")
        return found

    def info(self) -> dict[str, Any]:
        found = self.call("GET", "/info")
        if not isinstance(found, dict):
            raise DockerError("unexpected /info answer")
        return found

    @staticmethod
    def q(value: str) -> str:
        """Percent-encode a path or query value (no urllib: one import exception only)."""
        safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return "".join(c if c in safe else "".join(f"%{b:02X}" for b in c.encode()) for c in value)
