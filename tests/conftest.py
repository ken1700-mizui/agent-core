"""Test-wide guards.

Phase 1 tests use synthetic data only and must never reach the network
(PHASE_1_IMPLEMENTATION_PLAN.md section 2). Every test runs with socket
connections and name resolution blocked. Tests that need a tool run it as a
local subprocess with a fixed argument list.
"""

from __future__ import annotations

import socket
from typing import NoReturn

import pytest

NETWORK_BLOCKED_MESSAGE = "network access is blocked in Agent Core tests"


def _refuse(*_args: object, **_kwargs: object) -> NoReturn:
    raise RuntimeError(NETWORK_BLOCKED_MESSAGE)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
