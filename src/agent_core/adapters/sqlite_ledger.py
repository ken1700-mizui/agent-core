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
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Final

from agent_core.cost.rules import (
    ceil_div,
    check_transition,
    checked_add,
    checked_mul,
    checked_sum,
    known_cost,
    required_for,
)
from agent_core.safety import reports
from agent_core.safety.approval import check_approval, verify_signature
from agent_core.safety.descriptor import rebuild
from agent_core.safety.paths import any_match
from agent_core.safety.pdp import decide, require_allowed
from agent_core.types.cost import (
    MICRO_PER_MTOK,
    BudgetScopeType,
    CounterKind,
    CounterView,
    LedgerEntryView,
    PriceTable,
    ReservationState,
    ReservationView,
    ScopeUsage,
)
from agent_core.types.errors import (
    ActionDescriptorError,
    ApprovalRejectedError,
    AuditRecursionError,
    BudgetBlockedError,
    BudgetCapMissingError,
    BudgetExceededError,
    CompletionRefusedError,
    CostBasisMissingError,
    CounterCapExceededError,
    DuplicateUsageReportError,
    LedgerInconsistencyError,
    LedgerKeyMissingError,
    LedgerPairingError,
    OwnerAuthorizationError,
    PermitRejectedError,
    ReservationAmountError,
    ReservationExpiredError,
    ReservationMismatchError,
    ReservationReuseError,
    ReservationStateError,
)
from agent_core.types.events import Event, EventDraft
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
    InstallPriceTable,
    InstallRunGrant,
    IssuePermit,
    LedgerOp,
    MarkActive,
    MarkUnknown,
    Reconcile,
    ReconcileOutcome,
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
    SetStepCapDefault,
    StopScope,
    Unexplained,
    VoidAllocation,
)
from agent_core.types.model import Usage, UsageStatus
from agent_core.types.records import AttemptState
from agent_core.types.safety import (
    AUDIT_HASH_DOMAIN,
    PERMIT_MAC_DOMAIN,
    ActionDescriptor,
    ActionKind,
    ApprovalReason,
    AuditView,
    Consumer,
    ExactApproval,
    LedgerKeys,
    ModelCostBasis,
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
    ToolCostBasis,
)
from agent_core.types.serialization import dump_json
from agent_core.types.tools import ResourceAccess, ToolEffectClass, ToolRegistration
from agent_core.types.transitions import (
    AbortEvidence,
    AttemptTransition,
    EntityAction,
    PolicyDecisionEvidence,
    ReconciliationEvidence,
    RuntimePayload,
    RunTransition,
    StepTransition,
    TaskTransition,
)
from agent_core.types.values import MAX_INT64, NonMonetaryCaps

R = ReservationState

LEDGER_DDL: Final = (
    """CREATE TABLE budget_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    cap_micro INTEGER NOT NULL CHECK (cap_micro >= 0),
    PRIMARY KEY (scope_type, scope_id)
) STRICT""",
    # WP-15: the owner's cap for the Steps of a Run that have none of their own.
    """CREATE TABLE step_cap_defaults (
    run_id TEXT PRIMARY KEY,
    cap_micro INTEGER NOT NULL CHECK (cap_micro >= 0)
) STRICT""",
    """CREATE TABLE counter_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    cap INTEGER NOT NULL CHECK (cap >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT""",
    """CREATE TABLE counter_usage (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT""",
    """CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT""",
    """CREATE TABLE price_tables (
    price_table_id TEXT NOT NULL,
    version TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (price_table_id, version)
) STRICT""",
    """CREATE TABLE rt_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    budget_micro INTEGER NOT NULL CHECK (budget_micro >= 0),
    caps TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT""",
    """CREATE TABLE rt_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT""",
    """CREATE TABLE rt_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL
) STRICT""",
    # WP12-R05: when the ledger first saw a Task or Run (its own time). A source of
    # truth (it starts the Run's time window), never only a projection: the mirror's
    # copy in rt_tasks / rt_runs is checked against it and repaired from it.
    """CREATE TABLE entity_times (
    entity_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT""",
    # WP12-R04: since when (ledger time) a Run has had its current status; changed
    # only when the status changes, so other events never reset a deadline.
    """CREATE TABLE run_status_times (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    since_ms INTEGER NOT NULL CHECK (since_ms >= 0)
) STRICT""",
    # WP15-R07: what a started operation produced, as the live gate checked it (a
    # read's bytes by digest), from its MAC-signed execution report. Written once.
    """CREATE TABLE operation_results (
    attempt_id TEXT PRIMARY KEY,
    result_digest TEXT NOT NULL
) STRICT""",
    """CREATE TABLE rt_attempts (
    attempt_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    state TEXT NOT NULL
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
    action_kind TEXT,
    state TEXT NOT NULL,
    action_hash TEXT,
    cost_cap_micro INTEGER CHECK (cost_cap_micro >= 0),
    required_micro INTEGER CHECK (required_micro >= 0),
    reserved_micro INTEGER NOT NULL CHECK (reserved_micro >= 0),
    hold_micro INTEGER NOT NULL CHECK (hold_micro >= 0),
    spent_micro INTEGER NOT NULL CHECK (spent_micro >= 0),
    adjust_micro INTEGER NOT NULL CHECK (adjust_micro >= 0),
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
    "experiment_id, action_kind, state, action_hash, cost_cap_micro, required_micro, "
    "reserved_micro, hold_micro, spent_micro, adjust_micro, spent_lower_micro, spent_known, "
    "allocated_at_ms, expires_at_ms, permit_id, receipt_id"
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
# A Run in one of these states runs nothing new; cancelling also stops it (K1, WP09-R05).
_CLOSED_RUN_STATUSES: Final = frozenset(
    {"CANCELLING", "CANCELLED", "COMPLETED", "FAILED", "ABORTED_SAFETY"}
)
_UNSETTLED_STATES: Final = (
    R.RESERVED.value,
    R.CONSUMED.value,
    R.ACTIVE.value,
    R.UNKNOWN.value,
)


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
    """(held, spent) of one scope. Spent is the base cost plus every adjustment."""
    rows = cur.execute(
        "SELECT hold_micro, spent_micro, adjust_micro FROM reservations "  # noqa: S608
        f"WHERE {_SCOPE_COLUMNS[kind]} = ?",
        (scope_id,),
    ).fetchall()
    held = checked_sum(r[0] for r in rows)
    spent = checked_sum(checked_add(r[1], r[2]) for r in rows)
    return held, spent


def _effective_cap(cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str) -> int | None:
    """The owner's cap; for a Task, never above the Task's own ``budget_total``."""
    found = cur.execute(
        "SELECT cap_micro FROM budget_caps WHERE scope_type = ? AND scope_id = ?",
        (kind.value, scope_id),
    ).fetchone()
    cap = None if found is None else int(found[0])
    if cap is None and kind is BudgetScopeType.STEP:
        # The owner's default for this Run's Steps (SetStepCapDefault), never a guess.
        default = cur.execute(
            "SELECT d.cap_micro FROM step_cap_defaults d JOIN rt_steps s ON s.run_id = d.run_id "
            "WHERE s.step_id = ?",
            (scope_id,),
        ).fetchone()
        cap = None if default is None else int(default[0])
    if kind is BudgetScopeType.TASK:
        task = cur.execute(
            "SELECT budget_micro FROM rt_tasks WHERE task_id = ?", (scope_id,)
        ).fetchone()
        if task is not None:
            cap = int(task[0]) if cap is None else min(cap, int(task[0]))
    return cap


def _check_overrun(cur: sqlite3.Cursor, row: dict[str, Any], now_ms: int) -> None:
    """After a cost change: any scope above its cap records an OVERRUN and stops new
    reservations there (E 4.1). The cost itself is kept in full."""
    for kind, scope_id in _scopes_of(row):
        cap = _effective_cap(cur, kind, scope_id)
        if cap is None:
            continue
        total = checked_sum(_totals(cur, kind, scope_id))
        if total > cap:
            _entry(cur, row, "OVERRUN", now_ms, delta=total - cap)
            _block(cur, row, (kind,), "OVERRUN")


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
    """Record the base cost. Adjustments (CORRECTION, UNEXPLAINED) stay as they are."""
    row = _move(cur, row, target, now_ms, hold_micro=0, spent_micro=actual, spent_known=1)
    if actual > row["reserved_micro"]:
        # The actual amount is recorded in full; new reservations stop (E 4.1).
        _entry(cur, row, "OVERRUN", now_ms, delta=actual - row["reserved_micro"])
        _block(cur, row, _OVERRUN_SCOPES, "OVERRUN")
    _check_overrun(cur, row, now_ms)
    return row


# ---------------------------------------------------------------- operations


def _chain(cur: sqlite3.Cursor, attempt_id: str) -> dict[str, str]:
    """The journaled Task-Run-Step-Attempt chain of an attempt (WP09-R06)."""
    row = cur.execute(
        "SELECT a.step_id, s.run_id, r.task_id, r.tenant_id FROM rt_attempts a "
        "JOIN rt_steps s ON s.step_id = a.step_id JOIN rt_runs r ON r.run_id = s.run_id "
        "WHERE a.attempt_id = ?",
        (str(attempt_id),),
    ).fetchone()
    if row is None:
        raise ReservationMismatchError("ATTEMPT_CHAIN_UNKNOWN")
    return {"step_id": row[0], "run_id": row[1], "task_id": row[2], "tenant_id": row[3]}


def _allocate(cur: sqlite3.Cursor, op: AllocateReservation, now_ms: int) -> None:
    if _row(cur, op.reservation_id) is not None:
        raise ReservationReuseError("RESERVATION_ID_USED")
    taken = cur.execute(
        "SELECT 1 FROM reservations WHERE attempt_id = ?", (str(op.attempt_id),)
    ).fetchone()
    if taken is not None:
        raise ReservationReuseError("ATTEMPT_HAS_RESERVATION")
    # Every scope from trusted state: the journaled chain and the Run's grant.
    chain = _chain(cur, op.attempt_id)
    found = cur.execute(
        "SELECT body FROM run_grants WHERE run_id = ?", (chain["run_id"],)
    ).fetchone()
    if found is None:
        raise BudgetCapMissingError("RUN_GRANT")
    grant = RunGrant.model_validate_json(found[0])
    if (str(grant.tenant_id), str(grant.task_id)) != (chain["tenant_id"], chain["task_id"]):
        raise ReservationMismatchError("GRANT_SCOPE")
    expires = checked_add(now_ms, op.ttl_ms)
    values = {
        "reservation_id": str(op.reservation_id),
        "attempt_id": str(op.attempt_id),
        "tenant_id": chain["tenant_id"],
        "user_id": str(grant.user_id),
        "task_id": chain["task_id"],
        "run_id": chain["run_id"],
        "step_id": chain["step_id"],
        "period_id": grant.period_id,
        "experiment_id": None if grant.experiment_id is None else str(grant.experiment_id),
        "action_kind": None,
        "state": R.ID_ALLOCATED.value,
        "action_hash": None,
        "cost_cap_micro": None,
        "required_micro": None,
        "reserved_micro": 0,
        "hold_micro": 0,
        "spent_micro": 0,
        "adjust_micro": 0,
        "spent_lower_micro": 0,
        "spent_known": 1,
        "allocated_at_ms": now_ms,
        "expires_at_ms": expires,
        "permit_id": None,
        "receipt_id": None,
    }
    cur.execute(
        f"INSERT INTO reservations ({_ROW}) VALUES ({', '.join('?' * len(_NAMES))})",  # noqa: S608
        tuple(values[name] for name in _NAMES),
    )
    row = _row(cur, op.reservation_id)
    assert row is not None  # noqa: S101 (just inserted in this transaction)
    _entry(cur, row, "STATE", now_ms)


_TASK_CAP_FIELDS: Final = {
    CounterKind.MODEL_CALLS: "model_calls",
    CounterKind.TOOL_CALLS: "tool_calls",
    CounterKind.RETRIES: "retries",
    CounterKind.WORK_UNITS: "total_operations",
}


def _task_caps(cur: sqlite3.Cursor, task_id: str) -> tuple[dict[str, int], int] | None:
    found = cur.execute(
        "SELECT caps, created_at_ms FROM rt_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return None if found is None else (json.loads(found[0]), int(found[1]))


def _counter_cap(
    cur: sqlite3.Cursor,
    scope: BudgetScopeType,
    scope_id: str,
    counter: CounterKind,
    task: dict[str, int] | None,
) -> int | None:
    """The owner's counter cap, never above the Task's ``caps_total`` (WP09-R02).
    A Task scope without an owner cap uses the Task's own cap."""
    found = cur.execute(
        "SELECT cap FROM counter_caps WHERE scope_type = ? AND scope_id = ? AND counter = ?",
        (scope.value, scope_id, counter.value),
    ).fetchone()
    cap = None if found is None else int(found[0])
    if task is not None:
        task_cap = int(task[_TASK_CAP_FIELDS[counter]])
        if cap is None and scope is BudgetScopeType.TASK:
            return task_cap
        if cap is not None:
            cap = min(cap, task_cap)
    return cap


def _used(cur: sqlite3.Cursor, scope: BudgetScopeType, scope_id: str, counter: CounterKind) -> int:
    found = cur.execute(
        "SELECT used FROM counter_usage WHERE scope_type = ? AND scope_id = ? AND counter = ?",
        (scope.value, scope_id, counter.value),
    ).fetchone()
    return 0 if found is None else int(found[0])


def _elapsed_reasons(cur: sqlite3.Cursor, run_id: str, task_id: str, now_ms: int) -> list[str]:
    """Run and Task elapsed time, from their journaled creation (WP09-R02, R05)."""
    reasons: list[str] = []
    run = cur.execute("SELECT created_at_ms FROM rt_runs WHERE run_id = ?", (run_id,)).fetchone()
    window = cur.execute(
        "SELECT max_elapsed_ms FROM run_windows WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or window is None:
        raise BudgetCapMissingError("ELAPSED_MS")
    task = _task_caps(cur, task_id)
    limit = int(window[0])
    if task is not None:
        limit = min(limit, int(task[0]["wall_clock_ms"]))
        if now_ms - task[1] > int(task[0]["wall_clock_ms"]):
            reasons.append("ELAPSED_MS")
    if now_ms < int(run[0]) or now_ms - int(run[0]) > limit:
        reasons.append("ELAPSED_MS")
    return reasons


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
    found_task = _task_caps(cur, row["task_id"])
    task = None if found_task is None else found_task[0]
    exceeded: list[str] = []
    for counter in counters:
        for scope in (BudgetScopeType.RUN, BudgetScopeType.TASK):
            scope_id = row[_SCOPE_COLUMNS[scope]]
            cap = _counter_cap(cur, scope, scope_id, counter, task)
            if cap is None:
                raise BudgetCapMissingError(f"{scope.value}_{counter.value}")
            used = checked_add(_used(cur, scope, scope_id, counter), 1)
            if used > cap:
                exceeded.append(counter.value)
                continue
            cur.execute(
                "INSERT INTO counter_usage (scope_type, scope_id, counter, used) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (scope_type, scope_id, counter) "
                "DO UPDATE SET used = excluded.used",
                (scope.value, scope_id, counter.value, used),
            )
    exceeded += _elapsed_reasons(cur, row["run_id"], row["task_id"], now_ms)
    if exceeded:
        raise CounterCapExceededError(*dict.fromkeys(exceeded))


def _price_table(cur: sqlite3.Cursor, table_id: str, version: str) -> PriceTable | None:
    found = cur.execute(
        "SELECT body FROM price_tables WHERE price_table_id = ? AND version = ?",
        (table_id, version),
    ).fetchone()
    return None if found is None else PriceTable.model_validate_json(found[0])


def required_of(cur: sqlite3.Cursor, descriptor: ActionDescriptor) -> int:
    """The trusted required amount of a recorded descriptor (WP09-R01)."""
    basis = descriptor.cost_basis
    table = None
    if isinstance(basis, ModelCostBasis):
        table = _price_table(cur, basis.price_table_id, basis.price_table_version)
    return required_for(basis, table)


def _activate(cur: sqlite3.Cursor, op: ActivateReservation, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    check_transition(ReservationState(row["state"]), R.RESERVED)
    if now_ms >= row["expires_at_ms"]:
        raise ReservationExpiredError("ALLOCATION_EXPIRED")
    decided = cur.execute(
        "SELECT action_hash, descriptor, approval_required, approval_id FROM decisions "
        "WHERE attempt_id = ?",
        (row["attempt_id"],),
    ).fetchone()
    if decided is None or decided[0] != op.action_hash:
        raise ReservationMismatchError("NO_DECISION_FOR_ACTION")
    descriptor = ActionDescriptor.model_validate_json(decided[1])
    if descriptor.cost_cap != op.cost_cap:
        raise ReservationMismatchError("COST_CAP_DIFFERS")
    if decided[2] and decided[3] is None:
        raise ReservationStateError("APPROVAL_REQUIRED")
    # The required amount comes from the recorded basis, never from the caller.
    required = required_of(cur, descriptor)
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
        cap = _effective_cap(cur, kind, scope_id)
        if cap is None:
            raise BudgetCapMissingError(kind.value)
        hold, spent = _totals(cur, kind, scope_id)
        if checked_sum((hold, spent, amount)) > cap:
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
        required_micro=required,
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


def _report_usage(
    cur: sqlite3.Cursor, op: ReportUsage, now_ms: int, ctx: LedgerContext | None
) -> None:
    """A trusted execution report (WP09-R04): the gate's MAC, bound to this
    reservation, its used receipt and the live owner. The cost is derived from the
    recorded cost basis (synthetic billing); a stated cost that differs is refused.
    The hold is released only when the report is final and the billing scheme says
    a final report is complete; otherwise it stays (UNKNOWN)."""
    keys = _keys(ctx)
    assert ctx is not None  # noqa: S101 (checked by _keys)
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    report = op.report
    if not reports.verify(keys.permit, report):
        raise PermitRejectedError(PermitReason.REPORT_MAC_INVALID)
    if (str(report.reservation_id), str(report.attempt_id), str(report.receipt_id)) != (
        row["reservation_id"],
        row["attempt_id"],
        row["receipt_id"],
    ):
        raise PermitRejectedError(PermitReason.REPORT_MISMATCH)
    stored = _stored_receipt(cur, str(report.receipt_id))
    if stored is None or stored[1] is not ReceiptState.USED:
        raise PermitRejectedError(PermitReason.REPORT_MISMATCH, "RECEIPT_NOT_USED")
    receipt = stored[0]
    if (str(receipt.pep_instance_id), receipt.owner_epoch) != (
        str(report.pep_instance_id),
        report.owner_epoch,
    ) or report.owner_epoch != ctx.owner_epoch:
        raise PermitRejectedError(PermitReason.REPORT_MISMATCH, PermitReason.STALE_OWNER)
    seen = cur.execute(
        "SELECT 1 FROM ledger_entries WHERE reservation_id = ? AND usage_event_id = ?",
        (row["reservation_id"], op.usage_event_id),
    ).fetchone()
    if seen is not None:
        raise DuplicateUsageReportError("USAGE_EVENT_SEEN")
    if row["state"] != R.ACTIVE.value:
        raise ReservationStateError("NOT_ACTIVE")
    cost, complete = _billed_cost(cur, row["attempt_id"], report.usage)
    stated = known_cost(report.usage)
    if stated is not None and cost is not None and stated != cost:
        raise PermitRejectedError(PermitReason.USAGE_COST_MISMATCH)
    final = report.final and report.usage.status is UsageStatus.FINAL and cost is not None
    if final and complete:
        assert cost is not None  # noqa: S101 (checked by final)
        row = _record_cost(cur, row, R.USAGE_REPORTED, cost, now_ms)
    else:
        # Not final, cost unknown or the billing scheme not complete: keep the whole
        # hold; a known cost is a lower bound only, and unknown is never zero.
        lower = max(row["spent_lower_micro"], cost or 0, stated or 0)
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
    _record_result(cur, str(op.attempt_id), report.result_digest)


def _billed_cost(cur: sqlite3.Cursor, attempt_id: str, usage: Usage) -> tuple[int | None, bool]:
    """(cost, complete) from the synthetic billing scheme (WP09-R04).

    A tool call costs its manifest estimate. A model call costs its known tokens
    times the owner's price table (rounded up); unknown tokens give no cost."""
    found = cur.execute(
        "SELECT descriptor FROM decisions WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if found is None:
        raise PermitRejectedError(PermitReason.DECISION_MISSING)
    basis = ActionDescriptor.model_validate_json(found[0]).cost_basis
    if isinstance(basis, ToolCostBasis):
        return basis.estimate.micro_units, True
    table = _price_table(cur, basis.price_table_id, basis.price_table_version)
    item = None if table is None else table.item(basis.model_id)
    if table is None or item is None:
        raise CostBasisMissingError("PRICE_TABLE_MISSING")
    if usage.input_tokens is None or usage.output_tokens is None:
        return None, table.final_usage_is_complete
    cost = checked_add(
        ceil_div(checked_mul(usage.input_tokens, item.input_micro_per_mtok), MICRO_PER_MTOK),
        ceil_div(checked_mul(usage.output_tokens, item.output_micro_per_mtok), MICRO_PER_MTOK),
    )
    return cost, table.final_usage_is_complete


def _mark_unknown(cur: sqlite3.Cursor, op: MarkUnknown, now_ms: int) -> None:
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    _move(cur, row, R.UNKNOWN, now_ms, hold_micro=row["reserved_micro"], spent_known=0)


def _record_time(cur: sqlite3.Cursor, entity_id: str, created_at_ms: int) -> None:
    cur.execute(
        "INSERT INTO entity_times (entity_id, created_at_ms) VALUES (?, ?)",
        (entity_id, created_at_ms),
    )


def _entity_time(cur: sqlite3.Cursor, entity_id: str) -> int:
    row = cur.execute(
        "SELECT created_at_ms FROM entity_times WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise LedgerInconsistencyError("ENTITY_TIME_MISSING")  # not derivable: refused
    return int(row[0])


def _record_result(cur: sqlite3.Cursor, attempt_id: str, digest: str | None) -> None:
    """WP15-R07: the operation's result digest from a verified report, once."""
    if digest is None:
        return
    found = cur.execute(
        "SELECT result_digest FROM operation_results WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if found is not None and found[0] != digest:
        raise PermitRejectedError(PermitReason.REPORT_MISMATCH, "RESULT_DIFFERS")
    if found is None:
        cur.execute(
            "INSERT INTO operation_results (attempt_id, result_digest) VALUES (?, ?)",
            (attempt_id, digest),
        )


def _mark_active(
    cur: sqlite3.Cursor, op: MarkActive, now_ms: int, ctx: LedgerContext | None
) -> None:
    """``DISPATCH_STARTED`` (WP09-R03): the start is committed here, once. The
    receipt must exist, be CLAIMED by this gate under the live owner, and every
    live check (stop, revocation, expiry, elapsed time, approval) must hold."""
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    if row["receipt_id"] is None or row["receipt_id"] != str(op.receipt_id):
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    stored = _stored_receipt(cur, str(op.receipt_id))
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    decision = _decision(cur, row["attempt_id"])
    reasons = _receipt_reasons(
        cur,
        stored[0],
        str(op.pep_instance_id),
        decision["action_hash"],
        now_ms,
        ctx,
        wanted=ReceiptState.CLAIMED,
    )
    if reasons:
        raise PermitRejectedError(*dict.fromkeys(reasons))
    cur.execute(
        "UPDATE receipts SET state = 'USED' WHERE receipt_id = ? AND state = 'CLAIMED'",
        (str(op.receipt_id),),
    )
    _move(cur, row, R.ACTIVE, now_ms)
    _audit(
        cur,
        now_ms,
        "DISPATCH_STARTED",
        {"attempt": row["attempt_id"], "receipt": str(op.receipt_id)},
        "ALLOW",
        None,
        None,
        None,
    )


def _release(
    cur: sqlite3.Cursor, op: ReleaseReservation, now_ms: int, ctx: LedgerContext | None
) -> None:
    """Release a hold. After consumption only with the live gate's report that the
    operation was not started (E.Z6, WP09-R04): MAC, bindings to receipt, permit,
    attempt and reservation, the live owner epoch and gate instance, a receipt that
    was never used, and a report never used before."""
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    report = op.report
    if report is None:
        if row["state"] == R.CONSUMED.value:
            raise ReservationStateError("NOT_DISPATCHED_REPORT_REQUIRED")
        _move(cur, row, R.RELEASED, now_ms, hold_micro=0)
        return
    if row["state"] != R.CONSUMED.value:
        raise ReservationStateError("REPORT_FOR_UNCONSUMED")
    keys = _keys(ctx)
    assert ctx is not None  # noqa: S101 (checked by _keys)
    if not reports.verify(keys.permit, report):
        raise PermitRejectedError(PermitReason.REPORT_MAC_INVALID)
    bound = (
        str(report.reservation_id),
        str(report.attempt_id),
        str(report.receipt_id),
        str(report.permit_id),
    )
    if bound != (row["reservation_id"], row["attempt_id"], row["receipt_id"], row["permit_id"]):
        raise PermitRejectedError(PermitReason.REPORT_MISMATCH)
    stored = _stored_receipt(cur, str(report.receipt_id))
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    receipt, state, _run = stored
    if state is ReceiptState.USED:
        raise PermitRejectedError(PermitReason.ALREADY_STARTED)
    if state is ReceiptState.NOT_DISPATCHED:
        raise PermitRejectedError(PermitReason.REPORT_USED)
    if str(receipt.pep_instance_id) != str(report.pep_instance_id):
        raise PermitRejectedError(PermitReason.PEP_MISMATCH)
    if not receipt.owner_epoch == report.owner_epoch == ctx.owner_epoch:
        raise PermitRejectedError(PermitReason.STALE_OWNER)
    if cur.execute(
        "SELECT 1 FROM not_dispatched WHERE receipt_id = ?", (str(report.receipt_id),)
    ).fetchone():
        raise PermitRejectedError(PermitReason.REPORT_USED)
    cur.execute(
        "INSERT INTO not_dispatched (receipt_id, report_ref, recorded_at_ms) VALUES (?, ?, ?)",
        (str(report.receipt_id), reports.report_ref(report), now_ms),
    )
    cur.execute(
        "UPDATE receipts SET state = 'NOT_DISPATCHED' WHERE receipt_id = ?",
        (str(report.receipt_id),),
    )
    _move(cur, row, R.RELEASED, now_ms, hold_micro=0)
    _audit(
        cur,
        now_ms,
        "NOT_DISPATCHED",
        {"attempt": row["attempt_id"], "receipt": str(report.receipt_id)},
        "RELEASE",
        None,
        None,
        None,
    )


_OUTCOME_SETTLED: Final = frozenset(
    {AttemptState.COMPLETED.value, AttemptState.FAILED_DEFINITE.value}
)


def _refuse_cost_only(cur: sqlite3.Cursor, row: dict[str, Any]) -> None:
    """QR-01: a cost alone is settled only for an Attempt whose outcome is settled.
    An unknown (or not yet ended) outcome and its cost are settled together, by
    ``ReconcileOutcome``; a cost settled apart would leave the outcome unsettleable.
    The state is the journal's projection, written in the same transaction as each
    event and checked against the journal at every open."""
    found = cur.execute(
        "SELECT state FROM rt_attempts WHERE attempt_id = ?", (row["attempt_id"],)
    ).fetchone()
    if found is None or found[0] not in _OUTCOME_SETTLED:
        raise ReservationStateError("OUTCOME_NOT_SETTLED")


def _reconcile(cur: sqlite3.Cursor, op: Reconcile, now_ms: int) -> None:
    """The owner's cost-only reconciliation of a settled outcome's bill."""
    row = _row(cur, op.reservation_id)
    if row is None:
        raise ReservationStateError("NOT_ALLOCATED")
    _refuse_cost_only(cur, row)
    actual = op.actual.micro_units
    if row["spent_known"] and actual < row["spent_micro"]:
        # Lowering a recorded final cost frees budget: owner-only, not implemented.
        raise ReservationAmountError("BELOW_RECORDED_COST")
    if actual < row["spent_lower_micro"]:
        raise ReservationAmountError("BELOW_LOWER_BOUND")
    row = _record_cost(cur, row, R.RECONCILED, actual, now_ms)
    _entry(cur, row, "RECONCILIATION", now_ms, evidence_ref=op.evidence_ref)


def _reconcile_outcome(
    cur: sqlite3.Cursor, op: ReconcileOutcome, now_ms: int, ctx: LedgerContext | None
) -> None:
    """WP12-R02: an unknown outcome and its cost, settled together (see the type)."""
    keys = _keys(ctx)
    assert ctx is not None  # noqa: S101 (checked by _keys)
    row = _bound_row(cur, op.reservation_id, op.attempt_id)
    if row["state"] != R.UNKNOWN.value:
        raise ReservationStateError("NOT_UNKNOWN")  # settled already, or never unknown
    stored = None if row["receipt_id"] is None else _stored_receipt(cur, row["receipt_id"])
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    receipt, receipt_state, _run = stored
    if op.basis == "NOT_STARTED":
        # The start is recorded with the receipt (USED); a receipt revoked by a
        # later owner was never used: the operation never ran.
        if receipt_state is not ReceiptState.REVOKED or receipt.owner_epoch >= ctx.owner_epoch:
            raise ReservationStateError("NOT_PROVEN_NOT_STARTED")
        actual = 0
    else:
        report = op.report
        assert report is not None  # noqa: S101 (by the type)
        if not reports.verify(keys.permit, report):
            raise PermitRejectedError(PermitReason.REPORT_MAC_INVALID)
        if (
            (str(report.reservation_id), str(report.attempt_id), str(report.receipt_id))
            != (row["reservation_id"], row["attempt_id"], row["receipt_id"])
            or str(report.pep_instance_id) != str(receipt.pep_instance_id)
            or report.owner_epoch != receipt.owner_epoch
        ):
            raise PermitRejectedError(PermitReason.REPORT_MISMATCH)
        if receipt_state is not ReceiptState.USED:
            raise PermitRejectedError(PermitReason.REPORT_MISMATCH, "RECEIPT_NOT_USED")
        if (report.outcome == "COMPLETED") != (op.outcome == "COMPLETED"):
            raise PermitRejectedError(PermitReason.REPORT_MISMATCH, "OUTCOME_DIFFERS")
        cost, complete = _billed_cost(cur, row["attempt_id"], report.usage)
        stated = report.usage.cost.micro_units if report.usage.cost is not None else None
        if stated is not None and cost is not None and stated != cost:
            raise PermitRejectedError(PermitReason.USAGE_COST_MISMATCH)
        if (
            not (report.final and report.usage.status is UsageStatus.FINAL and complete)
            or cost is None
        ):
            raise ReservationStateError("COST_NOT_FINAL")  # never inferred, never zero
        actual = cost
    if actual < row["spent_lower_micro"]:
        # QR-01: a known lower bound (a partial report) is never undercut.
        raise ReservationAmountError("BELOW_LOWER_BOUND")
    if op.report is not None:
        _record_result(cur, row["attempt_id"], op.report.result_digest)
    row = _record_cost(cur, row, R.RECONCILED, actual, now_ms)
    _entry(cur, row, "RECONCILIATION", now_ms, evidence_ref=op.evidence_ref)


def _adjust(cur: sqlite3.Cursor, op: Correct | Unexplained, now_ms: int) -> None:
    """An adjustment is kept apart from the base cost, so a later reconciliation
    never erases it; a total above a cap is an overrun (WP09-R02)."""
    row = _row(cur, op.reservation_id)
    if row is None or row["state"] in (R.ID_ALLOCATED.value, R.VOID.value):
        raise ReservationStateError("NOTHING_TO_ADJUST")
    row = _update(cur, row, adjust_micro=checked_add(row["adjust_micro"], op.delta_micro))
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
    _check_overrun(cur, row, now_ms)


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
    cur: sqlite3.Cursor,
    op: SetBudgetCap | SetCounterCap | SetRunWindow | InstallPriceTable,
    now_ms: int,
) -> None:
    if isinstance(op, SetBudgetCap):
        old = cur.execute(
            "SELECT cap_micro FROM budget_caps WHERE scope_type = ? AND scope_id = ?",
            (op.scope_type.value, op.scope_id),
        ).fetchone()
        if old is not None and op.cap.micro_units < int(old[0]):
            _revoke_in_scope(cur, op.scope_type, op.scope_id, now_ms)
        cur.execute(
            "INSERT INTO budget_caps (scope_type, scope_id, cap_micro) VALUES (?, ?, ?) "
            "ON CONFLICT (scope_type, scope_id) DO UPDATE SET cap_micro = excluded.cap_micro",
            (op.scope_type.value, op.scope_id, op.cap.micro_units),
        )
    elif isinstance(op, SetCounterCap):
        old = cur.execute(
            "SELECT cap FROM counter_caps WHERE scope_type = ? AND scope_id = ? AND counter = ?",
            (op.scope_type.value, op.scope_id, op.counter.value),
        ).fetchone()
        if old is not None and op.cap < int(old[0]):
            _revoke_in_scope(cur, op.scope_type, op.scope_id, now_ms)
        cur.execute(
            "INSERT INTO counter_caps (scope_type, scope_id, counter, cap) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (scope_type, scope_id, counter) DO UPDATE SET cap = excluded.cap",
            (op.scope_type.value, op.scope_id, op.counter.value, op.cap),
        )
    elif isinstance(op, SetRunWindow):
        # Only the limit changes; elapsed time counts from the Run's creation.
        old = cur.execute(
            "SELECT max_elapsed_ms FROM run_windows WHERE run_id = ?", (str(op.run_id),)
        ).fetchone()
        if old is not None and op.max_elapsed_ms < int(old[0]):
            _revoke_in_scope(cur, BudgetScopeType.RUN, str(op.run_id), now_ms)
        cur.execute(
            "INSERT INTO run_windows (run_id, max_elapsed_ms) VALUES (?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET max_elapsed_ms = excluded.max_elapsed_ms",
            (str(op.run_id), op.max_elapsed_ms),
        )
    else:
        table = op.table
        current = _price_table(cur, table.price_table_id, table.version)
        if current is not None:
            if current != table:
                # A price table version never changes once installed.
                raise ReservationMismatchError("PRICE_TABLE_IMMUTABLE")
            return
        cur.execute(
            "INSERT INTO price_tables (price_table_id, version, body) VALUES (?, ?, ?)",
            (table.price_table_id, table.version, dump_json(table).decode()),
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
        _mark_active(cur, op, now_ms, ctx)
    elif isinstance(op, ReleaseReservation):
        _release(cur, op, now_ms, ctx)
    elif isinstance(op, ReportUsage):
        _report_usage(cur, op, now_ms, ctx)
    elif isinstance(op, MarkUnknown):
        _mark_unknown(cur, op, now_ms)
    elif isinstance(op, Reconcile):
        _reconcile(cur, op, now_ms)
    elif isinstance(op, ReconcileOutcome):
        _reconcile_outcome(cur, op, now_ms, ctx)
    elif isinstance(op, Correct | Unexplained):
        _adjust(cur, op, now_ms)
    elif isinstance(op, VoidAllocation):
        _void(cur, op, now_ms)
    elif isinstance(op, RecoverInFlight):
        _recover(cur, now_ms)
    elif isinstance(op, SetBudgetCap | SetCounterCap | SetRunWindow | InstallPriceTable):
        _owner(cur, op, now_ms)
    elif isinstance(op, SetStepCapDefault):
        old = cur.execute(
            "SELECT cap_micro FROM step_cap_defaults WHERE run_id = ?", (str(op.run_id),)
        ).fetchone()
        if old is not None and op.cap.micro_units < int(old[0]):
            _revoke_in_scope(cur, BudgetScopeType.RUN, str(op.run_id), now_ms)
        cur.execute(
            "INSERT INTO step_cap_defaults (run_id, cap_micro) VALUES (?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET cap_micro = excluded.cap_micro",
            (str(op.run_id), op.cap.micro_units),
        )
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
        spent_micro=checked_add(row["spent_micro"], row["adjust_micro"]),
        actual_micro=row["spent_micro"],
        adjust_micro=row["adjust_micro"],
        spent_lower_micro=row["spent_lower_micro"],
        spent_known=bool(row["spent_known"]),
        action_hash=row["action_hash"],
        permit_id=row["permit_id"],
        receipt_id=row["receipt_id"],
    )


def scope_usage(cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str) -> ScopeUsage:
    hold, spent = _totals(cur, kind, scope_id)
    blocked = cur.execute(
        "SELECT 1 FROM budget_blocks WHERE scope_type = ? AND scope_id = ? LIMIT 1",
        (kind.value, scope_id),
    ).fetchone()
    return ScopeUsage(
        scope_type=kind,
        scope_id=scope_id,
        cap_micro=_effective_cap(cur, kind, scope_id),
        hold_micro=hold,
        spent_micro=spent,
        blocked=blocked is not None,
    )


def read_counter(
    cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str, counter: CounterKind
) -> CounterView | None:
    task_id: str | None = scope_id
    if kind is BudgetScopeType.RUN:
        found = cur.execute("SELECT task_id FROM rt_runs WHERE run_id = ?", (scope_id,)).fetchone()
        task_id = None if found is None else found[0]
    task = None if task_id is None else _task_caps(cur, task_id)
    cap = _counter_cap(cur, kind, scope_id, counter, None if task is None else task[0])
    if cap is None:
        return None
    return CounterView(used=_used(cur, kind, scope_id, counter), cap=cap)


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
        settles_unknown = (
            transition.before is not None
            and transition.before.state is AttemptState.OUTCOME_UNKNOWN
        )
        if settles_unknown:
            # WP12-R02: an unknown outcome is settled only with the ledger's own check.
            settling = [
                op
                for op in ops
                if isinstance(op, ReconcileOutcome) and op.attempt_id == transition.after.attempt_id
            ]
            evidence = transition.evidence
            if (
                len(settling) != 1
                or not isinstance(evidence, ReconciliationEvidence)
                or evidence.evidence_ref != settling[0].evidence_ref
                or evidence.outcome.value != settling[0].outcome
                or settling[0].reservation_id != transition.after.reservation_id
            ):
                raise LedgerPairingError("MISSING_RECONCILE_OUTCOME")
        wanted = _REQUIRED_OPS.get(transition.after.state)
        if wanted is None:
            continue
        found = [
            op for op in ops if op.op == wanted and _attempt_of(op) == transition.after.attempt_id
        ]
        if len(found) != 1:
            raise LedgerPairingError(f"MISSING_{wanted.upper()}")
    for op in ops:
        if isinstance(op, ReconcileOutcome) and not any(
            t.after.attempt_id == op.attempt_id
            and t.before is not None
            and t.before.state is AttemptState.OUTCOME_UNKNOWN
            for t in transitions
        ):
            raise LedgerPairingError("UNPAIRED_RECONCILE_OUTCOME")
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
        # Allocation scopes are derived inside the transaction (WP09-R06).
        if isinstance(op, ActivateReservation) and op.action_hash != after.action_hash:
            raise LedgerPairingError("ACTION_HASH_DIFFERS")
        if isinstance(op, ReleaseReservation) and op.report is not None:
            proof = evidence.not_dispatched_ref if isinstance(evidence, AbortEvidence) else None
            if proof != reports.report_ref(op.report):
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
    elif (isinstance(op, ClaimReceipt) and op.receipt.receipt_id != after.receipt_id) or (
        isinstance(op, MarkActive) and op.receipt_id != after.receipt_id
    ):
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
    """CREATE TABLE not_dispatched (
    receipt_id TEXT PRIMARY KEY,
    report_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT""",
    """CREATE TABLE ledger_clock (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    high_water_ms INTEGER NOT NULL CHECK (high_water_ms >= 0)
) STRICT""",
    """CREATE TABLE owner_sequence (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    last_seq INTEGER NOT NULL CHECK (last_seq >= 0)
) STRICT""",
)
# Rows every new database starts with: revocation generation 0, global RUNNING.
LEDGER_INIT: Final = (
    "INSERT INTO revocation (singleton, revocation_gen) VALUES (1, 0)",
    "INSERT INTO ledger_clock (singleton, high_water_ms) VALUES (1, 0)",
    "INSERT INTO owner_sequence (singleton, last_seq) VALUES (1, 0)",
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
    """Revocation (E 3.3): one new generation; the listed tenants' access epochs too.
    Each generation is audited, so a restart can check it never went back (R07)."""
    generation = checked_add(_revocation_gen(cur), 1)
    cur.execute("UPDATE revocation SET revocation_gen = ? WHERE singleton = 1", (generation,))
    clock = cur.execute("SELECT high_water_ms FROM ledger_clock WHERE singleton = 1").fetchone()
    _audit(
        cur,
        0 if clock is None else int(clock[0]),
        "REVOCATION_GEN",
        {"gen": str(generation)},
        "REVOKE",
        None,
        None,
        None,
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
    policy = _policy(cur, str(grant.tenant_id))
    if policy is None:
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "NO_POLICY")
    if (
        grant.synthetic_task_ref is None
        or grant.synthetic_task_ref not in policy.trusted_synthetic_tasks
    ):
        # P-6 (F07-3): a Run is granted only for a trusted synthetic task definition.
        raise PermitRejectedError(PermitReason.POLICY_DENIED, "SYNTHETIC_TASK_UNTRUSTED")
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
    # WP09-R06: the descriptor belongs to the journaled chain of its attempt, and it
    # is exactly the descriptor its content builds (targets, hashes, cost basis).
    chain = _chain(cur, op.attempt_id)
    if (
        str(descriptor.step_id),
        str(descriptor.run_id),
        str(descriptor.task_id),
        str(descriptor.tenant_id),
    ) != (chain["step_id"], chain["run_id"], chain["task_id"], chain["tenant_id"]):
        raise PermitRejectedError(PermitReason.ATTEMPT_MISMATCH, "ATTEMPT_CHAIN_MISMATCH")
    try:
        rebuilt = rebuild(descriptor, op.content, op.registration)
    except ActionDescriptorError:
        raise PermitRejectedError(
            PermitReason.ACTION_MISMATCH, "DESCRIPTOR_NOT_FROM_CONTENT"
        ) from None
    if rebuilt != descriptor:
        raise PermitRejectedError(PermitReason.ACTION_MISMATCH, "DESCRIPTOR_NOT_FROM_CONTENT")
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
    cur.execute(
        "UPDATE reservations SET action_kind = ? WHERE reservation_id = ?",
        (descriptor.kind.value, str(descriptor.reservation_id)),
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
    # required <= reserved <= cost cap, with the required amount recomputed from the
    # recorded descriptor (WP09-R01), at permit issue and at consumption.
    found = cur.execute(
        "SELECT descriptor FROM decisions WHERE attempt_id = ?", (str(attempt_id),)
    ).fetchone()
    if found is None:
        return [PermitReason.DECISION_MISSING]
    try:
        required = required_of(cur, ActionDescriptor.model_validate_json(found[0]))
    except CostBasisMissingError:
        return [PermitReason.RESERVATION_INVALID]
    if row["reserved_micro"] < required or row["hold_micro"] < required:
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
    reasons += _live_reasons(
        cur, decision["tenant_id"], decision["run_id"], now_ms, descriptor.expires_at_ms
    )
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
    """Checks that hold at issue, consumption, claim and start (WP09-R05): expiry,
    stop state, a cancelled or ended Run, the Run grant's expiry and elapsed time."""
    reasons: list[str] = []
    if now_ms >= expires_at_ms:
        reasons.append(PermitReason.EXPIRED)
    stops = _stops(cur, tenant_id, run_id)
    found = {(e.scope_type, e.scope_id): e for e in stops}
    if (StopScopeType.GLOBAL, "global") not in found or any(
        e.status is not StopStatus.RUNNING for e in stops
    ):
        reasons.append(PermitReason.STOPPED)
    run = cur.execute(
        "SELECT task_id, status, cancel_requested FROM rt_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or run[2] or run[1] in _CLOSED_RUN_STATUSES:
        reasons.append(PermitReason.RUN_CANCELLED)
    grant = _grant(cur, run_id)
    if grant is None or now_ms >= grant.expires_at_ms:
        reasons.append(PermitReason.GRANT_EXPIRED)
    if run is not None:
        try:
            reasons += _elapsed_reasons(cur, run_id, run[0], now_ms)
        except BudgetCapMissingError:
            reasons.append(PermitReason.ELAPSED_MS)
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
        owner_epoch=ctx.owner_epoch,
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
    if ctx is None or trusted.owner_epoch != ctx.owner_epoch:
        # A receipt never outlives a restart, even with its old gate ID (WP09-R03).
        reasons.append(PermitReason.STALE_OWNER)
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
    """``ISSUED -> CLAIMED`` once (E 3.2 step 1) after all gate checks. The actual
    operation (arguments, targets, executable) must build the recorded descriptor."""
    stored = _stored_receipt(cur, op.receipt.receipt_id)
    if stored is None:
        raise PermitRejectedError(PermitReason.UNKNOWN_RECEIPT)
    decision = _decision(cur, stored[0].attempt_id)
    recorded = ActionDescriptor.model_validate_json(decision["descriptor"])
    try:
        actual = rebuild(recorded, op.content, op.registration)
    except ActionDescriptorError:
        actual = None
    if actual != recorded:
        raise PermitRejectedError(PermitReason.ACTION_MISMATCH, "DESCRIPTOR_NOT_FROM_CONTENT")
    reasons = _receipt_reasons(
        cur,
        op.receipt,
        op.pep_instance_id,
        decision["action_hash"],
        now_ms,
        ctx,
        wanted=ReceiptState.ISSUED,
    )
    if str(op.receipt.reservation_id) != decision["reservation_id"]:
        reasons.append(PermitReason.RECEIPT_DIFFERS)
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


def read_descriptor(cur: sqlite3.Cursor, attempt_id: str) -> ActionDescriptor | None:
    found = cur.execute(
        "SELECT descriptor FROM decisions WHERE attempt_id = ?", (str(attempt_id),)
    ).fetchone()
    return None if found is None else ActionDescriptor.model_validate_json(found[0])


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
    if op.scope_type is StopScopeType.RUN:
        run = cur.execute(
            "SELECT status, cancel_requested FROM rt_runs WHERE run_id = ?", (op.scope_id,)
        ).fetchone()
        if run is not None and (run[1] or run[0] in _CLOSED_RUN_STATUSES):
            # Cancelled or ended: no resume can make the Run run again (WP09-R05).
            raise PermitRejectedError(PermitReason.RUN_CANCELLED)
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


def read_policy(cur: sqlite3.Cursor, tenant_id: str) -> Policy | None:
    return _policy(cur, tenant_id)


def read_grant(cur: sqlite3.Cursor, run_id: str) -> RunGrant | None:
    return _grant(cur, run_id)


def read_access_epoch(cur: sqlite3.Cursor, tenant_id: str) -> int:
    return _access_epoch(cur, tenant_id)


def read_approval_uses(cur: sqlite3.Cursor, approval_id: str) -> int | None:
    found = _approval_row(cur, approval_id)
    return None if found is None else found[1]


ALL_DDL: Final = LEDGER_DDL + AUTHORITY_DDL


# ================================================================ runtime mirror (WP09-R02, R06)


def mirror(cur: sqlite3.Cursor, draft: EventDraft, now_ms: int) -> None:
    """Copy the journaled runtime chain and limits into the ledger, in the same
    transaction as the event: Task budget and caps, Run, Step and Attempt parents
    and states. The ledger reads only these copies, never caller claims."""
    payload = draft.payload
    if not isinstance(payload, RuntimePayload):
        return
    tenant = str(draft.tenant_id)
    created = now_ms
    for transition in payload.transitions:
        if isinstance(transition, TaskTransition) and transition.before is not None:
            if transition.after.status.value == "COMPLETED":
                _unsettled(cur, "task_id", str(transition.after.task_id))
        elif isinstance(transition, TaskTransition):
            task = transition.after
            cur.execute(
                "INSERT INTO rt_tasks (task_id, tenant_id, budget_micro, caps, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(task.task_id),
                    tenant,
                    task.budget_total.micro_units,
                    dump_json(task.caps_total).decode(),
                    created,
                ),
            )
            _record_time(cur, str(task.task_id), created)
        elif isinstance(transition, RunTransition):
            run = transition.after
            if transition.before is None:
                cur.execute(
                    "INSERT INTO rt_runs (run_id, task_id, tenant_id, status, cancel_requested, "
                    "created_at_ms) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(run.run_id),
                        str(run.task_id),
                        tenant,
                        run.status.value,
                        int(run.cancel_requested),
                        created,
                    ),
                )
                _record_time(cur, str(run.run_id), created)
                cur.execute(
                    "INSERT INTO run_status_times (run_id, status, since_ms) VALUES (?, ?, ?)",
                    (str(run.run_id), run.status.value, now_ms),
                )
            else:
                if run.status.value == "COMPLETED":
                    _unsettled(cur, "run_id", str(run.run_id))
                    _completion_basis(cur, str(run.run_id), tenant, transition.evidence, now_ms)
                cur.execute(
                    "UPDATE rt_runs SET status = ?, cancel_requested = ? WHERE run_id = ?",
                    (run.status.value, int(run.cancel_requested), str(run.run_id)),
                )
                cur.execute(
                    "UPDATE run_status_times SET status = ?, since_ms = ? "
                    "WHERE run_id = ? AND status != ?",
                    (run.status.value, now_ms, str(run.run_id), run.status.value),
                )
            if run.cancel_requested or run.status.value in _CLOSED_RUN_STATUSES:
                _stop_run(cur, str(run.run_id), now_ms)
        elif isinstance(transition, StepTransition) and transition.before is None:
            step = transition.after
            cur.execute(
                "INSERT INTO rt_steps (step_id, run_id, kind) VALUES (?, ?, ?)",
                (str(step.step_id), str(step.run_id), step.kind.value),
            )
        elif isinstance(transition, AttemptTransition):
            attempt = transition.after
            cur.execute(
                "INSERT INTO rt_attempts (attempt_id, step_id, state) VALUES (?, ?, ?) "
                "ON CONFLICT (attempt_id) DO UPDATE SET state = excluded.state",
                (str(attempt.attempt_id), str(attempt.step_id), attempt.state.value),
            )


# ================================================================ time and owner order (WP09-R05)


def effective_now(cur: sqlite3.Cursor, wall_ms: int) -> int:
    """The ledger's time: never earlier than any time it already used. A wall clock
    that moves back cannot extend an expiry or reset elapsed time."""
    row = cur.execute("SELECT high_water_ms FROM ledger_clock WHERE singleton = 1").fetchone()
    if row is None:
        raise ReservationStateError("CLOCK_STATE_MISSING")
    now = max(int(row[0]), wall_ms)
    if now != int(row[0]):
        cur.execute("UPDATE ledger_clock SET high_water_ms = ? WHERE singleton = 1", (now,))
    return now


def check_owner_seq(cur: sqlite3.Cursor, owner_seq: int) -> None:
    """Owner batches apply in increasing order, each once: a replayed signed batch
    cannot restore an old policy, cap or grant (WP09-R05)."""
    row = cur.execute("SELECT last_seq FROM owner_sequence WHERE singleton = 1").fetchone()
    if row is None or owner_seq <= int(row[0]):
        raise OwnerAuthorizationError("REPLAYED")
    cur.execute("UPDATE owner_sequence SET last_seq = ? WHERE singleton = 1", (owner_seq,))


def _revoke_in_scope(
    cur: sqlite3.Cursor, kind: BudgetScopeType, scope_id: str, now_ms: int
) -> None:
    """A lowered limit revokes the scope's outstanding permits and receipts (WP09-R05)."""
    column = _SCOPE_COLUMNS[kind]
    inner = f"SELECT attempt_id FROM reservations WHERE {column} = ?"  # noqa: S608 (constant)
    cur.execute(
        f"UPDATE permits SET state = 'REVOKED' WHERE state = 'ISSUED' AND attempt_id IN ({inner})",  # noqa: S608
        (scope_id,),
    )
    cur.execute(
        "UPDATE receipts SET state = 'REVOKED' WHERE state IN ('ISSUED', 'CLAIMED') "  # noqa: S608
        f"AND attempt_id IN ({inner})",
        (scope_id,),
    )
    _bump(cur, tenants=())
    _audit(
        cur,
        now_ms,
        "LIMIT_LOWERED",
        {"scope": f"{kind.value}:{scope_id}"},
        "REVOKE",
        None,
        None,
        None,
    )


def _unsettled(cur: sqlite3.Cursor, column: str, scope_id: str) -> None:
    """No normal completion while a cost is not settled (WP09-R04)."""
    placeholders = ", ".join("?" * len(_UNSETTLED_STATES))
    found = cur.execute(
        f"SELECT 1 FROM reservations WHERE {column} = ? AND "  # noqa: S608 (constant column)
        f"(state IN ({placeholders}) OR hold_micro > 0) LIMIT 1",
        (scope_id, *_UNSETTLED_STATES),
    ).fetchone()
    if found is not None:
        raise ReservationStateError("UNSETTLED_COST")


def _completion_basis(
    cur: sqlite3.Cursor, run_id: str, tenant: str, evidence: object, now_ms: int
) -> None:
    """WP15-R06, R07: a COMPLETED Run, in the same transaction as its event, only if
    every criterion of its evidence holds on a verification read of this Run:

    - the check's criterion digest is the digest of its own specification (path
      and expected digest), and the observed digest equals the expected one;
    - the read is a completed Attempt of a VERIFICATION Step of this Run, a
      read-only file read of exactly that path, under the current policy and
      access epoch, its cost final, and the live gate recorded that read's bytes
      with that digest (a free digest is not evidence); each read proves one
      criterion only;
    - the latest committed write of this Run to that path wrote that content, by
      the journal's order (QR-04): it ended before the read started, no write of
      that path started after the read or overlaps it, none is unknown or still
      running; an order the journal does not prove is refused;

    and under the current policy, Run grant (not expired) and access epoch, with
    no stop of the Run, the tenant or everything."""
    reasons: list[str] = []
    checks = getattr(evidence, "checks", ())
    criteria = {c.criterion_ref for c in getattr(evidence, "criteria", ())}
    if not checks:
        reasons.append("NO_VERIFIED_READS")
    elif {c.criterion_ref for c in checks} != criteria:
        reasons.append("CHECKS_DO_NOT_COVER_THE_CRITERIA")
    stopped = cur.execute(
        "SELECT 1 FROM stop_state WHERE status = ? AND ((scope_type = 'RUN' AND scope_id = ?) "
        "OR (scope_type = 'TENANT' AND scope_id = ?) OR scope_type = 'GLOBAL') LIMIT 1",
        (StopStatus.STOPPED.value, run_id, tenant),
    ).fetchone()
    if stopped is not None:
        reasons.append("STOPPED")
    grant, policy = _grant(cur, run_id), _policy(cur, tenant)
    epoch = _access_epoch(cur, tenant)
    if grant is None or policy is None:
        reasons.append("GRANT_MISSING")
    else:
        if now_ms >= grant.expires_at_ms:
            reasons.append("GRANT_EXPIRED")
        if grant.policy_version != policy.policy_version:
            reasons.append("POLICY_CHANGED")
        if grant.access_epoch_at_grant != epoch:
            reasons.append("ACCESS_REVOKED")
    writes = _run_writes(cur, run_id)
    spans = _run_spans(cur, run_id)
    used: set[str] = set()
    for check in checks:
        reasons.extend(_check_reasons(cur, check, run_id, policy, epoch, (writes, spans), used))
    if reasons:
        raise CompletionRefusedError(*dict.fromkeys(reasons))


@dataclass(frozen=True)
class _Span:
    """QR-04: an Attempt's positions in its Run's journal (``run_seq``): ``started``
    (DISPATCH_STARTED, committed before the operation runs), ``ended`` (COMPLETED
    or FAILED_DEFINITE, committed after it returned), and whether its outcome was
    ever unknown (then its effect's time is not bounded by the journal)."""

    state: str
    started: int | None
    ended: int | None
    unknown: bool


_NEVER: Final = _Span(state="", started=None, ended=None, unknown=False)


def _run_spans(cur: sqlite3.Cursor, run_id: str) -> dict[str, _Span]:
    marks: dict[str, dict[str, int]] = {}
    states: dict[str, str] = {}
    rows = cur.execute(
        "SELECT run_seq, body FROM events WHERE run_id = ? ORDER BY run_seq", (run_id,)
    ).fetchall()
    for seq, body in rows:
        event = Event.model_validate_json(body)
        if not isinstance(event.payload, RuntimePayload):
            continue
        for t in event.payload.transitions:
            if isinstance(t, AttemptTransition):
                key = str(t.after.attempt_id)
                marks.setdefault(key, {}).setdefault(t.after.state.value, int(seq))
                states[key] = t.after.state.value
    return {
        key: _Span(
            state=states[key],
            started=m.get(AttemptState.DISPATCH_STARTED.value),
            ended=m.get(AttemptState.COMPLETED.value, m.get(AttemptState.FAILED_DEFINITE.value)),
            unknown=AttemptState.OUTCOME_UNKNOWN.value in m,
        )
        for key, m in marks.items()
    }


def _run_writes(cur: sqlite3.Cursor, run_id: str) -> dict[str, list[tuple[str, str]]]:
    """Every write decided in this Run, by path: (attempt, content digest). The
    decisions' order is not used (QR-04)."""
    found: dict[str, list[tuple[str, str]]] = {}
    rows = cur.execute(
        "SELECT d.attempt_id, d.descriptor FROM decisions d "
        "JOIN rt_attempts a ON a.attempt_id = d.attempt_id "
        "JOIN rt_steps s ON s.step_id = a.step_id WHERE s.run_id = ?",
        (run_id,),
    ).fetchall()
    for attempt_id, body in rows:
        change = ActionDescriptor.model_validate_json(body).apply_change
        if change is not None:
            found.setdefault(change.path, []).append((attempt_id, change.content_digest))
    return found


def _write_order_reasons(
    writes: list[tuple[str, _Span]], read: _Span | None
) -> tuple[list[str], str | None]:
    """QR-04: the writes of one path against the verification read of it, by the
    journal's positions only. Returns the reasons the order is not proven (or the
    read is stale) and the content digest of the provably last write before the
    read (None: no write before it). A write is before the read when it ended
    before the read started, after it when it started after the read ended;
    anything else, an unknown outcome or a write still running is not ordered."""
    if read is None or read.started is None or read.ended is None or read.unknown:
        return ["READ_ORDER_UNPROVEN"], None
    reasons: list[str] = []
    before: list[tuple[str, int, int]] = []
    for digest, span in writes:
        if span.started is None:
            continue  # never started; the completion stops the Run's permits
        if span.unknown:
            reasons.append("WRITE_ORDER_UNPROVEN")
        elif span.ended is None:
            reasons.append("WRITE_IN_FLIGHT")
        elif span.state != AttemptState.COMPLETED.value:
            continue  # a definite failure wrote nothing
        elif span.ended < read.started:
            before.append((digest, span.started, span.ended))
        elif span.started > read.ended:
            reasons.append("WRITE_AFTER_VERIFICATION")
        else:
            reasons.append("WRITE_ORDER_UNPROVEN")
    latest = None
    if before:
        last = max(range(len(before)), key=lambda i: before[i][2])
        if any(i != last and w[2] >= before[last][1] for i, w in enumerate(before)):
            reasons.append("WRITE_ORDER_UNPROVEN")  # two writes overlap: no last one
        latest = before[last][0]
    return list(dict.fromkeys(reasons)), latest


_AFTER_ALL: Final = _Span(state="COMPLETED", started=MAX_INT64, ended=MAX_INT64, unknown=False)


def read_committed_writes(cur: sqlite3.Cursor, run_id: str) -> dict[str, str | None]:
    """The provably last committed write of each path of this Run, by the journal's
    order (QR-04); None where the order is not proven."""
    spans = _run_spans(cur, run_id)
    result: dict[str, str | None] = {}
    for path, writes in _run_writes(cur, run_id).items():
        reasons, latest = _write_order_reasons(
            [(d, spans.get(a, _NEVER)) for a, d in writes], _AFTER_ALL
        )
        if reasons:
            result[path] = None
        elif latest is not None:
            result[path] = latest
    return result


def _check_reasons(
    cur: sqlite3.Cursor,
    check: Any,
    run_id: str,
    policy: Policy | None,
    epoch: int,
    order: tuple[dict[str, list[tuple[str, str]]], dict[str, _Span]],
    used: set[str],
) -> list[str]:
    reasons: list[str] = []
    if check.specification_ref() != check.criterion_ref:
        reasons.append("CRITERION_NOT_ITS_SPECIFICATION")
    if check.read_attempt_id is None or check.observed_digest != check.expected_digest:
        return [*reasons, "CRITERION_NOT_MET"]
    attempt_id = str(check.read_attempt_id)
    if attempt_id in used:
        reasons.append("READ_USED_TWICE")
    used.add(attempt_id)
    row = cur.execute(
        "SELECT a.state, s.run_id, s.kind FROM rt_attempts a "
        "JOIN rt_steps s ON s.step_id = a.step_id WHERE a.attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    found = cur.execute(
        "SELECT descriptor FROM decisions WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if row is None or row[0] != "COMPLETED" or row[1] != run_id or found is None:
        return [*reasons, "READ_NOT_OF_THIS_RUN"]
    if row[2] != "VERIFICATION":
        reasons.append("NOT_A_VERIFICATION_READ")
    descriptor = ActionDescriptor.model_validate_json(found[0])
    targets = [(r.path, r.access) for r in descriptor.target_resources]
    if (
        descriptor.kind is not ActionKind.TOOL_CALL
        or descriptor.effect_class is not ToolEffectClass.READ_ONLY
        or descriptor.apply_change is not None
        or targets != [(check.path, ResourceAccess.READ)]
    ):
        reasons.append("NOT_A_READ_OF_THAT_FILE")
    if policy is not None and (
        descriptor.policy_version != policy.policy_version or descriptor.access_epoch != epoch
    ):
        reasons.append("READ_UNDER_OLD_AUTHORITY")
    reservation = cur.execute(
        "SELECT state, spent_known FROM reservations WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if reservation is None or reservation[0] != R.USAGE_REPORTED.value or reservation[1] != 1:
        reasons.append("READ_COST_NOT_FINAL")
    result = cur.execute(
        "SELECT result_digest FROM operation_results WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if result is None or result[0] != check.observed_digest:
        reasons.append("READ_RESULT_NOT_RECORDED")
    # QR-04: the writes of that path against this read, by the journal's order.
    writes, spans = order
    path_writes = [(d, spans.get(a, _NEVER)) for a, d in writes.get(check.path, [])]
    unproven, latest = _write_order_reasons(path_writes, spans.get(attempt_id))
    reasons.extend(unproven)
    if latest is not None and latest != check.observed_digest:
        reasons.append("WRITE_DIFFERS_FROM_VERIFIED")
    return reasons


def _stop_run(cur: sqlite3.Cursor, run_id: str, now_ms: int) -> None:
    """K1 for a cancelled or ended Run, in the same transaction as its transition."""
    row = cur.execute(
        "SELECT stop_seq, status FROM stop_state WHERE scope_type = 'RUN' AND scope_id = ?",
        (run_id,),
    ).fetchone()
    if row is not None and row[1] == StopStatus.STOPPED.value:
        return
    _stop_scope(
        cur, StopScope(op="stop_scope", scope_type=StopScopeType.RUN, scope_id=run_id), now_ms
    )


# ================================================================ open-time consistency (WP09-R07)

# Reservation states an attempt state allows (E 4.1, E 38). After a restart the
# recovery may already have moved in-flight reservations to UNKNOWN.
_SETTLED: Final = frozenset({"UNKNOWN", "RECONCILED", "USAGE_REPORTED"})
_ATTEMPT_RESERVATION: Final = {
    "CONTENT_FIXED": frozenset({"ID_ALLOCATED", "VOID"}),
    "POLICY_ALLOWED": frozenset({"ID_ALLOCATED", "VOID"}),
    "APPROVAL_PENDING": frozenset({"ID_ALLOCATED", "VOID"}),
    "APPROVED": frozenset({"ID_ALLOCATED", "VOID"}),
    "BUDGET_RESERVED": frozenset({"RESERVED"}),
    "INTENT_RECORDED": frozenset({"RESERVED"}),
    "PERMIT_ISSUED": frozenset({"RESERVED"}),
    "PERMIT_CONSUMED": frozenset({"CONSUMED", "UNKNOWN", "RECONCILED"}),
    "DISPATCH_AUTHORIZED": frozenset({"CONSUMED", "UNKNOWN", "RECONCILED"}),
    "DISPATCH_STARTED": frozenset({"ACTIVE"}) | _SETTLED,
    "COMPLETED": frozenset({"ACTIVE"}) | _SETTLED,
    "FAILED_DEFINITE": frozenset({"ACTIVE"}) | _SETTLED,
    "OUTCOME_UNKNOWN": frozenset({"CONSUMED", "ACTIVE"}) | _SETTLED,
    "ABORTED_BEFORE_DISPATCH": frozenset(
        {"ID_ALLOCATED", "VOID", "RESERVED", "RELEASED", "CONSUMED"}
    )
    | _SETTLED,
}


_CONSUMED_OR_LATER: Final = frozenset(
    {"PERMIT_CONSUMED", "DISPATCH_AUTHORIZED", "DISPATCH_STARTED", "COMPLETED", "FAILED_DEFINITE"}
)


def _inconsistent(reason: str) -> LedgerInconsistencyError:
    return LedgerInconsistencyError(reason)


def _journal_view(cur: sqlite3.Cursor) -> dict[str, dict[str, Any]]:
    """The runtime chain as the journal's own events record it (last snapshot wins).

    QR-02: each entity's tenant is its events' tenant (the journal is its source);
    an entity recorded under two tenants is refused. ``completions`` keeps each
    Run's completion evidence (QR-03), as the journal recorded it."""
    view: dict[str, dict[str, Any]] = {
        "tasks": {},
        "runs": {},
        "steps": {},
        "attempts": {},
        "tenants": {},
        "completions": {},
    }
    bodies = cur.execute("SELECT body FROM events ORDER BY tenant_id, tenant_seq").fetchall()
    for (body,) in bodies:
        event = Event.model_validate_json(body)
        if not isinstance(event.payload, RuntimePayload):
            continue
        tenant = str(event.tenant_id)
        for t in event.payload.transitions:
            if isinstance(t, TaskTransition):
                key, table = str(t.after.task_id), "tasks"
            elif isinstance(t, RunTransition):
                key, table = str(t.after.run_id), "runs"
                if t.after.status.value == "COMPLETED":
                    view["completions"][key] = t.evidence
            elif isinstance(t, StepTransition):
                key, table = str(t.after.step_id), "steps"
            else:  # the transitions are of these four kinds only
                key, table = str(t.after.attempt_id), "attempts"
            view[table][key] = t.after
            if view["tenants"].setdefault(key, tenant) != tenant:
                raise _inconsistent("TENANT_MISMATCH")
    return view


def _check_chain(view: dict[str, dict[str, Any]]) -> None:
    """QR-02: every parent the journal names exists, of the same tenant."""
    tenants = view["tenants"]
    links = (
        [(k, str(r.task_id), "tasks") for k, r in view["runs"].items()]
        + [(k, str(s.run_id), "runs") for k, s in view["steps"].items()]
        + [(k, str(a.step_id), "steps") for k, a in view["attempts"].items()]
    )
    for child, parent, table in links:
        if parent not in view[table]:
            raise _inconsistent("PARENT_MISSING")
        if tenants[child] != tenants[parent]:
            raise _inconsistent("TENANT_MISMATCH")


MIRROR_TABLES: Final = ("rt_tasks", "rt_runs", "rt_steps", "rt_attempts")


def repair_mirror(cur: sqlite3.Cursor) -> tuple[str, ...]:
    """WP-12 ProjectionRepair: the runtime mirror (``rt_*``) is a projection of the
    journal's events; each table that differs from what the events record is
    rewritten from them. Every value is derived from the journal (WP12-R05): the
    tenant from the event, the Task's limits and the Step's kind from the
    transitions, and a row's ``created_at_ms`` from the ledger's own record of when
    it first saw the entity (``entity_times``, a source of truth; missing: refused).
    A damaged projection's values are never
    kept. Nothing else is touched: costs, approvals, permits, receipts, stops,
    results and the audit are sources of truth, not projections. Returns the
    names of the tables rewritten (empty when nothing differed).

    QR-02: a journal whose own chain is inconsistent (an entity under two tenants,
    a child of another tenant than its parent, a missing parent) is refused, never
    "repaired": the projection would only hide the source's damage."""
    _check_chain(_journal_view(cur))
    want: dict[str, dict[str, tuple[Any, ...]]] = {
        "rt_tasks": {},
        "rt_runs": {},
        "rt_steps": {},
        "rt_attempts": {},
    }
    bodies = cur.execute("SELECT body FROM events ORDER BY tenant_id, tenant_seq").fetchall()
    for (body,) in bodies:
        event = Event.model_validate_json(body)
        if not isinstance(event.payload, RuntimePayload):
            continue
        tenant = str(event.tenant_id)
        for tr in event.payload.transitions:
            if isinstance(tr, TaskTransition):
                key = str(tr.after.task_id)
                old = want["rt_tasks"].get(key)
                want["rt_tasks"][key] = (
                    tenant,
                    tr.after.budget_total.micro_units,
                    dump_json(tr.after.caps_total).decode(),
                    old[3] if old else _entity_time(cur, key),
                )
            elif isinstance(tr, RunTransition):
                key = str(tr.after.run_id)
                prior = want["rt_runs"].get(key)
                want["rt_runs"][key] = (
                    str(tr.after.task_id),
                    tenant,
                    tr.after.status.value,
                    int(tr.after.cancel_requested),
                    prior[4] if prior else _entity_time(cur, key),
                )
            elif isinstance(tr, StepTransition):
                want["rt_steps"][str(tr.after.step_id)] = (
                    str(tr.after.run_id),
                    tr.after.kind.value,
                )
            elif isinstance(tr, AttemptTransition):
                want["rt_attempts"][str(tr.after.attempt_id)] = (
                    str(tr.after.step_id),
                    tr.after.state.value,
                )
    columns = {
        "rt_tasks": "task_id, tenant_id, budget_micro, caps, created_at_ms",
        "rt_runs": "run_id, task_id, tenant_id, status, cancel_requested, created_at_ms",
        "rt_steps": "step_id, run_id, kind",
        "rt_attempts": "attempt_id, step_id, state",
    }
    repaired: list[str] = []
    for table in MIRROR_TABLES:
        cols = columns[table]
        have = {
            r[0]: tuple(r[1:])
            for r in cur.execute(f"SELECT {cols} FROM {table}")  # noqa: S608 (constant names)
        }
        if have == want[table]:
            continue
        cur.execute(f"DELETE FROM {table}")  # noqa: S608 (constant name)
        marks = ", ".join("?" * (cols.count(",") + 1))
        cur.executemany(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})",  # noqa: S608 (constant names)
            [(k, *v) for k, v in sorted(want[table].items())],
        )
        repaired.append(table)
    return tuple(repaired)


def _check_mirror(cur: sqlite3.Cursor, view: dict[str, dict[str, Any]]) -> None:
    for table, key in (("rt_tasks", "task_id"), ("rt_runs", "run_id")):
        mismatch = cur.execute(
            f"SELECT 1 FROM {table} r LEFT JOIN entity_times e ON e.entity_id = r.{key} "  # noqa: S608
            "WHERE e.created_at_ms IS NULL OR e.created_at_ms != r.created_at_ms LIMIT 1"
        ).fetchone()
        if mismatch is not None:
            raise LedgerInconsistencyError("the mirror's creation time differs from the ledger's")
    attempts = {
        r[0]: (r[1], r[2])
        for r in cur.execute("SELECT attempt_id, step_id, state FROM rt_attempts")
    }
    expected = {k: (str(a.step_id), a.state.value) for k, a in view["attempts"].items()}
    tenants = view["tenants"]
    runs = {
        r[0]: (r[1], r[2], bool(r[3]), r[4])
        for r in cur.execute(
            "SELECT run_id, task_id, status, cancel_requested, tenant_id FROM rt_runs"
        )
    }
    expected_runs = {
        k: (str(r.task_id), r.status.value, r.cancel_requested, tenants[k])
        for k, r in view["runs"].items()
    }
    steps = {r[0]: (r[1], r[2]) for r in cur.execute("SELECT step_id, run_id, kind FROM rt_steps")}
    expected_steps = {k: (str(s.run_id), s.kind.value) for k, s in view["steps"].items()}
    # Task limits are compared too: the budget in micro units and every cap, the
    # caps read back as their type (a malformed copy is a mismatch as well).
    tasks: dict[str, tuple[int, NonMonetaryCaps | None, str]] = {}
    for task_id, budget, caps, tenant in cur.execute(
        "SELECT task_id, budget_micro, caps, tenant_id FROM rt_tasks"
    ):
        try:
            parsed: NonMonetaryCaps | None = NonMonetaryCaps.model_validate_json(caps)
        except ValueError:
            parsed = None
        tasks[task_id] = (int(budget), parsed, tenant)
    expected_tasks = {
        k: (t.budget_total.micro_units, t.caps_total, tenants[k]) for k, t in view["tasks"].items()
    }
    if (attempts, runs, steps, tasks) != (
        expected,
        expected_runs,
        expected_steps,
        expected_tasks,
    ):
        raise _inconsistent("MIRROR_MISMATCH")


# QR-03: which of the ledger's own records are sources of truth, projections or
# derived data (WP-13_INTEGRATION.md):
# - rt_tasks, rt_runs, rt_steps, rt_attempts: projections of the journal's events
#   (re-derivable; ProjectionRepair rewrites them).
# - entity_times, run_status_times: sources of truth, in the ledger's own clock;
#   the events carry another clock's time, so these are never re-derivable. Only
#   their presence, their entity, their status and their bounds are checked.
# - operation_results: a source of truth from a MAC-checked execution report that
#   is not kept; the digest is checked for its form, its Attempt, a recorded start
#   and the completions that name it, never against the bytes (not kept either).
_STARTED_STATES: Final = frozenset(
    {
        AttemptState.DISPATCH_STARTED.value,
        AttemptState.COMPLETED.value,
        AttemptState.FAILED_DEFINITE.value,
        AttemptState.OUTCOME_UNKNOWN.value,
    }
)
_DIGEST_FORM: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


def _check_ledger_records(cur: sqlite3.Cursor, view: dict[str, dict[str, Any]]) -> None:
    clock = cur.execute("SELECT high_water_ms FROM ledger_clock WHERE singleton = 1").fetchone()
    if clock is None:
        raise _inconsistent("CLOCK_STATE_MISSING")
    high = int(clock[0])
    times = {
        r[0]: int(r[1]) for r in cur.execute("SELECT entity_id, created_at_ms FROM entity_times")
    }
    if set(times) != set(view["tasks"]) | set(view["runs"]):
        raise _inconsistent("ENTITY_TIMES_DIFFER")  # missing, or of no such entity
    if any(t > high for t in times.values()):
        raise _inconsistent("ENTITY_TIME_AFTER_CLOCK")
    for run_id, run in view["runs"].items():
        if times[run_id] < times[str(run.task_id)]:
            raise _inconsistent("RUN_BEFORE_TASK")
    since = {
        r[0]: (r[1], int(r[2]))
        for r in cur.execute("SELECT run_id, status, since_ms FROM run_status_times")
    }
    if set(since) != set(view["runs"]):
        raise _inconsistent("RUN_STATUS_TIMES_DIFFER")
    for run_id, run in view["runs"].items():
        status, at = since[run_id]
        if status != run.status.value:
            raise _inconsistent("RUN_STATUS_TIME_OF_ANOTHER_STATUS")
        if not times[run_id] <= at <= high:
            raise _inconsistent("RUN_STATUS_TIME_OUT_OF_BOUNDS")
    receipts = {r[0]: r[1] for r in cur.execute("SELECT receipt_id, state FROM receipts")}
    receipt_of = {
        r[0]: r[1]
        for r in cur.execute("SELECT attempt_id, receipt_id FROM reservations")
        if r[1] is not None
    }
    results = {
        r[0]: r[1] for r in cur.execute("SELECT attempt_id, result_digest FROM operation_results")
    }
    for attempt_id, digest in results.items():
        attempt = view["attempts"].get(attempt_id)
        if attempt is None:
            raise _inconsistent("RESULT_OF_NO_ATTEMPT")
        if not isinstance(digest, str) or _DIGEST_FORM.fullmatch(digest) is None:
            raise _inconsistent("RESULT_MALFORMED")
        started = receipts.get(receipt_of.get(attempt_id, "")) == ReceiptState.USED.value
        if attempt.state.value not in _STARTED_STATES or not started:
            raise _inconsistent("RESULT_WITHOUT_START")
    for evidence in view["completions"].values():
        for check in getattr(evidence, "checks", ()):
            if check.read_attempt_id is None:
                continue
            if results.get(str(check.read_attempt_id)) != check.observed_digest:
                raise _inconsistent("COMPLETION_RESULT_DIFFERS")  # missing or changed


def _check_reservations(cur: sqlite3.Cursor, view: dict[str, dict[str, Any]]) -> None:
    rows = {
        r[0]: r[1:]
        for r in cur.execute(
            "SELECT reservation_id, attempt_id, state, permit_id, receipt_id FROM reservations"
        )
    }
    seen: set[str] = set()
    for attempt in view["attempts"].values():
        if attempt.reservation_id is None:
            continue
        found = rows.get(str(attempt.reservation_id))
        allowed = _ATTEMPT_RESERVATION.get(attempt.state.value)
        if found is None or allowed is None or found[0] != str(attempt.attempt_id):
            raise _inconsistent("RESERVATION_MISMATCH")
        if found[1] not in allowed:
            raise _inconsistent("RESERVATION_STATE_MISMATCH")
        # The ledger binds permit and receipt at consumption; from then on both agree.
        consumed = attempt.state.value in _CONSUMED_OR_LATER
        for held, journaled in ((found[2], attempt.permit_id), (found[3], attempt.receipt_id)):
            if (held is not None or consumed) and held != (
                None if journaled is None else str(journaled)
            ):
                raise _inconsistent("RESERVATION_PERMIT_MISMATCH")
        seen.add(str(attempt.reservation_id))
    if seen != set(rows):
        raise _inconsistent("RESERVATION_WITHOUT_ATTEMPT")


def _check_permits(cur: sqlite3.Cursor) -> None:
    permits = {
        r[0]: (r[1], r[2]) for r in cur.execute("SELECT permit_id, attempt_id, state FROM permits")
    }
    receipts = {r[0]: r[1] for r in cur.execute("SELECT permit_id, attempt_id FROM receipts")}
    for permit_id, (attempt_id, state) in permits.items():
        if (state == PermitState.CONSUMED.value) != (permit_id in receipts):
            raise _inconsistent("PERMIT_RECEIPT_MISMATCH")
        if permit_id in receipts and receipts[permit_id] != attempt_id:
            raise _inconsistent("PERMIT_RECEIPT_MISMATCH")
    if set(receipts) - set(permits):
        raise _inconsistent("PERMIT_RECEIPT_MISMATCH")
    uses = cur.execute(
        "SELECT a.approval_id, a.uses, (SELECT COUNT(*) FROM decisions d JOIN permits p "
        "ON p.attempt_id = d.attempt_id WHERE d.approval_id = a.approval_id "
        "AND p.state = 'CONSUMED') FROM approvals a"
    ).fetchall()
    if any(int(r[1]) != int(r[2]) for r in uses):
        raise _inconsistent("APPROVAL_USES_MISMATCH")


def _check_entries(cur: sqlite3.Cursor) -> None:
    for reservation_id, hold, spent, adjust in cur.execute(
        "SELECT reservation_id, hold_micro, spent_micro, adjust_micro FROM reservations"
    ).fetchall():
        last = cur.execute(
            "SELECT hold_micro, spent_micro FROM ledger_entries WHERE reservation_id = ? "
            "ORDER BY entry_seq DESC LIMIT 1",
            (reservation_id,),
        ).fetchone()
        deltas = cur.execute(
            "SELECT delta_micro FROM ledger_entries WHERE reservation_id = ? "
            "AND kind IN ('CORRECTION', 'UNEXPLAINED')",
            (reservation_id,),
        ).fetchall()
        if last is None or (int(last[0]), int(last[1])) != (int(hold), int(spent)):
            raise _inconsistent("RESERVATION_ENTRIES_MISMATCH")
        if checked_sum(int(d[0]) for d in deltas) != int(adjust):
            raise _inconsistent("RESERVATION_ENTRIES_MISMATCH")


def _audit_rows(cur: sqlite3.Cursor) -> list[tuple[Any, ...]]:
    return cur.execute(
        "SELECT audit_seq, at_ms, action, targets, decision, reason, policy_version, "
        "access_epoch, prev_hash, record_hash FROM audit_log ORDER BY audit_seq"
    ).fetchall()


def _check_audit(cur: sqlite3.Cursor, rows: list[tuple[Any, ...]]) -> None:
    prev: str | None = None
    for index, row in enumerate(rows, start=1):
        seq, at_ms, action, targets, decision, reason, version, epoch, prev_hash, digest = row
        payload = json.dumps(
            [at_ms, action, targets, decision, reason, version, epoch], separators=(",", ":")
        ).encode()
        expected = (
            "sha256:"
            + hashlib.sha256(
                AUDIT_HASH_DOMAIN + (prev or "").encode() + b"\x00" + payload
            ).hexdigest()
        )
        if seq != index or prev_hash != prev or digest != expected:
            raise _inconsistent("AUDIT_CHAIN_BROKEN")
        prev = digest


def _check_stop_and_revocation(cur: sqlite3.Cursor, rows: list[tuple[Any, ...]]) -> None:
    stops = cur.execute("SELECT scope_type, scope_id, stop_seq, status FROM stop_state").fetchall()
    if not any(r[0] == "GLOBAL" and r[1] == "global" for r in stops):
        raise _inconsistent("STOP_STATE_MISMATCH")
    by_scope: dict[str, list[str]] = {}
    generations: list[str] = []
    for row in rows:
        action, targets = row[2], json.loads(row[3])
        if action in ("STOP", "RESUME"):
            by_scope.setdefault(targets.get("scope", ""), []).append(action)
        if action == "REVOCATION_GEN":
            generations.append(targets.get("gen", ""))
    # Every scope the audit history names, and every stored row, is checked: the
    # row must exist, its stop_seq must equal the number of STOPs, and its state
    # must be the one the last STOP or RESUME leaves. A row without any history is
    # only the initial global RUNNING row.
    rows_by_scope = {f"{r[0]}:{r[1]}": (int(r[2]), r[3]) for r in stops}
    for scope in set(rows_by_scope) | set(by_scope):
        actions = by_scope.get(scope, [])
        stored = rows_by_scope.get(scope)
        if stored is None:
            raise _inconsistent("STOP_STATE_MISMATCH")
        stop_seq, status = stored
        if not actions:
            if (scope, stop_seq, status) != ("GLOBAL:global", 0, StopStatus.RUNNING.value):
                raise _inconsistent("STOP_STATE_MISMATCH")
            continue
        expected_status = (
            StopStatus.STOPPED.value if actions[-1] == "STOP" else StopStatus.RUNNING.value
        )
        if stop_seq != actions.count("STOP") or status != expected_status:
            raise _inconsistent("STOP_STATE_MISMATCH")
    gen = _revocation_gen(cur)
    if gen != len(generations) or (generations and generations[-1] != str(gen)):
        raise _inconsistent("REVOCATION_MISMATCH")


def verify_consistency(cur: sqlite3.Cursor) -> None:
    """Open-time checks of the journal against the ledger (WP09-R07). Any mismatch
    raises ``LedgerInconsistencyError``: the journal is not opened, nothing runs.

    Not a full Recovery Engine: it detects disagreement, it does not repair it, and
    it cannot detect a consistent rewrite by someone holding the database."""
    view = _journal_view(cur)
    _check_chain(view)
    _check_mirror(cur, view)
    _check_ledger_records(cur, view)
    _check_reservations(cur, view)
    _check_permits(cur)
    _check_entries(cur)
    rows = _audit_rows(cur)
    _check_audit(cur, rows)
    _check_stop_and_revocation(cur, rows)


def revoke_stale(cur: sqlite3.Cursor, new_owner_epoch: int, wall_ms: int) -> None:
    """At open, under the new owner epoch: every unused permit and receipt of an
    earlier owner can never be used (WP09-R07). Consumed and started operations
    stay as they are (held, to be reconciled)."""
    now = effective_now(cur, wall_ms)
    stale_permits = cur.execute("SELECT COUNT(*) FROM permits WHERE state = 'ISSUED'").fetchone()[0]
    stale_receipts = cur.execute(
        "SELECT COUNT(*) FROM receipts WHERE state IN ('ISSUED', 'CLAIMED')"
    ).fetchone()[0]
    if not stale_permits and not stale_receipts:
        return
    cur.execute("UPDATE permits SET state = 'REVOKED' WHERE state = 'ISSUED'")
    cur.execute("UPDATE receipts SET state = 'REVOKED' WHERE state IN ('ISSUED', 'CLAIMED')")
    _bump(cur, tenants=())
    _audit(
        cur,
        now,
        "RESTART_REVOKED",
        {"owner_epoch": str(new_owner_epoch)},
        "REVOKE",
        None,
        None,
        None,
    )


def read_run_status_since(cur: sqlite3.Cursor, run_id: str) -> int | None:
    """QR-03: read only when the record agrees with the Run's current status (a
    record of another status, or a Run without one, is refused, never used)."""
    run = cur.execute("SELECT status FROM rt_runs WHERE run_id = ?", (run_id,)).fetchone()
    row = cur.execute(
        "SELECT status, since_ms FROM run_status_times WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None and row is None:
        return None
    if run is None or row is None or row[0] != run[0]:
        raise _inconsistent("RUN_STATUS_TIME_DISAGREES")
    return int(row[1])
