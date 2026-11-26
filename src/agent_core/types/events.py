"""Journal events (E 8): EventDraft and Event.

Versions (``schema_version``):

- 1: the WP-03 format. No payload. Its serialized bytes, and therefore its
  hashes, are unchanged by later versions.
- 2: RETIRED. Written by the WP-04 draft (9f1a799) and then, with a different
  Run shape (``cancel_requested`` added), by 635e0b3: one number, two meanings.
  It cannot be restored safely (the transition rules also changed), so it is
  rejected with ``superseded_schema_version``; it is never reinterpreted.
  Only synthetic development data was ever written in it.
- 3: adds ``payload``: a typed ``RuntimePayload`` of state transitions (WP-04
  with WP04-R04: Run carries ``cancel_requested``). Required in version 3.
  Still no content body or secret.

Unknown versions are rejected. Readers never reinterpret one version as another.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import (
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from agent_core.types.base import ImmutableRecord
from agent_core.types.ids import EventId, RunId, TenantId
from agent_core.types.transitions import RuntimePayload
from agent_core.types.values import (
    Digest,
    EngineVersion,
    Epoch,
    Identifier,
    KeyedHash,
    Label,
    PolicyVersion,
    Seq,
    TimestampMs,
    require_sorted_unique,
)

# Event schema versions this code can read. Unknown versions are rejected.
SUPPORTED_EVENT_SCHEMA_VERSIONS: frozenset[int] = frozenset({1, 3})
# Versions that existed but can no longer be read (see the module docstring).
RETIRED_EVENT_SCHEMA_VERSIONS: frozenset[int] = frozenset({2})


def _invalid(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, message)


def _check_schema_version(value: object) -> int:
    if type(value) is int and value in RETIRED_EVENT_SCHEMA_VERSIONS:
        raise _invalid(
            "superseded_schema_version",
            "event schema_version 2 is retired (WP-04 draft format); it cannot be restored",
        )
    if type(value) is not int or value not in SUPPORTED_EVENT_SCHEMA_VERSIONS:
        raise _invalid("unsupported_schema_version", "unsupported event schema_version")
    return value


class _EventFields(ImmutableRecord):
    """Fields shared by EventDraft and Event."""

    event_id: EventId
    type: Identifier
    schema_version: int
    tenant_id: TenantId
    run_id: RunId | None
    engine: EngineVersion
    policy_version: PolicyVersion
    access_epoch: Epoch
    owner_epoch: Epoch
    revocation_gen: Epoch
    labels: tuple[Label, ...]
    refs: tuple[Digest, ...]
    keyed_hashes: tuple[KeyedHash, ...]
    # A 2 lists ``ts``; it records time for people and never orders events.
    recorded_at_ms: TimestampMs
    # Typed runtime metadata (schema_version 3 only). Absent in version 1, and then
    # also absent from the serialized form, so version 1 bytes and hashes are unchanged.
    payload: RuntimePayload | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_payload(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if self.payload is None and isinstance(data, dict):
            data.pop("payload", None)
        return data

    @model_validator(mode="after")
    def _payload_matches_version(self) -> Self:
        if self.schema_version == 1 and self.payload is not None:
            raise _invalid("payload_not_allowed", "schema_version 1 events carry no payload")
        if self.schema_version == 3 and self.payload is None:
            raise _invalid("payload_required", "schema_version 3 events carry a payload")
        return self

    @field_validator("schema_version", mode="plain")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _check_schema_version(value)

    @field_validator("labels", "refs")
    @classmethod
    def _canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_sorted_unique(value, "items")
        return value

    @field_validator("keyed_hashes")
    @classmethod
    def _keyed_names(cls, value: tuple[KeyedHash, ...]) -> tuple[KeyedHash, ...]:
        require_sorted_unique([h.name for h in value], "keyed hash names")
        return value


class EventDraft(_EventFields):
    """An event before the Journal assigns its positions (``tenant_seq``, ``run_seq``,
    ``prev_hash``). Only the Journal turns a draft into an Event (WP-03)."""


class Event(_EventFields):
    """A Journal event (E 8). Append-only; positions are assigned by the Journal."""

    run_seq: Seq | None
    tenant_seq: Seq
    prev_hash: Digest | None

    @model_validator(mode="after")
    def _positions(self) -> Self:
        if (self.run_id is None) != (self.run_seq is None):
            raise _invalid("run_position", "run_id and run_seq must be set together")
        if (self.tenant_seq == 1) != (self.prev_hash is None):
            raise _invalid("hash_chain_start", "only tenant_seq 1 has no prev_hash")
        return self

    @classmethod
    def from_draft(
        cls,
        draft: EventDraft,
        *,
        tenant_seq: int,
        run_seq: int | None,
        prev_hash: str | None,
    ) -> Event:
        values = {name: getattr(draft, name) for name in EventDraft.model_fields}
        values.update(tenant_seq=tenant_seq, run_seq=run_seq, prev_hash=prev_hash)
        return cls.model_validate(values)
