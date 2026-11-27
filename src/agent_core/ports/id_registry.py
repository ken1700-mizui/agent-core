"""ID uniqueness port.

``register`` raises ``DuplicateIdError`` when the ID already exists. The durable
implementation is the Journal's UNIQUE constraint inside P1's transaction
(WP-03, E_INTEGRATION 2.2). An in-memory check proves nothing across restarts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.ids import EntityId


@runtime_checkable
class IdRegistry(Protocol):
    def register(self, entity_id: EntityId) -> None:
        """Record ``entity_id`` or raise ``DuplicateIdError``."""
        ...
