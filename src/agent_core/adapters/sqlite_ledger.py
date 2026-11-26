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

import sqlite3
from typing import Any, Final

from agent_core.cost.rules import (
    check_transition,
    checked_add,
    checked_sum,
    known_cost,
    required_amount,
)
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
    BudgetBlockedError,
    BudgetCapMissingError,
    BudgetExceededError,
    CounterCapExceededError,
    DuplicateUsageReportError,
    LedgerPairingError,
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
    Correct,
    LedgerOp,
    MarkActive,
    MarkUnknown,
    Reconcile,
    RecoverInFlight,
    ReleaseReservation,
    ReportUsage,
    SetBudgetCap,
    SetCounterCap,
    SetRunWindow,
    Unexplained,
    VoidAllocation,
)
from agent_core.types.model import UsageStatus
from agent_core.types.records import AttemptState
from agent_core.types.safety import ActionKind
from agent_core.types.transitions import (
    AbortEvidence,
    AttemptTransition,
    EntityAction,
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


def apply_op(cur: sqlite3.Cursor, op: LedgerOp, *, now_ms: int) -> None:
    """Apply one operation inside the caller's transaction (pairing is checked by
    the journal before this is called)."""
    if type(now_ms) is not int or not 0 <= now_ms <= MAX_INT64:
        raise ReservationStateError("CLOCK_INVALID")
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
    AttemptState.BUDGET_RESERVED: "activate",
}
_BOUND_TO: Final = {
    "allocate": AttemptState.CONTENT_FIXED,
    "activate": AttemptState.BUDGET_RESERVED,
    "mark_active": AttemptState.DISPATCH_STARTED,
    "release": AttemptState.ABORTED_BEFORE_DISPATCH,
}


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
            op
            for op in ops
            if op.op == wanted and getattr(op, "attempt_id", None) == transition.after.attempt_id
        ]
        if len(found) != 1:
            raise LedgerPairingError(f"MISSING_{wanted.upper()}")
    for op in ops:
        state = _BOUND_TO.get(op.op)
        if state is None:
            continue
        attempt_id = getattr(op, "attempt_id", None)
        matches = [
            t for t in transitions if t.after.state is state and t.after.attempt_id == attempt_id
        ]
        if len(matches) != 1:
            raise LedgerPairingError(f"UNPAIRED_{op.op.upper()}")
        after = matches[0].after
        if getattr(op, "reservation_id", None) != after.reservation_id:
            raise LedgerPairingError("RESERVATION_DIFFERS")
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
            evidence = matches[0].evidence
            proof = evidence.not_dispatched_ref if isinstance(evidence, AbortEvidence) else None
            if op.not_dispatched_ref != proof:
                raise LedgerPairingError("NOT_DISPATCHED_REPORT_DIFFERS")


def check_standalone(ops: tuple[LedgerOp, ...]) -> None:
    for op in ops:
        if op.op not in STANDALONE_OPS:
            raise LedgerPairingError(f"NOT_STANDALONE_{op.op.upper()}")


def check_owner_ops(ops: tuple[LedgerOp, ...]) -> None:
    for op in ops:
        if op.op not in OWNER_OPS:
            raise LedgerPairingError(f"NOT_OWNER_{op.op.upper()}")
