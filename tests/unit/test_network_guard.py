"""The autouse guard in tests/conftest.py blocks Python-level network access.

The guard is limited to the pytest process (see tests/conftest.py). It does not
prove OS-level isolation.
"""

from __future__ import annotations

import socket

import pytest

BLOCKED = "network access is blocked"


@pytest.mark.parametrize(
    ("family", "kind"),
    [
        (socket.AF_INET, socket.SOCK_STREAM),
        (socket.AF_INET, socket.SOCK_DGRAM),
        (socket.AF_INET6, socket.SOCK_STREAM),
        (socket.AF_INET6, socket.SOCK_DGRAM),
    ],
)
def test_creating_an_internet_socket_is_refused(family: int, kind: int) -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.socket(family, kind)


def test_default_socket_is_refused() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.socket()


def test_tcp_connection_helper_is_refused() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_server_helper_is_refused() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_server(("127.0.0.1", 0))


@pytest.mark.parametrize(
    "call",
    [
        lambda: socket.getaddrinfo("example.invalid", 443),
        lambda: socket.gethostbyname("example.invalid"),
        lambda: socket.gethostbyname_ex("example.invalid"),
        lambda: socket.gethostbyaddr("127.0.0.1"),
        lambda: socket.getnameinfo(("127.0.0.1", 0), 0),
    ],
)
def test_name_resolution_is_refused(call: object) -> None:
    assert callable(call)
    with pytest.raises(RuntimeError, match=BLOCKED):
        call()


def test_unix_socket_is_still_allowed_but_cannot_send_to_an_address() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        left.sendall(b"synthetic")
        assert right.recv(16) == b"synthetic"
        with pytest.raises(RuntimeError, match=BLOCKED):
            left.sendto(b"synthetic", "/nonexistent")
        with pytest.raises(RuntimeError, match=BLOCKED):
            left.connect("/nonexistent")
