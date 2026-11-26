"""The autouse guard in tests/conftest.py blocks network access in every test."""

from __future__ import annotations

import socket

import pytest

BLOCKED = "network access is blocked"


def test_tcp_connection_is_refused() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_raw_socket_connect_is_refused() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(RuntimeError, match=BLOCKED),
    ):
        sock.connect(("127.0.0.1", 9))


def test_name_resolution_is_refused() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.getaddrinfo("example.invalid", 443)
