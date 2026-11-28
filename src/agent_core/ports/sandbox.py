"""Sandbox port (WP2-01 types, WP2-02 implementation): P1's view of P2.

Implemented in P1 by the broker client (``agent_core.adapters.broker_client``),
which talks to the P2 broker over AF_UNIX; P1 never holds the Docker API.
Every method may raise ``SandboxUnavailableError`` (no answer, refused
authentication, unusable execution record): the caller then treats a start as
not done (before it was sent) or its outcome as unknown (after), never as done.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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


@runtime_checkable
class SandboxPort(Protocol):
    def prepare(self, request: PrepareRequest) -> PrepareResult: ...

    def start(self, request: StartRequest) -> StartResult: ...

    def status(self, request: StatusRequest) -> StatusResult: ...

    def stop(self, request: StopRequest) -> StopResult: ...

    def collect(self, request: CollectRequest) -> CollectResult: ...

    def reap(self, request: ReapRequest) -> ReapResult: ...
