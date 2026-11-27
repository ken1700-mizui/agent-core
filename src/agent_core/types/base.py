"""Base models.

- ``FrozenModel``: strict, frozen, rejects unknown fields, hides input values in
  errors. Python input is not coerced (a ``str`` is not an enum member, a
  ``list`` is not a ``tuple``, ``1.0`` and ``True`` are not ints).
- ``ImmutableRecord``: append-only records (Event, EngineRequest). They are
  never updated.
- ``StateSnapshot``: the current state of an entity (Task, Run, Step,
  Attempt) as folded from the Journal. Also frozen; ``evolve`` returns a new,
  re-validated snapshot and refuses to change identity or write-once fields.
  Evolving a snapshot is not a state transition: transition rules are WP-04.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict

from agent_core.types.errors import ImmutableFieldError


class FrozenModel(BaseModel):
    # Written inline (not via a variable) so the Pydantic mypy plugin also sees frozen=True.
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class ImmutableRecord(FrozenModel):
    """An append-only record. There is deliberately no update method."""


class StateSnapshot(FrozenModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset()
    # Fields that may go from None to a value once and never change afterwards.
    WRITE_ONCE_FIELDS: ClassVar[frozenset[str]] = frozenset()

    def evolve(self, **changes: Any) -> Self:
        for name, value in changes.items():
            if name in self.IDENTITY_FIELDS and value != getattr(self, name):
                raise ImmutableFieldError(type(self).__name__, name)
            if name in self.WRITE_ONCE_FIELDS:
                current = getattr(self, name)
                if current is not None and value != current:
                    raise ImmutableFieldError(type(self).__name__, name)
        values = {name: getattr(self, name) for name in type(self).model_fields}
        values.update(changes)
        return type(self).model_validate(values)
