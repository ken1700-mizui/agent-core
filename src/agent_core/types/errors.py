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


class JournalHeadMismatchError(JournalError):
    """The tenant head is not the one the caller expected. Nothing was stored.

    The caller's state is stale (another writer, or a lost update): reload it."""

    code = "JOURNAL_HEAD_MISMATCH"
    fatal = False


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


# ---------------------------------------------------------------- runtime state machine (WP-04)


class StateTransitionError(AgentCoreError):
    """Base class of rejected state transitions. Nothing was recorded."""

    code = "STATE_TRANSITION_REJECTED"


class IllegalTransitionError(StateTransitionError):
    """The transition is not an edge of the transition table."""

    code = "ILLEGAL_TRANSITION"


class TransitionPreconditionError(StateTransitionError):
    """The edge exists, but its preconditions or required evidence are not met."""

    code = "TRANSITION_PRECONDITION_FAILED"


class StaleStateError(StateTransitionError):
    """The caller's snapshot is not the current state (stale or repeated update)."""

    code = "STALE_STATE"


class UnknownEntityError(StateTransitionError):
    """The referenced Task, Run, Step or Attempt does not exist."""

    code = "UNKNOWN_ENTITY"


class DuplicateEntityError(StateTransitionError):
    """An entity with this ID already exists (an attempt is never reused)."""

    code = "DUPLICATE_ENTITY"


class RuntimeReentryError(StateTransitionError):
    """A runtime command was called while another command of the same runtime was
    in progress (for example from an evidence verifier). Nothing was written."""

    code = "RUNTIME_REENTRY"


class EvidenceRejectedError(StateTransitionError):
    """The evidence verifier did not confirm the evidence."""

    code = "EVIDENCE_REJECTED"


class StateRebuildError(AgentCoreError):
    """Journal events cannot be folded into a valid state. Never ignored."""

    code = "STATE_REBUILD_FAILED"
    fatal = True


class RuntimeReloadRequiredError(AgentCoreError):
    """The runtime's state may no longer match the Journal (stale head, unknown write
    outcome or divergence). It refuses every command until it is reloaded."""

    code = "RUNTIME_RELOAD_REQUIRED"
    fatal = True


class StateDivergenceError(AgentCoreError):
    """The journal stored an event whose position differs from the one planned."""

    code = "STATE_DIVERGENCE"
    fatal = True


# ---------------------------------------------------------------- tools (WP-07)


class ToolError(AgentCoreError):
    """Base class of tool registry errors. Nothing was registered or proposed."""

    code = "TOOL_ERROR"


class UnknownToolError(ToolError):
    code = "UNKNOWN_TOOL"


class UnknownToolVersionError(ToolError):
    code = "UNKNOWN_TOOL_VERSION"


class ToolNotApprovedError(ToolError):
    """No matching owner-approved entry (manifest_hash and artifact_hash) exists."""

    code = "TOOL_NOT_APPROVED"


class ToolAlreadyRegisteredError(ToolError):
    """Another manifest is already registered for this tool_id and version."""

    code = "TOOL_ALREADY_REGISTERED"


class ToolKindNotAllowedError(ToolError):
    """The kind or a declared feature is not allowed in the current Phase."""

    code = "TOOL_KIND_NOT_ALLOWED"


class ToolManifestError(ToolError):
    code = "TOOL_MANIFEST_INVALID"


class _ToolDataError(ToolError):
    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        summary = ", ".join(f"{i.location}: {i.type}" for i in issues) or "invalid"
        super().__init__(f"{self.code}: {summary}")
        self.issues = issues


class ToolInputError(_ToolDataError):
    """Tool input does not match the input schema. Names and values are not echoed."""

    code = "TOOL_INPUT_INVALID"


class ToolOutputError(_ToolDataError):
    """Tool output does not match the output schema or size limit."""

    code = "TOOL_OUTPUT_INVALID"


# ---------------------------------------------------------------- observability (WP-11)


class LogWriteError(AgentCoreError):
    """An observation log record could not be written. Not a Journal error: the
    Journal's state is unaffected. Observation logs are not audit records."""

    code = "LOG_WRITE_FAILED"


class LogCapacityError(LogWriteError):
    """The log storage cap was reached; nothing was written."""

    code = "LOG_CAPACITY_REACHED"


class LogRecordTooLargeError(LogWriteError):
    code = "LOG_RECORD_TOO_LARGE"


class LogFileChangedError(LogWriteError):
    """The log file changed outside this sink (size, identity or links). The sink
    stops: it cannot tell what the file now holds."""

    code = "LOG_FILE_CHANGED"


class LogFileIncompleteError(LogWriteError):
    """The log file ends with an incomplete line (for example after a partial
    write). Appending is refused; the file is left as it is."""

    code = "LOG_FILE_INCOMPLETE"


class LogPartialWriteError(LogWriteError):
    """Only part of a line was written. The partial tail stays in the file (it is
    never truncated) and the sink stops; the file's last line is then incomplete."""

    code = "LOG_PARTIAL_WRITE"


class LogSinkStoppedError(LogWriteError):
    """The sink stopped after an earlier failure and accepts no more lines."""

    code = "LOG_SINK_STOPPED"


class AuditWriteError(AgentCoreError):
    """An audit record could not be written. The operation that needs it must stop
    (C2 4.5): audit is never skipped. Full enforcement is WP-09."""

    code = "AUDIT_WRITE_FAILED"
    fatal = True


class AuditRecursionError(AuditWriteError):
    """The audit writer was entered again while writing (C2.X25: no recursion)."""

    code = "AUDIT_RECURSION"


# ---------------------------------------------------------------- replay (WP-06)


class ReplayBlobNotFoundError(AgentCoreError):
    """A replay blob store has no blob for this tenant and reference."""

    code = "REPLAY_BLOB_NOT_FOUND"


# ---------------------------------------------------------------- safety (WP-09A)


class SafetyError(AgentCoreError):
    """A safety check refused an operation. ``reasons`` are fixed codes, never text
    from the operation itself."""

    code = "SAFETY_REFUSED"

    def __init__(self, *reasons: str) -> None:
        if not reasons:
            raise ValueError("a refusal names at least one reason")
        super().__init__(", ".join(reasons))
        self.reasons: tuple[str, ...] = tuple(reasons)


class ActionDescriptorError(SafetyError):
    """The operation's content cannot be fixed into a valid ActionDescriptor."""

    code = "ACTION_DESCRIPTOR_INVALID"


class PolicyDeniedError(SafetyError):
    code = "POLICY_DENIED"


class ApprovalRejectedError(SafetyError):
    """The approval is forged, expired, revoked, used, or for another action."""

    code = "APPROVAL_REJECTED"


class StorageAdmissionError(SafetyError):
    """The minimal Phase 1 storage admission gate refused the data."""

    code = "STORAGE_ADMISSION_REFUSED"


# ---------------------------------------------------------------- cost (WP-09B)


class CostError(AgentCoreError):
    """A budget ledger operation was refused. The whole transaction is rolled back
    (no journal event, no ledger row). ``reasons`` are fixed codes."""

    code = "COST_REFUSED"

    def __init__(self, *reasons: str) -> None:
        if not reasons:
            raise ValueError("a refusal names at least one reason")
        super().__init__(", ".join(reasons))
        self.reasons: tuple[str, ...] = tuple(reasons)


class CostOverflowError(CostError):
    """A cost total or product would leave the 64-bit range (E 4.3, E.Z9)."""

    code = "COST_OVERFLOW"
    fatal = True


class BudgetExceededError(CostError):
    """A reservation would exceed a budget level; ``reasons`` name the levels."""

    code = "BUDGET_EXCEEDED"


class BudgetCapMissingError(CostError):
    """A budget level has no cap configured (fail closed: no cap, no reservation)."""

    code = "BUDGET_CAP_MISSING"


class BudgetBlockedError(CostError):
    """New reservations in this scope are stopped (overrun or unexplained difference)."""

    code = "BUDGET_BLOCKED"


class CounterCapExceededError(CostError):
    """A non-monetary limit (calls, retries, work units, elapsed time) was reached."""

    code = "COUNTER_CAP_EXCEEDED"


class ReservationStateError(CostError):
    """The reservation is not in a state that allows this operation."""

    code = "RESERVATION_STATE"


class ReservationMismatchError(CostError):
    """The operation names another Attempt, action or reservation than the ledger row."""

    code = "RESERVATION_MISMATCH"


class ReservationReuseError(CostError):
    """The reservation ID or the Attempt already has a reservation (E 38.3)."""

    code = "RESERVATION_REUSED"


class ReservationExpiredError(CostError):
    code = "RESERVATION_EXPIRED"


class ReservationAmountError(CostError):
    """The amount is below the trusted required amount or above the cost cap."""

    code = "RESERVATION_AMOUNT"


class DuplicateUsageReportError(CostError):
    code = "USAGE_REPORT_DUPLICATE"


class LedgerPairingError(CostError):
    """A ledger operation and a runtime transition must come together (WP-09B)."""

    code = "LEDGER_PAIRING"


class LedgerKeyMissingError(CostError):
    code = "LEDGER_KEY_MISSING"


class OwnerAuthorizationError(CostError):
    """An owner operation without a valid owner channel MAC."""

    code = "OWNER_AUTHORIZATION"
