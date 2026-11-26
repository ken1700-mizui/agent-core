"""File operation port (WP-08): what the runtime needs from the file broker.

The broker checks the work tree before the start (``preflight``) and performs the
one file operation inside the gate callback, redeeming the execution ticket for
exactly that operation (``redeem`` is the gate's). Implemented by
``agent_core.adapters.file_broker``; only the composition root builds one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from agent_core.types.files import ExecutionTicket, FileOperation, TicketBinding
from agent_core.types.safety import ActionDescriptor, DispatchAuthorization, Policy

Redeem = Callable[[ExecutionTicket, FileOperation], TicketBinding]


@runtime_checkable
class FileOperationPort(Protocol):
    def preflight(self, descriptor: ActionDescriptor, policy: Policy | None) -> None:
        """Refuse (WorkspaceRejectedError, FileOperationRefusedError) before the
        start, without opening any target."""
        ...

    def read(
        self, authorization: DispatchAuthorization, ticket: ExecutionTicket, redeem: Redeem
    ) -> bytes: ...

    def write(
        self,
        authorization: DispatchAuthorization,
        ticket: ExecutionTicket,
        redeem: Redeem,
        content: bytes,
    ) -> None: ...
