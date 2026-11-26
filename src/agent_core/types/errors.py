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


# ---------------------------------------------------------------- journal (WP-03)


class JournalError(AgentCoreError):
    """Base class of journal errors. Fatal unless stated otherwise: stop, restart, verify."""

    code = "JOURNAL_ERROR"
    fatal = True


class JournalArgumentError(JournalError, ValueError):
    """A caller passed an invalid argument. Nothing was read or written."""

    code = "JOURNAL_INVALID_ARGUMENT"
    fatal = False


class JournalClosedError(JournalError):
    """The journal instance was closed or stopped after a fatal error."""

    code = "JOURNAL_CLOSED"


class RuntimeLockHeldError(JournalError):
    """Another Runtime holds the OS lock. This process must not start (A 5.3)."""

    code = "RUNTIME_LOCK_HELD"


class RuntimeLockLostError(JournalError):
    """The lock file was deleted or replaced while this Runtime held it."""

    code = "RUNTIME_LOCK_LOST"


class OwnerEpochMismatchError(JournalError):
    """The runtime_owner row names another owner or epoch (A 5.3, A.I3)."""

    code = "OWNER_EPOCH_MISMATCH"


class StaleEventEpochError(JournalError):
    """A draft carries an owner_epoch other than the journal's current one."""

    code = "STALE_EVENT_EPOCH"
    fatal = False


class RunTenantMismatchError(JournalError):
    """A run_id already belongs to another tenant."""

    code = "RUN_TENANT_MISMATCH"
    fatal = False


class JournalSchemaError(JournalError):
    """The database is not a journal, or its schema version is not supported."""

    code = "JOURNAL_SCHEMA_UNSUPPORTED"


class JournalCorruptionError(JournalError):
    """Stored data failed verification (hash, chain, sequence, body or head)."""

    code = "JOURNAL_CORRUPTION"

    def __init__(self, reason: str, tenant_seq: int | None = None) -> None:
        where = f" at tenant_seq {tenant_seq}" if tenant_seq is not None else ""
        super().__init__(f"journal corruption: {reason}{where}")
        self.reason = reason
        self.tenant_seq = tenant_seq


class JournalStorageError(JournalError):
    """A database error that is not a duplicate ID. The transaction was rolled back."""

    code = "JOURNAL_STORAGE_FAILURE"


class JournalWriteOutcomeUnknownError(JournalError):
    """It is not known whether the write was stored. Never retry under a new ID."""

    code = "JOURNAL_WRITE_OUTCOME_UNKNOWN"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        # A failure during clean-up (rollback) is kept here, so it does not hide
        # the original error, which stays the ``__context__``.
        self.rollback_error: BaseException | None = None


class SequenceExhaustedError(JournalError):
    """A sequence or epoch would exceed the signed 64-bit range."""

    code = "SEQUENCE_EXHAUSTED"


class RuntimeLockFileError(JournalError):
    """The lock file is not one this Runtime may use (link, unexpected content).
    It is left unchanged."""

    code = "RUNTIME_LOCK_FILE_REJECTED"


class JournalFileChangedError(JournalError):
    """The database path no longer names the opened file, or it gained a hard link."""

    code = "JOURNAL_FILE_CHANGED"
