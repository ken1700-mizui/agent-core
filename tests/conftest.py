"""Test-wide guards.

Phase 1 tests use synthetic data only and must never reach the network
(PHASE_1_IMPLEMENTATION_PLAN.md section 2).

Scope of this guard (it is not OS-level isolation):

- It patches the Python ``socket`` module inside the pytest process. Creating
  any non-AF_UNIX socket (TCP, UDP, raw, IPv4 or IPv6) is refused, and so are
  connect, sendto, sendmsg, bind and name resolution on the patched paths.
- It does not cover child processes, C extensions that open sockets
  directly, or code that uses the low-level ``_socket`` module. For
  ``agent_core`` those paths are forbidden statically (import-linter contract
  and tests/unit/test_no_process_execution.py).
"""

from __future__ import annotations

import os
import socket
from typing import Any, NoReturn

import pytest

NETWORK_BLOCKED_MESSAGE = "network access is blocked in Agent Core tests"

_REFUSED_SOCKET_METHODS = ("connect", "connect_ex", "sendto", "sendmsg", "bind")
_REFUSED_MODULE_FUNCTIONS = (
    "create_connection",
    "create_server",
    "getaddrinfo",
    "gethostbyname",
    "gethostbyname_ex",
    "gethostbyaddr",
    "getnameinfo",
    "fromfd",
)


def _refuse(*_args: object, **_kwargs: object) -> NoReturn:
    raise RuntimeError(NETWORK_BLOCKED_MESSAGE)


_original_socket_init = socket.socket.__init__


def _guarded_socket_init(self: socket.socket, family: Any = -1, *args: Any, **kwargs: Any) -> None:
    # family=-1 means "default" (AF_INET) or "taken from fileno": refuse both.
    if family != socket.AF_UNIX:
        _refuse()
    _original_socket_init(self, family, *args, **kwargs)


_DOCKER_SOCKET = "/var/run/docker.sock"
_original_connect = socket.socket.connect


def _docker_socket_only(self: socket.socket, address: Any) -> None:
    """``sandbox`` tests (D17, E-1): an AF_UNIX connect to the Docker daemon's
    socket only. Every other connect stays refused."""
    if self.family == socket.AF_UNIX and address == _DOCKER_SOCKET:
        _original_connect(self, address)
        return
    _refuse()


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch, request: Any) -> None:
    monkeypatch.setattr(socket.socket, "__init__", _guarded_socket_init)
    for method in _REFUSED_SOCKET_METHODS:
        monkeypatch.setattr(socket.socket, method, _refuse)
    if request.node.get_closest_marker("sandbox") is not None:
        monkeypatch.setattr(socket.socket, "connect", _docker_socket_only)
    for function in _REFUSED_MODULE_FUNCTIONS:
        monkeypatch.setattr(socket, function, _refuse)


def pytest_runtest_setup(item: Any) -> None:
    """A privileged test (``@pytest.mark.privileged(reason=...)``, see
    ``tests/support/privileged.py``) runs only as root. Without root it fails as
    ``PRIVILEGED_NOT_RUN``: never skipped, never counted as a success. Deselect
    it explicitly with ``-m "not privileged"``; run it as root with
    ``-m privileged``."""
    marker = item.get_closest_marker("privileged")
    if marker is None:
        return
    reason = marker.kwargs.get("reason")
    if not reason:
        pytest.fail("PRIVILEGED_REASON_MISSING: a privileged test states why", pytrace=False)
    if os.geteuid() != 0:
        pytest.fail(
            f"PRIVILEGED_NOT_RUN: {reason}; run as root with -m privileged, "
            "or deselect with -m 'not privileged'",
            pytrace=False,
        )


# ---------------------------------------------------------------- sandbox tests (WP2-03, D17)

SANDBOX_USERS = ("agentcore-p1", "agentcore-p2")


# Separate categories (deselected unless ``-m`` names them, so the existing CI
# command is unchanged; never counted as passed when not run): ``sandbox``
# (D17, needs E-1/E-2) and ``simulation`` (WP2-07, D 16.1: long).
SEPARATE_CATEGORIES = ("sandbox", "simulation")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    expression = config.getoption("-m") or ""
    for category in SEPARATE_CATEGORIES:
        if category in expression:
            continue
        selected: list[Any] = []
        deselected: list[Any] = []
        for item in items:
            (deselected if item.get_closest_marker(category) else selected).append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
            setattr(config, f"_{category}_not_run", len(deselected))


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    del exitstatus
    for category in SEPARATE_CATEGORIES:
        count = getattr(config, f"_{category}_not_run", 0)
        if count:
            terminalreporter.write_line(
                f"{category.upper()}_NOT_RUN: {count} {category} tests were not run"
                f" (run them with -m {category})"
            )


def _sandbox_environment_problem() -> str | None:
    import pwd

    if os.geteuid() != 0:
        return "not root"
    if not os.path.exists("/var/run/docker.sock"):
        return "no Docker daemon socket (E-1)"
    for user in SANDBOX_USERS:
        try:
            pwd.getpwnam(user)
        except KeyError:
            return f"OS user {user} missing (E-2)"
    return None


@pytest.fixture(autouse=True)
def _sandbox_environment(request: Any) -> None:
    if request.node.get_closest_marker("sandbox") is None:
        return
    problem = _sandbox_environment_problem()
    if problem is not None:
        pytest.fail(f"SANDBOX_NOT_RUN: {problem}", pytrace=False)
