"""File operation port (WP-08): what the runtime needs from the file broker.

``prepare`` runs before the start (before any receipt is claimed). It checks S19,
the policy's trust, the managed area, the tree's root and its signed manifest,
and takes the single-writer lock. It refuses with ``WorkspaceRejectedError`` or
``FileOperationRefusedError`` and never opens a target.

The gate itself calls the prepared operation (``execute_file``, WP08-R06) for the
one file operation; the broker redeems the execution ticket (``redeem`` is the
gate's) and returns a ``FileOutcome`` that the gate checks against the redeemed
binding before anything is recorded as done. A failure is a
``FileOperationFailedError`` whose ``effect`` says whether a target may have
changed. ``close`` releases the fds and the lock.

Implemented by ``agent_core.adapters.file_broker``; only the composition root
builds one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from agent_core.types.files import ExecutionTicket, FileOperation, FileOutcome, TicketBinding
from agent_core.types.safety import ActionDescriptor, DispatchAuthorization, Policy

Redeem = Callable[[ExecutionTicket, FileOperation], TicketBinding]


@runtime_checkable
class PreparedFileOperation(Protocol):
    @property
    def workspace_ref(self) -> str: ...

    def read(
        self, authorization: DispatchAuthorization, ticket: ExecutionTicket, redeem: Redeem
    ) -> tuple[FileOutcome, bytes]: ...

    def write(
        self,
        authorization: DispatchAuthorization,
        ticket: ExecutionTicket,
        redeem: Redeem,
        content: bytes,
    ) -> FileOutcome: ...

    def close(self) -> None: ...


@runtime_checkable
class FileOperationPort(Protocol):
    def prepare(
        self, descriptor: ActionDescriptor, policy: Policy | None
    ) -> PreparedFileOperation: ...
