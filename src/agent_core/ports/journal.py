"""Journal port (WP-03). Implemented by ``agent_core.adapters.sqlite_journal``.

Contract (E_INTEGRATION 2.1-2.3, A 2):

- Only P1 (Runtime Core) writes. ``append`` assigns ``tenant_seq`` (and
  ``run_seq`` when the draft has a ``run_id``) gaplessly, sets ``prev_hash``
  to the previous event's hash, and stores the event, all in one transaction.
- A duplicate ``event_id`` raises ``DuplicateIdError`` and stores nothing,
  so the caller can retry with a new ID (``IdGenerator.issue_unique``).
- Every append names the head its caller's state was built from
  (``HeadExpectation``: ``tenant_seq`` and ``head_hash``). The journal compares
  it with the actual head inside the write transaction, before storing
  anything; a mismatch raises ``JournalHeadMismatchError`` and stores nothing.
  There is no append without an expectation (WP04-R01).
- Reads return events in ``tenant_seq`` order.
- The event hash is ``agent_core.types.event_hash`` (version 1). Storage schema
  and transactions are in the SQLite adapter (docs/design/phase-1/WP-03_JOURNAL.md).
"""

from __future__ import annotations

from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.events import Event, EventDraft
from agent_core.types.ids import RunId, TenantId
from agent_core.types.ledger import LedgerOp
from agent_core.types.values import MAX_INT64, Digest, Seq


class JournalHead(FrozenModel):
    """The last position of one tenant's journal."""

    tenant_id: TenantId
    tenant_seq: Seq
    head_hash: Digest


class HeadExpectation(FrozenModel):
    """The head a caller's state was built from. ``tenant_seq`` 0 means no events."""

    tenant_seq: Annotated[int, Field(strict=True, ge=0, le=MAX_INT64)]
    head_hash: Digest | None

    @model_validator(mode="after")
    def _empty_has_no_hash(self) -> Self:
        if (self.tenant_seq == 0) != (self.head_hash is None):
            raise ValueError("only an empty journal (tenant_seq 0) has no head hash")
        return self

    @classmethod
    def of(cls, head: JournalHead | None) -> HeadExpectation:
        if head is None:
            return cls(tenant_seq=0, head_hash=None)
        return cls(tenant_seq=head.tenant_seq, head_hash=head.head_hash)


@runtime_checkable
class JournalPort(Protocol):
    @property
    def owner_epoch(self) -> int:
        """The current owner epoch; every draft must carry it."""
        ...

    def append(self, draft: EventDraft, *, expected: HeadExpectation) -> Event:
        """Store the draft at the next position if the head is ``expected``.

        Raises ``JournalHeadMismatchError`` or ``DuplicateIdError`` (nothing stored)."""
        ...

    def append_with_ledger(
        self, draft: EventDraft, *, expected: HeadExpectation, ledger: tuple[LedgerOp, ...]
    ) -> Event:
        """``append`` plus ledger operations in the same transaction (WP-09).

        The operations must pair with the draft's runtime transitions. A journal
        without a ledger raises instead of storing the event alone."""
        ...

    def read_tenant(
        self, tenant_id: TenantId, *, after_tenant_seq: int, limit: int
    ) -> tuple[Event, ...]:
        """Events with ``tenant_seq > after_tenant_seq``, at most ``limit``, in order."""
        ...

    def read_run(self, run_id: RunId, *, after_run_seq: int, limit: int) -> tuple[Event, ...]:
        """Events of one run with ``run_seq > after_run_seq``, at most ``limit``, in order."""
        ...

    def head(self, tenant_id: TenantId) -> JournalHead | None:
        """The last position, or None when the tenant has no events."""
        ...


@runtime_checkable
class JournalVerifier(Protocol):
    def verify_tenant(self, tenant_id: TenantId) -> JournalHead | None:
        """Re-check the tenant's whole journal; raise ``JournalCorruptionError`` on any
        mismatch. Returns the verified head, or None when there are no events."""
        ...
