"""Journal port: the basic contract only (PROVISIONAL; WP-03 implements and may refine it).

Contract (E_INTEGRATION 2.1-2.3, A 2):

- Only P1 (Runtime Core) writes. ``append`` assigns ``tenant_seq`` (and
  ``run_seq`` when the draft has a ``run_id``) gaplessly, sets ``prev_hash``
  to the previous event's hash, and stores the event, all in one transaction.
- A duplicate ``event_id`` raises ``DuplicateIdError`` and stores nothing,
  so the caller can retry with a new ID (``IdGenerator.issue_unique``).
- Reads return events in ``tenant_seq`` order.
- The event hash, storage schema and transaction details are WP-03.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.base import FrozenModel
from agent_core.types.ids import TenantId
from agent_core.types.records import Event, EventDraft
from agent_core.types.values import Digest, Seq


class JournalHead(FrozenModel):
    """The last position of one tenant's journal."""

    tenant_id: TenantId
    tenant_seq: Seq
    head_hash: Digest


@runtime_checkable
class JournalPort(Protocol):
    def append(self, draft: EventDraft) -> Event:
        """Store the draft at the next position, or raise ``DuplicateIdError``."""
        ...

    def read_tenant(
        self, tenant_id: TenantId, *, after_tenant_seq: int, limit: int
    ) -> tuple[Event, ...]:
        """Events with ``tenant_seq > after_tenant_seq``, at most ``limit``, in order."""
        ...

    def head(self, tenant_id: TenantId) -> JournalHead | None:
        """The last position, or None when the tenant has no events."""
        ...
