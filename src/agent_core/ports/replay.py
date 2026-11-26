"""Replay ports (WP-06; E.Z14).

- ``ReplayBlobStore``: reads a tenant's content-addressed blob, or raises
  ``ReplayBlobNotFoundError``. It never looks anywhere else (no fallback).
- ``ReplayAccessPort``: the current access decision for blobs of a tenant,
  including deletion state and key availability. It is asked before any body
  is read and again before a replayed result is returned. With no verifier,
  the answer must be a denial (``DenyAllReplayAccess``), never an allowance.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.ids import TenantId
from agent_core.types.replay import ReplayAccessDecision


@runtime_checkable
class ReplayBlobStore(Protocol):
    def get(self, tenant_id: TenantId, ref: str) -> bytes:
        """The bytes stored under ``ref`` for this tenant, or raise
        ``ReplayBlobNotFoundError``."""
        ...


@runtime_checkable
class ReplayAccessPort(Protocol):
    def check(
        self, tenant_id: TenantId, refs: tuple[str, ...], key_version: int
    ) -> ReplayAccessDecision:
        """Current permission, deletion state and key availability for ``refs``."""
        ...
