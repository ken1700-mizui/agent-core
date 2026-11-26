"""Typed errors shared by all Engines.

Every error carries a stable ``code`` and says whether it is ``fatal`` (the
caller must stop rather than retry). Messages never include record contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class AgentCoreError(Exception):
    """Base class of all Agent Core errors."""

    code: ClassVar[str] = "AGENT_CORE_ERROR"
    fatal: ClassVar[bool] = False


class InvalidIdError(AgentCoreError, ValueError):
    """A string is not a valid entity ID or site ID."""

    code = "INVALID_ID"


class IdKindMismatchError(InvalidIdError):
    """An ID of one kind was given where another kind is required."""

    code = "ID_KIND_MISMATCH"


class ClockOutOfRangeError(AgentCoreError):
    """The clock returned a value outside the range that IDs can encode."""

    code = "CLOCK_OUT_OF_RANGE"
    fatal = True


class RandomSourceError(AgentCoreError):
    """The random source failed or returned malformed output."""

    code = "RANDOM_SOURCE_FAILURE"
    fatal = True


class DuplicateIdError(AgentCoreError):
    """A store rejected an ID that already exists."""

    code = "DUPLICATE_ID"

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"duplicate id: {entity_id}")
        self.entity_id = entity_id


class IdCollisionRetryExhaustedError(AgentCoreError):
    """Regenerating the random part did not produce a free ID within the limit.

    E_INTEGRATION 2.2: treated as FATAL, a sign that the random generator is broken.
    """

    code = "ID_COLLISION_RETRY_EXHAUSTED"
    fatal = True

    def __init__(self, attempts: int) -> None:
        super().__init__(f"no unique id after {attempts} attempts")
        self.attempts = attempts


class ImmutableFieldError(AgentCoreError):
    """An update tried to change a field that must not change."""

    code = "IMMUTABLE_FIELD"

    def __init__(self, record_type: str, field: str) -> None:
        super().__init__(f"{record_type}.{field} cannot be changed")
        self.record_type = record_type
        self.field = field


@dataclass(frozen=True)
class ValidationIssue:
    """One validation problem: where it is and what kind it is, never the input value."""

    location: str
    type: str


class RecordValidationError(AgentCoreError):
    """Input could not be restored as a record."""

    code = "RECORD_VALIDATION_FAILED"

    def __init__(self, record_type: str, issues: tuple[ValidationIssue, ...]) -> None:
        summary = ", ".join(f"{i.location}: {i.type}" for i in issues) or "invalid input"
        super().__init__(f"invalid {record_type}: {summary}")
        self.record_type = record_type
        self.issues = issues
