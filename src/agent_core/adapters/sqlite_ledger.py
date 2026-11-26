"""Budget ledger tables and operations (WP-09B; D 10.2, 11; E 4, 38; AUDIT-01).

The tables live in the Journal's SQLite database (journal schema version 2).
Every function here takes a cursor inside an open ``BEGIN IMMEDIATE``
transaction and either completes or raises; the caller (the SQLite journal)
rolls the whole transaction back on any error, so a refused operation leaves
no journal event and no ledger row.

Accounting:

- One row per reservation, bound to one Attempt (``attempt_id`` UNIQUE) and one
  ``reservation_id`` (PRIMARY KEY). ``hold_micro`` is what is still held;
  ``spent_micro`` is the cost recorded so far. Every scope's usage is the sum of
  ``hold + spent`` of its reservations, computed with checked integers. A
  release sets the hold to 0 and never touches the spent cost.
- Every change appends a ``ledger_entries`` row (state entries and the
  adjustments CORRECTION, UNEXPLAINED and OVERRUN).
- ``ID_ALLOCATED`` holds nothing. Activation (``ID_ALLOCATED -> RESERVED``) checks,
  in one transaction: the Attempt, expiry, ``required <= amount <= cost_cap``,
  blocks, every budget level's cap, the enforced counters and the Run's elapsed
  time. A missing cap refuses (fail closed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from typing import Any, Final

from agent_core.cost.rules import (
    check_transition,
    checked_add,
    checked_sum,
    known_cost,
    required_amount,
)
from agent_core.safety.approval import check_approval, verify_signature
from agent_core.safety.paths import any_match
from agent_core.safety.pdp import decide, require_allowed
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    CounterView,
    LedgerEntryView,
    ReservationState,
    ReservationView,
    ScopeUsage,
)
from agent_core.types.errors import (
    ApprovalRejectedError,
    AuditRecursionError,
    BudgetBlockedError,
    BudgetCapMissingError,
    BudgetExceededError,
    CounterCapExceededError,
    DuplicateUsageReportError,
    LedgerKeyMissingError,
    LedgerPairingError,
    PermitRejectedError,
    ReservationAmountError,
    ReservationExpiredError,
    ReservationMismatchError,
    ReservationReuseError,
    ReservationStateError,
)
from agent_core.types.events import EventDraft
from agent_core.types.ledger import (
    OWNER_OPS,
    STANDALONE_OPS,
    ActivateReservation,
    AllocateReservation,
    ClaimReceipt,
    ConfirmApproval,
    ConsumePermit,
    Correct,
    DenyResource,
    InstallPolicy,
    InstallRunGrant,
    IssuePermit,
    LedgerOp,
    MarkActive,
    MarkUnknown,
    Reconcile,
    RecordApproval,
    RecordDecision,
    RecordIntent,
    RecoverInFlight,
    ReleaseReservation,
    ReportUsage,
    ResumeScope,
    RevokeAccess,
    RevokeApproval,
    SetBudgetCap,
    SetCounterCap,
    SetRunWindow,
    StopScope,
    Unexplained,
    VoidAllocation,
)
from agent_core.types.model import UsageStatus
from agent_core.types.records import AttemptState
from agent_core.types.safety import (
    AUDIT_HASH_DOMAIN,
    PERMIT_MAC_DOMAIN,
    ActionDescriptor,
    ActionKind,
    ApprovalReason,
    AuditView,
    Consumer,
    DispatchAuthorization,
    ExactApproval,
    LedgerKeys,
    Permit,
    PermitBody,
    PermitReason,
    PermitState,
    Policy,
    PolicyDecision,
    Receipt,
    ReceiptState,
    RunGrant,
    SafetyContext,
    SignedApproval,
    StopEntry,
    StopScopeType,
    StopStatus,
)
from agent_core.types.serialization import dump_json
from agent_core.types.tools import ToolRegistration
from agent_core.types.transitions import (
    AbortEvidence,
    AttemptTransition,
    EntityAction,
    PolicyDecisionEvidence,
    RuntimePayload,
)
from agent_core.types.values import MAX_INT64

R = ReservationState

LEDGER_DDL: Final = (
    """CREATE TABLE budget_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    cap_micro INTEGER NOT NULL CHECK (cap_micro >= 0),
    PRIMARY KEY (scope_type, scope_id)
) STRICT""",
    """CREATE TABLE counter_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    cap INTEGER NOT NULL CHECK (cap >= 0),
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT""",
    """CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT""",
    """CREATE TABLE budget_blocks (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id, reservation_id, reason)
) STRICT""",
    """CREATE TABLE reservations (
    reservation_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    experiment_id TEXT,
    action_kind TEXT NOT NULL,
    state TEXT NOT NULL,
    action_hash TEXT,
    cost_cap_micro INTEGER CHECK (cost_cap_micro >= 0),
    reserved_micro INTEGER NOT NULL CHECK (reserved_micro >= 0),
    hold_micro INTEGER NOT NULL CHECK (hold_micro >= 0),
    spent_micro INTEGER NOT NULL CHECK (spent_micro >= 0),
    spent_lower_micro INTEGER NOT NULL CHECK (spent_lower_micro >= 0),
    spent_known INTEGER NOT NULL CHECK (spent_known IN (0, 1)),
    allocated_at_ms INTEGER NOT NULL CHECK (allocated_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= 0),
    permit_id TEXT,
    receipt_id TEXT
) STRICT""",
    "CREATE INDEX reservations_by_step ON reservations (step_id)",
    "CREATE INDEX reservations_by_run ON reservations (run_id)",
    "CREATE INDEX reservations_by_task ON reservations (task_id)",
    """CREATE TABLE ledger_entries (
    entry_seq INTEGER PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT,
    hold_micro INTEGER NOT NULL CHECK (hold_micro >= 0),
    spent_micro INTEGER NOT NULL CHECK (spent_micro >= 0),
    delta_micro INTEGER NOT NULL CHECK (delta_micro >= 0),
    usage_event_id TEXT,
    refers_to INTEGER,
    evidence_ref TEXT,
    at_ms INTEGER NOT NULL CHECK (at_ms >= 0),
    CONSTRAINT ledger_usage_once UNIQUE (reservation_id, usage_event_id)
) STRICT""",
)

_SCOPE_COLUMNS: Final = {
    BudgetScopeType.STEP: "step_id",
    BudgetScopeType.RUN: "run_id",
    BudgetScopeType.TASK: "task_id",
    BudgetScopeType.USER: "user_id",
    BudgetScopeType.TENANT: "tenant_id",
    BudgetScopeType.PERIOD: "period_id",
    BudgetScopeType.EXPERIMENT: "experiment_id",
}
_ROW: Final = (
    "reservation_id, attempt_id, tenant_id, user_id, task_id, run_id, step_id, period_id, "
    "experiment_id, action_kind, state, action_hash, cost_cap_micro, reserved_micro, "
    "hold_micro, spent_micro, spent_lower_micro, spent_known, allocated_at_ms, "
    "expires_at_ms, permit_id, receipt_id"
)
_NAMES: Final = tuple(name.strip() for name in _ROW.split(","))
# Overruns stop new reservations in the run, task, experiment and period (E 4.1);
# unexplained differences in the run, task and experiment (D 10.2).
_OVERRUN_SCOPES: Final = (
    BudgetScopeType.RUN,
    BudgetScopeType.TASK,
    BudgetScopeType.EXPERIMENT,
    BudgetScopeType.PERIOD,
)
_UNEXPLAINED_SCOPES: Final = (BudgetScopeType.RUN, BudgetScopeType.TASK, BudgetScopeType.EXPERIMENT)


# ---------------------------------------------------------------- rows and entries


def _row(cur: sqlite3.Cursor, reservation_id: str) -> dict[str, Any] | None:
    found = cur.execute(
        f"SELECT {_ROW} FROM reservations WHERE reservation_id = ?",  # noqa: S608 (constant)
        (str(reservation_id),),
    ).fetchone()
    return None if found is None else dict(zip(_NAMES, found, strict=True))


def _bound_row(cur: sqlite3.Cursor, reservation_id: str, attempt_id: str) -> dict[str, Any]:
    row = _row(cur, reservation_id)
    if row is None:
        raise ReservationStateError("NOT_ALLOCATED")
    if row["attempt_id"] != str(attempt_id):
        raise ReservationMismatchError("ATTEMPT")
    return row


def _entry(
    cur: sqlite3.Cursor,
    row: dict[str, Any],
    kind: str,
    now_ms: int,
    *,
    delta: int = 0,
    usage_event_id: str | None = None,
    refers_to: int | None = None,
    evidence_ref: str | None = None,
) -> None:
    cur.execute(
        "INSERT INTO ledger_entries (reservation_id, kind, state, hold_micro, spent_micro, "
        "delta_micro, usage_event_id, refers_to, evidence_ref, at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["reservation_id"],
            kind,
            row["state"] if kind == "STATE" else None,
            row["hold_micro"],
            row["spent_micro"],
            delta,
            usage_event_id,
            refers_to,
            evidence_ref,
            now_ms,
        ),
    )


def _update(cur: sqlite3.Cursor, row: dict[str, Any], **changes: Any) -> dict[str, Any]:
    row = {**row, **changes}
    assignments = ", ".join(f"{name} = ?" for name in changes)
    cur.execute(
        f"UPDATE reservations SET {assignments} WHERE reservation_id = ?",  # noqa: S608 (constant names)
        (*changes.values(), row["reservation_id"]),
    )
    return row


def _move(
    cur: sqlite3.Cursor, row: dict[str, Any], target: ReservationState, now_ms: int, **changes: Any
) -> dict[str, Any]:
    check_transition(ReservationState(row["state"]), target)
    row = _update(cur, row, state=target.value, **changes)
    _entry(cur, row, "STATE", now_ms)
    return row


def _scopes_of(row: dict[str, Any]) -> list[tuple[BudgetScopeType, str]]:
    return [
        (kind, row[column]) for kind, column in _SCOPE_COLUMNS.items() if row[column] is not None
    ]


def _totals(cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str) -> tuple[int, int]:
    rows = cur.execute(
        f"SELECT hold_micro, spent_micro FROM reservations WHERE {_SCOPE_COLUMNS[kind]} = ?",  # noqa: S608
        (scope_id,),
    ).fetchall()
    return checked_sum(r[0] for r in rows), checked_sum(r[1] for r in rows)


def _block(
    cur: sqlite3.Cursor, row: dict[str, Any], kinds: tuple[BudgetScopeType, ...], reason: str
) -> None:
    for kind, scope_id in _scopes_of(row):
        if kind in kinds:
            cur.execute(
                "INSERT OR IGNORE INTO budget_blocks "
                "(scope_type, scope_id, reservation_id, reason) VALUES (?, ?, ?, ?)",
                (kind.value, scope_id, row["reservation_id"], reason),
            )


def _record_cost(
    cur: sqlite3.Cursor, row: dict[str, Any], target: ReservationState, actual: int, now_ms: int
) -> dict[str, Any]:
    row = _move(cur, row, target, now_ms, hold_micro=0, spent_micro=actual, spent_known=1)
    if actual > row["reserved_micro"]:
        # The actual amount is recorded in full; new reservations stop (E 4.1).
        _entry(cur, row, "OVERRUN", now_ms, delta=actual - row["reserved_micro"])
        _block(cur, row, _OVERRUN_SCOPES, "OVERRUN")
    return row


# ---------------------------------------------------------------- operations


def _allocate(cur: sqlite3.Cursor, op: AllocateReservation, now_ms: int) -> None:
    if _row(cur, op.reservation_id) is not None:
        raise ReservationReuseError("RESERVATION_ID_USED")
    taken = cur.execute(
        "SELECT 1 FROM reservations WHERE attempt_id = ?", (str(op.attempt_id),)
    ).fetchone()
    if taken is not None:
        raise ReservationReuseError("ATTEMPT_HAS_RESERVATION")
    scopes = op.scopes
    expires = checked_add(now_ms, op.ttl_ms)
    cur.execute(
        f"INSERT INTO reservations ({_ROW}) VALUES ({', '.join('?' * len(_NAMES))})",  # noqa: S608
        (
            str(op.reservation_id),
            str(op.attempt_id),
            str(scopes.tenant_id),
            str(scopes.user_id),
            str(scopes.task_id),
            str(scopes.run_id),
            str(scopes.step_id),
            scopes.period_id,
            None if scopes.experiment_id is None else str(scopes.experiment_id),
            op.action_kind.value,
            R.ID_ALLOCATED.value,
            None,
            None,
            0,
            0,
            0,
            0,
            1,
            now_ms,
            expires,
            None,
            None,
        ),
    )
    row = _row(cur, op.reservation_id)
    assert row is not None  # noqa: S101 (just inserted in this transaction)
    _entry(cur, row, "STATE", now_ms)


def _check_counters(cur: sqlite3.Cursor, row: dict[str, Any], now_ms: int) -> None:
    kind = ActionKind(row["action_kind"])
    counters = [
        CounterKind.MODEL_CALLS if kind is ActionKind.MODEL_CALL else CounterKind.TOOL_CALLS,
        CounterKind.WORK_UNITS,
    ]
    earlier = cur.execute(
        "SELECT COUNT(*) FROM reservations WHERE step_id = ? AND reservation_id != ? "
        "AND state NOT IN (?, ?)",
        (row["step_id"], row["reservation_id"], R.ID_ALLOCATED.value, R.VOID.value),
    ).fetchone()[0]
    if earlier:
        counters.append(CounterKind.RETRIES)
    exceeded: list[str] = []
    for counter in counters:
        for scope in (BudgetScopeType.RUN, BudgetScopeType.TASK):
            scope_id = row[_SCOPE_COLUMNS[scope]]
            found = cur.execute(
                "SELECT cap, used FROM counter_caps WHERE scope_type = ? AND scope_id = ? "
                "AND counter = ?",
                (scope.value, scope_id, counter.value),
            ).fetchone()
            if found is None:
                raise BudgetCapMissingError(f"{scope.value}_{counter.value}")
            used = checked_add(found[1], 1)
            if used > found[0]:
                exceeded.append(counter.value)
                continue
            cur.execute(
                "UPDATE counter_caps SET used = ? WHERE scope_type = ? AND scope_id = ? "
                "AND counter = ?",
                (used, scope.value, scope_id, counter.value),
            )
    window = cur.execute(
        "SELECT started_at_ms, max_elapsed_ms FROM run_windows WHERE run_id = ?", (row["run_id"],)
    ).fetchone()
    if window is None:
        raise BudgetCapMissingError("ELAPSED_MS")
    if now_ms < window[0] or now_ms - window[0] > window[1]:
        exceeded.append("ELAPSED_MS")
    if exceeded:
        raise CounterCapExceededError(*dict.fromkeys(exceeded))


def _activate(cur: sqlite3.Cursor, op: ActivateReservation, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    check_transition(ReservationState(row["state"]), R.RESERVED)
    if now_ms >= row["expires_at_ms"]:
        raise ReservationExpiredError("ALLOCATION_EXPIRED")
    required = required_amount(op.estimate)
    amount = op.amount.micro_units
    if amount < required:
        raise ReservationAmountError("BELOW_REQUIRED")
    if amount > op.cost_cap.micro_units:
        raise ReservationAmountError("ABOVE_COST_CAP")
    decided = cur.execute(
        "SELECT action_hash, descriptor, approval_required, approval_id FROM decisions "
        "WHERE attempt_id = ?",
        (row["attempt_id"],),
    ).fetchone()
    if decided is None or decided[0] != op.action_hash:
        raise ReservationMismatchError("NO_DECISION_FOR_ACTION")
    if ActionDescriptor.model_validate_json(decided[1]).cost_cap != op.cost_cap:
        raise ReservationMismatchError("COST_CAP_DIFFERS")
    if decided[2] and decided[3] is None:
        raise ReservationStateError("APPROVAL_REQUIRED")
    scopes = _scopes_of(row)
    blocked = [
        kind.value
        for kind, scope_id in scopes
        if cur.execute(
            "SELECT 1 FROM budget_blocks WHERE scope_type = ? AND scope_id = ? LIMIT 1",
            (kind.value, scope_id),
        ).fetchone()
    ]
    if blocked:
        raise BudgetBlockedError(*blocked)
    exceeded: list[str] = []
    for kind, scope_id in scopes:
        cap = cur.execute(
            "SELECT cap_micro FROM budget_caps WHERE scope_type = ? AND scope_id = ?",
            (kind.value, scope_id),
        ).fetchone()
        if cap is None:
            raise BudgetCapMissingError(kind.value)
        hold, spent = _totals(cur, kind, scope_id)
        if checked_sum((hold, spent, amount)) > cap[0]:
            exceeded.append(kind.value)
    if exceeded:
        raise BudgetExceededError(*exceeded)
    _check_counters(cur, row, now_ms)
    _move(
        cur,
        row,
        R.RESERVED,
        now_ms,
        action_hash=op.action_hash,
        cost_cap_micro=op.cost_cap.micro_units,
        reserved_micro=amount,
        hold_micro=amount,
    )


def consume_reservation(
    cur: sqlite3.Cursor,
    *,
    reservation_id: str,
    attempt_id: str,
    action_hash: str,
    permit_id: str,
    receipt_id: str,
    now_ms: int = 0,
) -> None:
    """``RESERVED -> CONSUMED`` inside the permit consumption transaction (WP-09C)."""
    row = _bound_row(cur, reservation_id, attempt_id)
    if row["state"] != R.RESERVED.value:
        raise ReservationStateError("NOT_RESERVED")
    if row["action_hash"] != str(action_hash):
        raise ReservationMismatchError("ACTION_HASH")
    _move(cur, row, R.CONSUMED, now_ms, permit_id=str(permit_id), receipt_id=str(receipt_id))


def _report_usage(cur: sqlite3.Cursor, op: ReportUsage, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    seen = cur.execute(
        "SELECT 1 FROM ledger_entries WHERE reservation_id = ? AND usage_event_id = ?",
        (row["reservation_id"], op.usage_event_id),
    ).fetchone()
    if seen is not None:
        raise DuplicateUsageReportError("USAGE_EVENT_SEEN")
    if row["state"] != R.ACTIVE.value:
        raise ReservationStateError("NOT_ACTIVE")
    cost = known_cost(op.usage)
    final = op.final and op.usage.status is UsageStatus.FINAL and cost is not None
    if final:
        assert cost is not None  # noqa: S101 (checked by final)
        row = _record_cost(cur, row, R.USAGE_REPORTED, cost, now_ms)
    else:
        # Unknown or partial: keep the whole hold; a known partial cost is a lower
        # bound, never a total, and unknown is never zero (E 3.4, D 10.2).
        lower = max(row["spent_lower_micro"], cost or 0)
        row = _move(
            cur,
            row,
            R.UNKNOWN,
            now_ms,
            hold_micro=row["reserved_micro"],
            spent_lower_micro=lower,
            spent_known=0,
        )
    _entry(cur, row, "USAGE", now_ms, usage_event_id=op.usage_event_id)


def _mark_unknown(cur: sqlite3.Cursor, op: MarkUnknown, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    _move(cur, row, R.UNKNOWN, now_ms, hold_micro=row["reserved_micro"], spent_known=0)


def _mark_active(cur: sqlite3.Cursor, op: MarkActive, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    _use_receipt(cur, row)
    _move(cur, row, R.ACTIVE, now_ms)


def _release(cur: sqlite3.Cursor, op: ReleaseReservation, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    if row["state"] == R.CONSUMED.value and op.not_dispatched_ref is None:
        # After consumption, only a live PEP's not-dispatched report releases (E.Z6).
        raise ReservationStateError("NOT_DISPATCHED_REPORT_REQUIRED")
    _move(cur, row, R.RELEASED, now_ms, hold_micro=0)


def _reconcile(cur: sqlite3.Cursor, op: Reconcile, now_ms: int) -> None:
    row = _row(cur, op.reservation_id)
    if row is None:
        raise ReservationStateError("NOT_ALLOCATED")
    actual = op.actual.micro_units
    if row["spent_known"] and actual < row["spent_micro"]:
        # Lowering a recorded final cost frees budget: owner-only, not implemented.
        raise ReservationAmountError("BELOW_RECORDED_COST")
    if actual < row["spent_lower_micro"]:
        raise ReservationAmountError("BELOW_LOWER_BOUND")
    row = _record_cost(cur, row, R.RECONCILED, actual, now_ms)
    _entry(cur, row, "RECONCILIATION", now_ms, evidence_ref=op.evidence_ref)


def _adjust(cur: sqlite3.Cursor, op: Correct | Unexplained, now_ms: int) -> None:
    row = _row(cur, op.reservation_id)
    if row is None or row["state"] in (R.ID_ALLOCATED.value, R.VOID.value):
        raise ReservationStateError("NOTHING_TO_ADJUST")
    row = _update(cur, row, spent_micro=checked_add(row["spent_micro"], op.delta_micro))
    if isinstance(op, Correct):
        _entry(
            cur,
            row,
            "CORRECTION",
            now_ms,
            delta=op.delta_micro,
            refers_to=op.refers_to_entry,
            evidence_ref=op.evidence_ref,
        )
    else:
        _entry(cur, row, "UNEXPLAINED", now_ms, delta=op.delta_micro, evidence_ref=op.evidence_ref)
        _block(cur, row, _UNEXPLAINED_SCOPES, "UNEXPLAINED")


def _void(cur: sqlite3.Cursor, op: VoidAllocation, now_ms: int) -> None:
    row = _row(cur, op.reservation_id)
    if row is None:
        raise ReservationStateError("NOT_ALLOCATED")
    _move(cur, row, R.VOID, now_ms)


def _recover(cur: sqlite3.Cursor, now_ms: int) -> None:
    ids = cur.execute(
        "SELECT reservation_id FROM reservations WHERE state IN (?, ?) ORDER BY reservation_id",
        (R.CONSUMED.value, R.ACTIVE.value),
    ).fetchall()
    for (reservation_id,) in ids:
        row = _row(cur, reservation_id)
        assert row is not None  # noqa: S101 (selected in this transaction)
        _move(cur, row, R.UNKNOWN, now_ms, hold_micro=row["reserved_micro"], spent_known=0)


def _owner(
    cur: sqlite3.Cursor, op: SetBudgetCap | SetCounterCap | SetRunWindow, now_ms: int
) -> None:
    if isinstance(op, SetBudgetCap):
        cur.execute(
            "INSERT INTO budget_caps (scope_type, scope_id, cap_micro) VALUES (?, ?, ?) "
            "ON CONFLICT (scope_type, scope_id) DO UPDATE SET cap_micro = excluded.cap_micro",
            (op.scope_type.value, op.scope_id, op.cap.micro_units),
        )
    elif isinstance(op, SetCounterCap):
        cur.execute(
            "INSERT INTO counter_caps (scope_type, scope_id, counter, cap, used) "
            "VALUES (?, ?, ?, ?, 0) ON CONFLICT (scope_type, scope_id, counter) "
            "DO UPDATE SET cap = excluded.cap",
            (op.scope_type.value, op.scope_id, op.counter.value, op.cap),
        )
    else:
        cur.execute(
            "INSERT INTO run_windows (run_id, started_at_ms, max_elapsed_ms) VALUES (?, ?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET started_at_ms = excluded.started_at_ms, "
            "max_elapsed_ms = excluded.max_elapsed_ms",
            (str(op.run_id), now_ms, op.max_elapsed_ms),
        )


def apply_op(
    cur: sqlite3.Cursor, op: LedgerOp, *, now_ms: int, ctx: LedgerContext | None = None
) -> None:
    """Apply one operation inside the caller's transaction (pairing is checked by
    the journal before this is called). ``ctx`` carries the keys and owner epoch."""
    if type(now_ms) is not int or not 0 <= now_ms <= MAX_INT64:
        raise ReservationStateError("CLOCK_INVALID")
    if _apply_authority(cur, op, now_ms, ctx):
        return
    if isinstance(op, AllocateReservation):
        _allocate(cur, op, now_ms)
    elif isinstance(op, ActivateReservation):
        _activate(cur, op, now_ms)
    elif isinstance(op, MarkActive):
        _mark_active(cur, op, now_ms)
    elif isinstance(op, ReleaseReservation):
        _release(cur, op, now_ms)
    elif isinstance(op, ReportUsage):
        _report_usage(cur, op, now_ms)
    elif isinstance(op, MarkUnknown):
        _mark_unknown(cur, op, now_ms)
    elif isinstance(op, Reconcile):
        _reconcile(cur, op, now_ms)
    elif isinstance(op, Correct | Unexplained):
        _adjust(cur, op, now_ms)
    elif isinstance(op, VoidAllocation):
        _void(cur, op, now_ms)
    elif isinstance(op, RecoverInFlight):
        _recover(cur, now_ms)
    elif isinstance(op, SetBudgetCap | SetCounterCap | SetRunWindow):
        _owner(cur, op, now_ms)
    else:
        raise LedgerPairingError("UNKNOWN_OPERATION")


# ---------------------------------------------------------------- reads


def read_reservation(cur: sqlite3.Cursor, reservation_id: str) -> ReservationView | None:
    row = _row(cur, reservation_id)
    if row is None:
        return None
    return ReservationView(
        reservation_id=row["reservation_id"],
        attempt_id=row["attempt_id"],
        state=ReservationState(row["state"]),
        reserved_micro=row["reserved_micro"],
        hold_micro=row["hold_micro"],
        spent_micro=row["spent_micro"],
        spent_lower_micro=row["spent_lower_micro"],
        spent_known=bool(row["spent_known"]),
        action_hash=row["action_hash"],
        permit_id=row["permit_id"],
        receipt_id=row["receipt_id"],
    )


def scope_usage(cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str) -> ScopeUsage:
    cap = cur.execute(
        "SELECT cap_micro FROM budget_caps WHERE scope_type = ? AND scope_id = ?",
        (kind.value, scope_id),
    ).fetchone()
    hold, spent = _totals(cur, kind, scope_id)
    blocked = cur.execute(
        "SELECT 1 FROM budget_blocks WHERE scope_type = ? AND scope_id = ? LIMIT 1",
        (kind.value, scope_id),
    ).fetchone()
    return ScopeUsage(
        scope_type=kind,
        scope_id=scope_id,
        cap_micro=None if cap is None else cap[0],
        hold_micro=hold,
        spent_micro=spent,
        blocked=blocked is not None,
    )


def read_counter(
    cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str, counter: CounterKind
) -> CounterView | None:
    found = cur.execute(
        "SELECT used, cap FROM counter_caps WHERE scope_type = ? AND scope_id = ? AND counter = ?",
        (kind.value, scope_id, counter.value),
    ).fetchone()
    return None if found is None else CounterView(used=found[0], cap=found[1])


def read_entries(cur: sqlite3.Cursor, reservation_id: str) -> tuple[LedgerEntryView, ...]:
    rows = cur.execute(
        "SELECT entry_seq, kind, state, hold_micro, spent_micro, delta_micro FROM ledger_entries "
        "WHERE reservation_id = ? ORDER BY entry_seq",
        (str(reservation_id),),
    ).fetchall()
    return tuple(
        LedgerEntryView(
            entry_seq=r[0],
            kind=r[1],
            state=None if r[2] is None else ReservationState(r[2]),
            hold_micro=r[3],
            spent_micro=r[4],
            delta_micro=r[5],
        )
        for r in rows
    )


# ---------------------------------------------------------------- pairing with transitions

# Transitions that must carry their ledger operation, and operations that must
# come with their transition (E 38; WP-09B).
_REQUIRED_OPS: Final = {
    AttemptState.CONTENT_FIXED: "allocate",
    AttemptState.POLICY_ALLOWED: "record_decision",
    AttemptState.APPROVED: "confirm_approval",
    AttemptState.BUDGET_RESERVED: "activate",
    AttemptState.INTENT_RECORDED: "record_intent",
    AttemptState.PERMIT_ISSUED: "issue_permit",
    AttemptState.PERMIT_CONSUMED: "consume_permit",
    AttemptState.DISPATCH_AUTHORIZED: "claim_receipt",
    AttemptState.DISPATCH_STARTED: "mark_active",
}
_BOUND_TO: Final = {
    "allocate": AttemptState.CONTENT_FIXED,
    "record_decision": AttemptState.POLICY_ALLOWED,
    "confirm_approval": AttemptState.APPROVED,
    "activate": AttemptState.BUDGET_RESERVED,
    "record_intent": AttemptState.INTENT_RECORDED,
    "issue_permit": AttemptState.PERMIT_ISSUED,
    "consume_permit": AttemptState.PERMIT_CONSUMED,
    "claim_receipt": AttemptState.DISPATCH_AUTHORIZED,
    "mark_active": AttemptState.DISPATCH_STARTED,
    "release": AttemptState.ABORTED_BEFORE_DISPATCH,
}


def _attempt_of(op: LedgerOp) -> object:
    if isinstance(op, ConsumePermit):
        return op.permit.body.attempt_id
    if isinstance(op, ClaimReceipt):
        return op.receipt.attempt_id
    return getattr(op, "attempt_id", None)


def check_pairing(draft: EventDraft, ops: tuple[LedgerOp, ...]) -> None:
    """Raise ``LedgerPairingError`` unless transitions and operations match exactly."""
    payload = draft.payload
    transitions: list[AttemptTransition] = []
    if isinstance(payload, RuntimePayload):
        transitions = [
            t
            for t in payload.transitions
            if isinstance(t, AttemptTransition) and t.action is EntityAction.SET_STATUS
        ]
    for op in ops:
        if op.op in OWNER_OPS:
            raise LedgerPairingError("OWNER_OPERATION_NOT_ALLOWED")
    for transition in transitions:
        wanted = _REQUIRED_OPS.get(transition.after.state)
        if wanted is None:
            continue
        found = [
            op for op in ops if op.op == wanted and _attempt_of(op) == transition.after.attempt_id
        ]
        if len(found) != 1:
            raise LedgerPairingError(f"MISSING_{wanted.upper()}")
    for op in ops:
        state = _BOUND_TO.get(op.op)
        if state is None:
            continue
        attempt_id = _attempt_of(op)
        matches = [
            t for t in transitions if t.after.state is state and t.after.attempt_id == attempt_id
        ]
        if len(matches) != 1:
            raise LedgerPairingError(f"UNPAIRED_{op.op.upper()}")
        after = matches[0].after
        evidence = matches[0].evidence
        if hasattr(op, "reservation_id") and op.reservation_id != after.reservation_id:
            raise LedgerPairingError("RESERVATION_DIFFERS")
        _check_bound(op, after, evidence)
        if isinstance(op, AllocateReservation):
            scopes = op.scopes
            if (
                scopes.step_id != after.step_id
                or scopes.tenant_id != draft.tenant_id
                or scopes.run_id != draft.run_id
            ):
                raise LedgerPairingError("SCOPES_DIFFER")
        if isinstance(op, ActivateReservation) and op.action_hash != after.action_hash:
            raise LedgerPairingError("ACTION_HASH_DIFFERS")
        if isinstance(op, ReleaseReservation):
            proof = evidence.not_dispatched_ref if isinstance(evidence, AbortEvidence) else None
            if op.not_dispatched_ref != proof:
                raise LedgerPairingError("NOT_DISPATCHED_REPORT_DIFFERS")


def _check_bound(op: LedgerOp, after: Any, evidence: Any) -> None:
    """Operation-specific agreement with the transition (WP-09C)."""
    if isinstance(op, RecordDecision):
        descriptor = op.descriptor
        if (
            descriptor.action_hash() != after.action_hash
            or descriptor.reservation_id != after.reservation_id
        ):
            raise LedgerPairingError("DESCRIPTOR_DIFFERS")
        if (
            not isinstance(evidence, PolicyDecisionEvidence)
            or evidence.approval_required != op.approval_required
            or evidence.action_hash != after.action_hash
        ):
            raise LedgerPairingError("DECISION_EVIDENCE_DIFFERS")
    elif isinstance(op, RecordIntent) and op.action_hash != after.action_hash:
        raise LedgerPairingError("ACTION_HASH_DIFFERS")
    elif isinstance(op, IssuePermit) and op.permit_id != after.permit_id:
        raise LedgerPairingError("PERMIT_DIFFERS")
    elif isinstance(op, ConsumePermit) and (
        op.permit.body.permit_id != after.permit_id or op.receipt_id != after.receipt_id
    ):
        raise LedgerPairingError("PERMIT_OR_RECEIPT_DIFFERS")
    elif isinstance(op, ClaimReceipt) and op.receipt.receipt_id != after.receipt_id:
        raise LedgerPairingError("RECEIPT_DIFFERS")


def check_standalone(ops: tuple[LedgerOp, ...]) -> None:
    for op in ops:
        if op.op not in STANDALONE_OPS:
            raise LedgerPairingError(f"NOT_STANDALONE_{op.op.upper()}")


def check_owner_ops(ops: tuple[LedgerOp, ...]) -> None:
    for op in ops:
        if op.op not in OWNER_OPS:
            raise LedgerPairingError(f"NOT_OWNER_{op.op.upper()}")


# ================================================================ WP-09C: authority

AUTHORITY_DDL: Final = (
    """CREATE TABLE safety_epochs (
    tenant_id TEXT PRIMARY KEY,
    access_epoch INTEGER NOT NULL CHECK (access_epoch >= 0)
) STRICT""",
    """CREATE TABLE revocation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revocation_gen INTEGER NOT NULL CHECK (revocation_gen >= 0)
) STRICT""",
    """CREATE TABLE policies (
    tenant_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT""",
    """CREATE TABLE run_grants (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT""",
    """CREATE TABLE stop_state (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    stop_seq INTEGER NOT NULL CHECK (stop_seq >= 0),
    status TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id)
) STRICT""",
    """CREATE TABLE deny_list (
    tenant_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (tenant_id, path)
) STRICT""",
    """CREATE TABLE decisions (
    attempt_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    registration TEXT,
    decision_ref TEXT NOT NULL,
    approval_required INTEGER NOT NULL CHECK (approval_required IN (0, 1)),
    approval_id TEXT
) STRICT""",
    """CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    owner_mac TEXT NOT NULL,
    uses INTEGER NOT NULL CHECK (uses >= 0),
    revoked INTEGER NOT NULL CHECK (revoked IN (0, 1))
) STRICT""",
    """CREATE TABLE intents (
    attempt_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT""",
    """CREATE TABLE permits (
    permit_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    mac TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT""",
    """CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY,
    permit_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT""",
    """CREATE TABLE audit_log (
    audit_seq INTEGER PRIMARY KEY,
    at_ms INTEGER NOT NULL CHECK (at_ms >= 0),
    action TEXT NOT NULL,
    targets TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT,
    access_epoch INTEGER,
    prev_hash TEXT,
    record_hash TEXT NOT NULL
) STRICT""",
)
# Rows every new database starts with: revocation generation 0, global RUNNING.
LEDGER_INIT: Final = (
    "INSERT INTO revocation (singleton, revocation_gen) VALUES (1, 0)",
    "INSERT INTO stop_state (scope_type, scope_id, stop_seq, status) "
    "VALUES ('GLOBAL', 'global', 0, 'RUNNING')",
)


class LedgerContext:
    """What the journal passes to authority operations: its keys and owner epoch."""

    __slots__ = ("keys", "owner_epoch")

    def __init__(self, *, keys: LedgerKeys | None, owner_epoch: int) -> None:
        self.keys = keys
        self.owner_epoch = owner_epoch


# ---------------------------------------------------------------- mandatory audit

_AUDIT_STATE = threading.local()


def _chain_hash(cur: sqlite3.Cursor, payload: bytes) -> tuple[str | None, str]:
    last = cur.execute(
        "SELECT record_hash FROM audit_log ORDER BY audit_seq DESC LIMIT 1"
    ).fetchone()
    prev = None if last is None else str(last[0])
    data = AUDIT_HASH_DOMAIN + (prev or "").encode() + b"\x00" + payload
    return prev, "sha256:" + hashlib.sha256(data).hexdigest()


def _insert_audit(cur: sqlite3.Cursor, values: tuple[Any, ...]) -> None:
    cur.execute(
        "INSERT INTO audit_log (at_ms, action, targets, decision, reason, policy_version, "
        "access_epoch, prev_hash, record_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )


def _audit(
    cur: sqlite3.Cursor,
    now_ms: int,
    action: str,
    targets: dict[str, str],
    decision: str,
    reason: str | None,
    policy_version: str | None,
    access_epoch: int | None,
) -> None:
    """Append one hash-chained audit record in the same transaction (C2 9). IDs,
    codes and epochs only; no body, argument or key. It never calls the ledger,
    the SAG or any other writer; a nested call is refused (C2.X25)."""
    if getattr(_AUDIT_STATE, "active", False):
        raise AuditRecursionError("the mandatory audit writer was re-entered")
    _AUDIT_STATE.active = True
    try:
        targets_json = json.dumps(targets, sort_keys=True, separators=(",", ":"))
        payload = json.dumps(
            [now_ms, action, targets_json, decision, reason, policy_version, access_epoch],
            separators=(",", ":"),
        ).encode()
        prev, record_hash = _chain_hash(cur, payload)
        _insert_audit(
            cur,
            (
                now_ms,
                action,
                targets_json,
                decision,
                reason,
                policy_version,
                access_epoch,
                prev,
                record_hash,
            ),
        )
    finally:
        _AUDIT_STATE.active = False


# ---------------------------------------------------------------- state reads


def _access_epoch(cur: sqlite3.Cursor, tenant_id: str) -> int:
    row = cur.execute(
        "SELECT access_epoch FROM safety_epochs WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    return 0 if row is None else int(row[0])


def _revocation_gen(cur: sqlite3.Cursor) -> int:
    row = cur.execute("SELECT revocation_gen FROM revocation WHERE singleton = 1").fetchone()
    if row is None:
        raise PermitRejectedError(PermitReason.STALE_REVOCATION)
    return int(row[0])


def _bump(cur: sqlite3.Cursor, *, tenants: tuple[str, ...]) -> None:
    """Revocation (E 3.3): one new generation; the listed tenants' access epochs too."""
    cur.execute(
        "UPDATE revocation SET revocation_gen = ? WHERE singleton = 1",
        (checked_add(_revocation_gen(cur), 1),),
    )
    for tenant in tenants:
        cur.execute(
            "INSERT INTO safety_epochs (tenant_id, access_epoch) VALUES (?, ?) "
            "ON CONFLICT (tenant_id) DO UPDATE SET access_epoch = excluded.access_epoch",
            (tenant, checked_add(_access_epoch(cur, tenant), 1)),
        )


def _policy(cur: sqlite3.Cursor, tenant_id: str) -> Policy | None:
    row = cur.execute("SELECT body FROM policies WHERE tenant_id = ?", (tenant_id,)).fetchone()
    return None if row is None else Policy.model_validate_json(row[0])


def _grant(cur: sqlite3.Cursor, run_id: str) -> RunGrant | None:
    row = cur.execute("SELECT body FROM run_grants WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else RunGrant.model_validate_json(row[0])


def _stops(cur: sqlite3.Cursor, tenant_id: str, run_id: str) -> tuple[StopEntry, ...]:
    rows = cur.execute(
        "SELECT scope_type, scope_id, stop_seq, status FROM stop_state WHERE "
        "(scope_type = 'GLOBAL' AND scope_id = 'global') OR "
        "(scope_type = 'TENANT' AND scope_id = ?) OR (scope_type = 'RUN' AND scope_id = ?)",
        (tenant_id, run_id),
    ).fetchall()
    return tuple(
        StopEntry(
            scope_type=StopScopeType(r[0]), scope_id=r[1], stop_seq=r[2], status=StopStatus(r[3])
        )
        for r in rows
    )


def _denied(cur: sqlite3.Cursor, tenant_id: str) -> tuple[str, ...]:
    rows = cur.execute(
        "SELECT path FROM deny_list WHERE tenant_id = ? ORDER BY path", (tenant_id,)
    ).fetchall()
    return tuple(r[0] for r in rows)


def _context(
    cur: sqlite3.Cursor, policy: Policy, run_id: str, now_ms: int, owner_epoch: int
) -> SafetyContext:
    tenant = str(policy.tenant_id)
    return SafetyContext(
        now_ms=now_ms,
        tenant_id=policy.tenant_id,
        policy_version=policy.policy_version,
        access_epoch=_access_epoch(cur, tenant),
        journal_owner_epoch=owner_epoch,
        runtime_owner_epoch=owner_epoch,
        stops=_stops(cur, tenant, run_id),
        denied_resources=_denied(cur, tenant),
    )


def _decision(cur: sqlite3.Cursor, attempt_id: str) -> dict[str, Any]:
    row = cur.execute(
        "SELECT tenant_id, run_id, action_hash, reservation_id, descriptor, registration, "
        "approval_required, approval_id FROM decisions WHERE attempt_id = ?",
        (str(attempt_id),),
    ).fetchone()
    if row is None:
        raise PermitRejectedError(PermitReason.DECISION_MISSING)
    names = (
        "tenant_id",
        "run_id",
        "action_hash",
        "reservation_id",
        "descriptor",
        "registration",
        "approval_required",
        "approval_id",
    )
    return dict(zip(names, row, strict=True))


def _decide_again(
    cur: sqlite3.Cursor, decision: dict[str, Any], now_ms: int, owner_epoch: int
) -> tuple[ActionDescriptor, Policy, PolicyDecision]:
    """The current Safety decision for a recorded descriptor (C1 6.2 step 8)."""
    descriptor = ActionDescriptor.model_validate_json(decision["descriptor"])
    registration = (
        None
        if decision["registration"] is None
        else ToolRegistration.model_validate_json(decision["registration"])
    )
    policy = _policy(cur, decision["tenant_id"])
    grant = _grant(cur, decision["run_id"])
    if policy is None or grant is None:
        raise PermitRejectedError(PermitReason.POLICY_DENIED)
    result = decide(
        policy=policy,
        grant=grant,
        descriptor=descriptor,
        registration=registration,
        context=_context(cur, policy, decision["run_id"], now_ms, owner_epoch),
    )
    return descriptor, policy, result


def _approval_row(cur: sqlite3.Cursor, approval_id: str) -> tuple[SignedApproval, int, bool] | None:
    row = cur.execute(
        "SELECT body, owner_mac, uses, revoked FROM approvals WHERE approval_id = ?",
        (str(approval_id),),
    ).fetchone()
    if row is None:
        return None
    signed = SignedApproval(approval=ExactApproval.model_validate_json(row[0]), owner_mac=row[1])
    return signed, int(row[2]), bool(row[3])


def _approval_reasons(
    cur: sqlite3.Cursor,
    ctx: LedgerContext,
    decision: dict[str, Any],
    policy: Policy,
    now_ms: int,
    *,
    unused: bool,
) -> list[str]:
    """Empty when the decision needs no approval or its approval is valid now."""
    if not decision["approval_required"]:
        return []
    if decision["approval_id"] is None:
        return [PermitReason.APPROVAL_MISSING]
    found = _approval_row(cur, decision["approval_id"])
    if found is None or ctx.keys is None:
        return [PermitReason.APPROVAL_MISSING]
    signed, uses, revoked = found
    try:
        check_approval(
            signed,
            key=ctx.keys.owner,
            action_hash=decision["action_hash"],
            tenant_id=decision["tenant_id"],
            now_ms=now_ms,
            max_ttl_ms=policy.max_approval_ttl_ms,
            uses=uses if unused else 0,
            revoked=revoked,
        )
    except ApprovalRejectedError as exc:
        return [PermitReason.APPROVAL_INVALID, *exc.reasons]
    return []


def _keys(ctx: LedgerContext | None) -> LedgerKeys:
    if ctx is None or ctx.keys is None:
        raise PermitRejectedError(PermitReason.KEY_MISSING)
    return ctx.keys


def _permit_mac(keys: LedgerKeys, body: PermitBody) -> str:
    return hmac.new(
        keys.permit.reveal(), PERMIT_MAC_DOMAIN + dump_json(body), hashlib.sha256
    ).hexdigest()


def _scope_filter(kind: StopScopeType, scope_id: str) -> tuple[str, tuple[str, ...]]:
    if kind is StopScopeType.GLOBAL:
        return "1 = 1", ()
    if kind is StopScopeType.TENANT:
        return "tenant_id = ?", (scope_id,)
    return "run_id = ?", (scope_id,)


def _revoke_outstanding(cur: sqlite3.Cursor, kind: StopScopeType, scope_id: str) -> None:
    where, params = _scope_filter(kind, scope_id)
    cur.execute(
        f"UPDATE permits SET state = 'REVOKED' WHERE state = 'ISSUED' AND {where}",  # noqa: S608
        params,
    )
    cur.execute(
        f"UPDATE receipts SET state = 'REVOKED' WHERE state IN ('ISSUED', 'CLAIMED') AND {where}",  # noqa: S608
        params,
    )


def _all_tenants(cur: sqlite3.Cursor) -> tuple[str, ...]:
    rows = cur.execute(
        "SELECT tenant_id FROM policies UNION SELECT tenant_id FROM safety_epochs ORDER BY 1"
    ).fetchall()
    return tuple(r[0] for r in rows)


# ---------------------------------------------------------------- authority operations


def _install_policy(cur: sqlite3.Cursor, op: InstallPolicy, now_ms: int) -> None:
    policy = op.policy
    tenant = str(policy.tenant_id)
    current = _policy(cur, tenant)
    if current is not None and current.policy_hash() == policy.policy_hash():
        return
    if current is not None and current.policy_version == policy.policy_version:
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "POLICY_VERSION_REUSED")
    cur.execute(
        "INSERT INTO policies (tenant_id, policy_version, policy_hash, body) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (tenant_id) DO UPDATE SET policy_version = excluded.policy_version, "
        "policy_hash = excluded.policy_hash, body = excluded.body",
        (tenant, policy.policy_version, policy.policy_hash(), dump_json(policy).decode()),
    )
    if current is not None:
        # A changed policy may narrow access: old descriptors and permits stop (C1 4.2).
        _bump(cur, tenants=(tenant,))
    _audit(
        cur,
        now_ms,
        "POLICY_INSTALLED",
        {"tenant": tenant},
        "ALLOW",
        None,
        policy.policy_version,
        _access_epoch(cur, tenant),
    )


def _install_grant(cur: sqlite3.Cursor, op: InstallRunGrant, now_ms: int) -> None:
    grant = op.grant
    if _policy(cur, str(grant.tenant_id)) is None:
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "NO_POLICY")
    row = cur.execute(
        "SELECT grant_hash FROM run_grants WHERE run_id = ?", (str(grant.run_id),)
    ).fetchone()
    if row is not None:
        if row[0] == grant.grant_hash():
            return
        # A running Run's grant is never replaced or widened (C1 4.2, S7).
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "GRANT_ALREADY_INSTALLED")
    cur.execute(
        "INSERT INTO run_grants (run_id, tenant_id, grant_hash, body) VALUES (?, ?, ?, ?)",
        (str(grant.run_id), str(grant.tenant_id), grant.grant_hash(), dump_json(grant).decode()),
    )
    _audit(
        cur,
        now_ms,
        "GRANT_INSTALLED",
        {"run": str(grant.run_id)},
        "ALLOW",
        None,
        grant.policy_version,
        grant.access_epoch_at_grant,
    )


def _record_decision(
    cur: sqlite3.Cursor, op: RecordDecision, now_ms: int, ctx: LedgerContext | None
) -> None:
    descriptor = op.descriptor
    if descriptor.attempt_id != op.attempt_id:
        raise PermitRejectedError(PermitReason.ATTEMPT_MISMATCH)
    row = _row(cur, descriptor.reservation_id)
    if (
        row is None
        or row["attempt_id"] != str(op.attempt_id)
        or row["state"] != R.ID_ALLOCATED.value
    ):
        raise PermitRejectedError(PermitReason.RESERVATION_INVALID)
    if cur.execute(
        "SELECT 1 FROM decisions WHERE attempt_id = ?", (str(op.attempt_id),)
    ).fetchone():
        raise PermitRejectedError(PermitReason.DECISION_MISSING, "DECISION_ALREADY_RECORDED")
    policy = _policy(cur, str(descriptor.tenant_id))
    grant = _grant(cur, str(descriptor.run_id))
    if policy is None or grant is None:
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "NO_POLICY_OR_GRANT")
    owner_epoch = 0 if ctx is None else ctx.owner_epoch
    result = require_allowed(
        decide(
            policy=policy,
            grant=grant,
            descriptor=descriptor,
            registration=op.registration,
            context=_context(cur, policy, str(descriptor.run_id), now_ms, owner_epoch),
        )
    )
    if result.approval_required != op.approval_required:
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "APPROVAL_REQUIREMENT_DIFFERS")
    cur.execute(
        "INSERT INTO decisions (attempt_id, tenant_id, run_id, action_hash, reservation_id, "
        "descriptor, registration, decision_ref, approval_required, approval_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            str(op.attempt_id),
            str(descriptor.tenant_id),
            str(descriptor.run_id),
            result.action_hash,
            str(descriptor.reservation_id),
            dump_json(descriptor).decode(),
            None if op.registration is None else dump_json(op.registration).decode(),
            result.decision_ref(),
            int(result.approval_required),
        ),
    )
    _audit(
        cur,
        now_ms,
        "POLICY_ALLOWED",
        {"attempt": str(op.attempt_id)},
        "ALLOW",
        None,
        result.policy_version,
        result.access_epoch,
    )


def _record_approval(
    cur: sqlite3.Cursor, op: RecordApproval, now_ms: int, ctx: LedgerContext | None
) -> None:
    if ctx is None or ctx.keys is None:
        raise LedgerKeyMissingError("OWNER_KEY")
    approval = verify_signature(ctx.keys.owner, op.signed)
    if _approval_row(cur, approval.approval_id) is not None:
        raise ApprovalRejectedError(ApprovalReason.USED)
    cur.execute(
        "INSERT INTO approvals (approval_id, tenant_id, action_hash, body, owner_mac, uses, "
        "revoked) VALUES (?, ?, ?, ?, ?, 0, 0)",
        (
            str(approval.approval_id),
            str(approval.tenant_id),
            approval.action_hash,
            dump_json(approval).decode(),
            op.signed.owner_mac,
        ),
    )
    _audit(
        cur,
        now_ms,
        "APPROVAL_RECORDED",
        {"approval": str(approval.approval_id)},
        "ALLOW",
        None,
        None,
        None,
    )


def _confirm_approval(
    cur: sqlite3.Cursor, op: ConfirmApproval, now_ms: int, ctx: LedgerContext | None
) -> None:
    if ctx is None or ctx.keys is None:
        raise LedgerKeyMissingError("OWNER_KEY")
    decision = _decision(cur, op.attempt_id)
    if not decision["approval_required"] or decision["approval_id"] is not None:
        raise PermitRejectedError(PermitReason.APPROVAL_INVALID, "NOT_PENDING")
    policy = _policy(cur, decision["tenant_id"])
    if policy is None:
        raise PermitRejectedError(PermitReason.POLICY_DENIED)
    if _approval_row(cur, op.approval_id) is None:
        raise PermitRejectedError(PermitReason.APPROVAL_MISSING)
    candidate = {**decision, "approval_id": str(op.approval_id)}
    reasons = _approval_reasons(cur, ctx, candidate, policy, now_ms, unused=True)
    if reasons:
        raise PermitRejectedError(*reasons)
    cur.execute(
        "UPDATE decisions SET approval_id = ? WHERE attempt_id = ?",
        (str(op.approval_id), str(op.attempt_id)),
    )
    _audit(
        cur,
        now_ms,
        "APPROVAL_CONFIRMED",
        {"attempt": str(op.attempt_id), "approval": str(op.approval_id)},
        "ALLOW",
        None,
        policy.policy_version,
        None,
    )


def _record_intent(cur: sqlite3.Cursor, op: RecordIntent, now_ms: int) -> None:
    row = _row(cur, op.reservation_id)
    if (
        row is None
        or row["attempt_id"] != str(op.attempt_id)
        or row["state"] != R.RESERVED.value
        or row["action_hash"] != op.action_hash
    ):
        raise PermitRejectedError(PermitReason.RESERVATION_INVALID)
    if cur.execute("SELECT 1 FROM intents WHERE attempt_id = ?", (str(op.attempt_id),)).fetchone():
        raise PermitRejectedError(PermitReason.INTENT_MISSING, "INTENT_ALREADY_RECORDED")
    cur.execute(
        "INSERT INTO intents (attempt_id, action_hash, reservation_id, intent_ref, recorded_at_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(op.attempt_id), op.action_hash, str(op.reservation_id), op.intent_ref, now_ms),
    )


def _reservation_reasons(
    cur: sqlite3.Cursor, attempt_id: str, action_hash: str, reservation_id: str
) -> list[str]:
    row = _row(cur, reservation_id)
    if (
        row is None
        or row["attempt_id"] != str(attempt_id)
        or row["state"] != R.RESERVED.value
        or row["action_hash"] != action_hash
        or row["cost_cap_micro"] is None
        or row["reserved_micro"] > row["cost_cap_micro"]
    ):
        return [PermitReason.RESERVATION_INVALID]
    return []


def _intent_reasons(cur: sqlite3.Cursor, attempt_id: str, action_hash: str) -> list[str]:
    row = cur.execute(
        "SELECT action_hash FROM intents WHERE attempt_id = ?", (str(attempt_id),)
    ).fetchone()
    return [] if row is not None and row[0] == action_hash else [PermitReason.INTENT_MISSING]


def _issue_permit(
    cur: sqlite3.Cursor, op: IssuePermit, now_ms: int, ctx: LedgerContext | None
) -> None:
    keys = _keys(ctx)
    assert ctx is not None  # noqa: S101 (checked by _keys)
    decision = _decision(cur, op.attempt_id)
    descriptor, policy, result = _decide_again(cur, decision, now_ms, ctx.owner_epoch)
    reasons: list[str] = []
    if not result.allowed:
        reasons += [PermitReason.POLICY_DENIED, *result.reasons]
    reasons += _approval_reasons(cur, ctx, decision, policy, now_ms, unused=True)
    reasons += _reservation_reasons(
        cur, op.attempt_id, decision["action_hash"], decision["reservation_id"]
    )
    reasons += _intent_reasons(cur, op.attempt_id, decision["action_hash"])
    if op.consumer is not Consumer.BROKER:
        # Phase 1 has no Egress Gateway: every permit is consumed by the broker.
        reasons.append(PermitReason.CONSUMER_MISMATCH)
    if op.ttl_ms > policy.max_permit_ttl_ms:
        reasons.append(PermitReason.TTL_TOO_LONG)
    if reasons:
        raise PermitRejectedError(*dict.fromkeys(reasons))
    body = PermitBody(
        permit_version=1,
        permit_id=op.permit_id,
        tenant_id=descriptor.tenant_id,
        run_id=descriptor.run_id,
        attempt_id=op.attempt_id,
        action_hash=decision["action_hash"],
        reservation_id=descriptor.reservation_id,
        consumer=op.consumer,
        access_epoch=_access_epoch(cur, decision["tenant_id"]),
        owner_epoch=ctx.owner_epoch,
        revocation_gen=_revocation_gen(cur),
        policy_version=policy.policy_version,
        issued_at_ms=now_ms,
        expires_at_ms=min(checked_add(now_ms, op.ttl_ms), descriptor.expires_at_ms),
    )
    if cur.execute(
        "SELECT 1 FROM permits WHERE permit_id = ? OR attempt_id = ?",
        (str(op.permit_id), str(op.attempt_id)),
    ).fetchone():
        raise PermitRejectedError(PermitReason.NOT_ISSUED, "PERMIT_ALREADY_EXISTS")
    cur.execute(
        "INSERT INTO permits (permit_id, attempt_id, tenant_id, run_id, body, mac, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(op.permit_id),
            str(op.attempt_id),
            decision["tenant_id"],
            decision["run_id"],
            dump_json(body).decode(),
            _permit_mac(keys, body),
            PermitState.ISSUED.value,
        ),
    )
    _audit(
        cur,
        now_ms,
        "PERMIT_ISSUED",
        {"attempt": str(op.attempt_id), "permit": str(op.permit_id)},
        "ALLOW",
        None,
        policy.policy_version,
        body.access_epoch,
    )


def _stored_permit(cur: sqlite3.Cursor, permit_id: str) -> tuple[Permit, PermitState] | None:
    row = cur.execute(
        "SELECT body, mac, state FROM permits WHERE permit_id = ?", (str(permit_id),)
    ).fetchone()
    if row is None:
        return None
    permit = Permit(body=PermitBody.model_validate_json(row[0]), mac=row[1])
    return permit, PermitState(row[2])


def _live_reasons(
    cur: sqlite3.Cursor, tenant_id: str, run_id: str, now_ms: int, expires_at_ms: int
) -> list[str]:
    reasons: list[str] = []
    if now_ms >= expires_at_ms:
        reasons.append(PermitReason.EXPIRED)
    stops = _stops(cur, tenant_id, run_id)
    found = {(e.scope_type, e.scope_id): e for e in stops}
    if (StopScopeType.GLOBAL, "global") not in found or any(
        e.status is not StopStatus.RUNNING for e in stops
    ):
        reasons.append(PermitReason.STOPPED)
    return reasons


def _consume_permit(
    cur: sqlite3.Cursor, op: ConsumePermit, now_ms: int, ctx: LedgerContext | None
) -> None:
    """The atomic consumption (C1 7.5, E 3.3). Every check before any write."""
    keys = _keys(ctx)
    assert ctx is not None  # noqa: S101 (checked by _keys)
    presented = op.permit
    body = presented.body
    stored = _stored_permit(cur, body.permit_id)
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_PERMIT)
    permit, state = stored
    reasons: list[str] = []
    if not hmac.compare_digest(_permit_mac(keys, body), presented.mac):
        reasons.append(PermitReason.MAC_INVALID)
    if presented != permit:
        reasons.append(PermitReason.PERMIT_DIFFERS)
    body = permit.body  # from here on, only the trusted stored record counts
    if state is not PermitState.ISSUED:
        reasons.append(PermitReason.NOT_ISSUED)
    if op.consumer is not body.consumer:
        reasons.append(PermitReason.CONSUMER_MISMATCH)
    if op.actual_action_hash != body.action_hash:
        reasons.append(PermitReason.ACTION_MISMATCH)
    if body.access_epoch != _access_epoch(cur, str(body.tenant_id)):
        reasons.append(PermitReason.STALE_ACCESS_EPOCH)
    if body.owner_epoch != ctx.owner_epoch:
        reasons.append(PermitReason.STALE_OWNER)
    if body.revocation_gen != _revocation_gen(cur):
        reasons.append(PermitReason.STALE_REVOCATION)
    reasons += _live_reasons(cur, str(body.tenant_id), str(body.run_id), now_ms, body.expires_at_ms)
    decision = _decision(cur, body.attempt_id)
    if decision["action_hash"] != body.action_hash or decision["tenant_id"] != str(body.tenant_id):
        reasons.append(PermitReason.ACTION_MISMATCH)
    descriptor = ActionDescriptor.model_validate_json(decision["descriptor"])
    denied = _denied(cur, decision["tenant_id"])
    if any(r.path in denied or any_match(denied, r.path) for r in descriptor.target_resources):
        reasons.append(PermitReason.RESOURCE_DENIED)
    policy = _policy(cur, decision["tenant_id"])
    if policy is None:
        reasons.append(PermitReason.POLICY_DENIED)
    else:
        reasons += _approval_reasons(cur, ctx, decision, policy, now_ms, unused=True)
    reasons += _reservation_reasons(
        cur, body.attempt_id, body.action_hash, str(body.reservation_id)
    )
    reasons += _intent_reasons(cur, body.attempt_id, body.action_hash)
    if reasons:
        raise PermitRejectedError(*dict.fromkeys(reasons))
    cur.execute(
        "UPDATE permits SET state = 'CONSUMED' WHERE permit_id = ? AND state = 'ISSUED'",
        (str(body.permit_id),),
    )
    if decision["approval_required"]:
        cur.execute(
            "UPDATE approvals SET uses = uses + 1 WHERE approval_id = ? AND revoked = 0",
            (decision["approval_id"],),
        )
    consume_reservation(
        cur,
        reservation_id=str(body.reservation_id),
        attempt_id=str(body.attempt_id),
        action_hash=body.action_hash,
        permit_id=str(body.permit_id),
        receipt_id=str(op.receipt_id),
        now_ms=now_ms,
    )
    receipt = Receipt(
        receipt_version=1,
        receipt_id=op.receipt_id,
        permit_id=body.permit_id,
        tenant_id=body.tenant_id,
        attempt_id=body.attempt_id,
        reservation_id=body.reservation_id,
        consumer=body.consumer,
        pep_instance_id=op.pep_instance_id,
        revocation_gen=body.revocation_gen,
        expires_at_ms=min(checked_add(now_ms, op.receipt_ttl_ms), body.expires_at_ms),
    )
    cur.execute(
        "INSERT INTO receipts (receipt_id, permit_id, attempt_id, tenant_id, run_id, body, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(op.receipt_id),
            str(body.permit_id),
            str(body.attempt_id),
            str(body.tenant_id),
            str(body.run_id),
            dump_json(receipt).decode(),
            ReceiptState.ISSUED.value,
        ),
    )
    _audit(
        cur,
        now_ms,
        "PERMIT_CONSUMED",
        {
            "attempt": str(body.attempt_id),
            "permit": str(body.permit_id),
            "receipt": str(op.receipt_id),
        },
        "ALLOW",
        None,
        body.policy_version,
        body.access_epoch,
    )


def _stored_receipt(
    cur: sqlite3.Cursor, receipt_id: str
) -> tuple[Receipt, ReceiptState, str] | None:
    row = cur.execute(
        "SELECT body, state, run_id FROM receipts WHERE receipt_id = ?", (str(receipt_id),)
    ).fetchone()
    if row is None:
        return None
    return Receipt.model_validate_json(row[0]), ReceiptState(row[1]), str(row[2])


def _receipt_reasons(
    cur: sqlite3.Cursor,
    receipt: Receipt,
    pep_instance_id: str,
    action_hash: str,
    now_ms: int,
    ctx: LedgerContext | None,
    *,
    wanted: ReceiptState,
) -> list[str]:
    stored = _stored_receipt(cur, receipt.receipt_id)
    if stored is None:
        return [PermitReason.UNKNOWN_RECEIPT]
    trusted, state, run_id = stored
    reasons: list[str] = []
    if receipt != trusted:
        reasons.append(PermitReason.RECEIPT_DIFFERS)
    if state is not wanted:
        reasons.append(
            PermitReason.RECEIPT_NOT_ISSUED
            if wanted is ReceiptState.ISSUED
            else PermitReason.RECEIPT_NOT_CLAIMED
        )
    if str(pep_instance_id) != str(trusted.pep_instance_id):
        reasons.append(PermitReason.PEP_MISMATCH)
    if trusted.revocation_gen != _revocation_gen(cur):
        reasons.append(PermitReason.STALE_REVOCATION)
    reasons += _live_reasons(cur, str(trusted.tenant_id), run_id, now_ms, trusted.expires_at_ms)
    decision = _decision(cur, trusted.attempt_id)
    if action_hash != decision["action_hash"]:
        reasons.append(PermitReason.ACTION_MISMATCH)
    policy = _policy(cur, decision["tenant_id"])
    if policy is None:
        reasons.append(PermitReason.POLICY_DENIED)
    elif ctx is not None and decision["approval_required"]:
        # Used once at consumption; it must still be unexpired and unrevoked.
        reasons += _approval_reasons(cur, ctx, decision, policy, now_ms, unused=False)
    return reasons


def _claim_receipt(
    cur: sqlite3.Cursor, op: ClaimReceipt, now_ms: int, ctx: LedgerContext | None
) -> None:
    """``ISSUED -> CLAIMED`` once (E 3.2 step 1) after all gate checks."""
    reasons = _receipt_reasons(
        cur,
        op.receipt,
        op.pep_instance_id,
        op.actual_action_hash,
        now_ms,
        ctx,
        wanted=ReceiptState.ISSUED,
    )
    if reasons:
        raise PermitRejectedError(*dict.fromkeys(reasons))
    cur.execute(
        "UPDATE receipts SET state = 'CLAIMED' WHERE receipt_id = ? AND state = 'ISSUED'",
        (str(op.receipt.receipt_id),),
    )
    _audit(
        cur,
        now_ms,
        "RECEIPT_CLAIMED",
        {"attempt": str(op.receipt.attempt_id), "receipt": str(op.receipt.receipt_id)},
        "ALLOW",
        None,
        None,
        None,
    )


def _use_receipt(cur: sqlite3.Cursor, reservation: dict[str, Any]) -> None:
    """``DISPATCH_STARTED``: the claimed receipt is used (never reusable)."""
    if reservation["receipt_id"] is None:
        return
    found = _stored_receipt(cur, reservation["receipt_id"])
    if found is None:
        return
    if found[1] is not ReceiptState.CLAIMED:
        raise PermitRejectedError(PermitReason.RECEIPT_NOT_CLAIMED)
    cur.execute(
        "UPDATE receipts SET state = 'USED' WHERE receipt_id = ?", (reservation["receipt_id"],)
    )


def verify_dispatch(
    cur: sqlite3.Cursor,
    receipt_id: str,
    *,
    pep_instance_id: str,
    action_hash: str,
    now_ms: int,
    ctx: LedgerContext | None,
) -> DispatchAuthorization:
    """Right before acting, a PEP confirms its claimed receipt against the trusted record."""
    stored = _stored_receipt(cur, receipt_id)
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    receipt = stored[0]
    reasons = _receipt_reasons(
        cur, receipt, pep_instance_id, action_hash, now_ms, ctx, wanted=ReceiptState.CLAIMED
    )
    if reasons:
        raise PermitRejectedError(*dict.fromkeys(reasons))
    decision = _decision(cur, receipt.attempt_id)
    return DispatchAuthorization(
        receipt=receipt,
        action_hash=decision["action_hash"],
        descriptor=ActionDescriptor.model_validate_json(decision["descriptor"]),
    )


def _stop_scope(cur: sqlite3.Cursor, op: StopScope, now_ms: int) -> None:
    """Stop (C1 11.4): the stop record first, then revocation. Outstanding permits
    and receipts in the scope are revoked; consumed ones stay for reconciliation."""
    row = cur.execute(
        "SELECT stop_seq FROM stop_state WHERE scope_type = ? AND scope_id = ?",
        (op.scope_type.value, op.scope_id),
    ).fetchone()
    seq = checked_add(0 if row is None else int(row[0]), 1)
    cur.execute(
        "INSERT INTO stop_state (scope_type, scope_id, stop_seq, status) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (scope_type, scope_id) DO UPDATE SET stop_seq = excluded.stop_seq, "
        "status = excluded.status",
        (op.scope_type.value, op.scope_id, seq, StopStatus.STOPPED.value),
    )
    tenants: tuple[str, ...] = ()
    if op.scope_type is StopScopeType.TENANT:
        tenants = (op.scope_id,)
    elif op.scope_type is StopScopeType.GLOBAL:
        tenants = _all_tenants(cur)
    _bump(cur, tenants=tenants)
    _revoke_outstanding(cur, op.scope_type, op.scope_id)
    _audit(
        cur,
        now_ms,
        "STOP",
        {"scope": f"{op.scope_type.value}:{op.scope_id}"},
        "STOP",
        None,
        None,
        None,
    )


def _resume_scope(cur: sqlite3.Cursor, op: ResumeScope, now_ms: int) -> None:
    row = cur.execute(
        "SELECT stop_seq, status FROM stop_state WHERE scope_type = ? AND scope_id = ?",
        (op.scope_type.value, op.scope_id),
    ).fetchone()
    if row is None or row[1] != StopStatus.STOPPED.value or int(row[0]) != op.stop_seq:
        raise PermitRejectedError(PermitReason.STOPPED, "STOP_SEQ_MISMATCH")
    cur.execute(
        "UPDATE stop_state SET status = ? WHERE scope_type = ? AND scope_id = ?",
        (StopStatus.RUNNING.value, op.scope_type.value, op.scope_id),
    )
    _bump(cur, tenants=())
    _audit(
        cur,
        now_ms,
        "RESUME",
        {"scope": f"{op.scope_type.value}:{op.scope_id}"},
        "ALLOW",
        None,
        None,
        None,
    )


def _revoke_tenant(cur: sqlite3.Cursor, tenant_id: str, now_ms: int, action: str) -> None:
    _bump(cur, tenants=(tenant_id,))
    _revoke_outstanding(cur, StopScopeType.TENANT, tenant_id)
    _audit(
        cur,
        now_ms,
        action,
        {"tenant": tenant_id},
        "REVOKE",
        None,
        None,
        _access_epoch(cur, tenant_id),
    )


def _revoke_approval(cur: sqlite3.Cursor, op: RevokeApproval, now_ms: int) -> None:
    if _approval_row(cur, op.approval_id) is None:
        raise PermitRejectedError(PermitReason.APPROVAL_MISSING)
    cur.execute("UPDATE approvals SET revoked = 1 WHERE approval_id = ?", (str(op.approval_id),))
    _bump(cur, tenants=())
    _audit(
        cur,
        now_ms,
        "APPROVAL_REVOKED",
        {"approval": str(op.approval_id)},
        "REVOKE",
        None,
        None,
        None,
    )


def _apply_authority(
    cur: sqlite3.Cursor, op: LedgerOp, now_ms: int, ctx: LedgerContext | None
) -> bool:
    """Authority operations; False when ``op`` is a cost operation."""
    if isinstance(op, RecordDecision):
        _record_decision(cur, op, now_ms, ctx)
    elif isinstance(op, RecordApproval):
        _record_approval(cur, op, now_ms, ctx)
    elif isinstance(op, ConfirmApproval):
        _confirm_approval(cur, op, now_ms, ctx)
    elif isinstance(op, RecordIntent):
        _record_intent(cur, op, now_ms)
    elif isinstance(op, IssuePermit):
        _issue_permit(cur, op, now_ms, ctx)
    elif isinstance(op, ConsumePermit):
        _consume_permit(cur, op, now_ms, ctx)
    elif isinstance(op, ClaimReceipt):
        _claim_receipt(cur, op, now_ms, ctx)
    elif isinstance(op, InstallPolicy):
        _install_policy(cur, op, now_ms)
    elif isinstance(op, InstallRunGrant):
        _install_grant(cur, op, now_ms)
    elif isinstance(op, StopScope):
        _stop_scope(cur, op, now_ms)
    elif isinstance(op, ResumeScope):
        _resume_scope(cur, op, now_ms)
    elif isinstance(op, RevokeAccess):
        _revoke_tenant(cur, str(op.tenant_id), now_ms, "ACCESS_REVOKED")
    elif isinstance(op, DenyResource):
        cur.execute(
            "INSERT OR IGNORE INTO deny_list (tenant_id, path) VALUES (?, ?)",
            (str(op.tenant_id), op.path),
        )
        _revoke_tenant(cur, str(op.tenant_id), now_ms, "RESOURCE_DENIED")
    elif isinstance(op, RevokeApproval):
        _revoke_approval(cur, op, now_ms)
    else:
        return False
    return True


# ---------------------------------------------------------------- authority reads


def read_permit(cur: sqlite3.Cursor, permit_id: str) -> tuple[Permit, PermitState] | None:
    return _stored_permit(cur, permit_id)


def read_receipt(cur: sqlite3.Cursor, receipt_id: str) -> tuple[Receipt, ReceiptState] | None:
    found = _stored_receipt(cur, receipt_id)
    return None if found is None else (found[0], found[1])


def read_stops(cur: sqlite3.Cursor) -> tuple[StopEntry, ...]:
    rows = cur.execute(
        "SELECT scope_type, scope_id, stop_seq, status FROM stop_state ORDER BY 1, 2"
    ).fetchall()
    return tuple(
        StopEntry(
            scope_type=StopScopeType(r[0]), scope_id=r[1], stop_seq=r[2], status=StopStatus(r[3])
        )
        for r in rows
    )


def read_audit(cur: sqlite3.Cursor) -> tuple[AuditView, ...]:
    rows = cur.execute(
        "SELECT audit_seq, action, decision, reason, prev_hash, record_hash FROM audit_log "
        "ORDER BY audit_seq"
    ).fetchall()
    return tuple(
        AuditView(
            audit_seq=r[0],
            action=r[1],
            decision=r[2],
            reason=r[3],
            prev_hash=r[4],
            record_hash=r[5],
        )
        for r in rows
    )


def read_approval_uses(cur: sqlite3.Cursor, approval_id: str) -> int | None:
    found = _approval_row(cur, approval_id)
    return None if found is None else found[1]


ALL_DDL: Final = LEDGER_DDL + AUTHORITY_DDL
