"""Recovery after a crash (WP-12; A 6-7, A.I9, A.I17, E 2.4, E 16, E.Z17, E.Z32).

Separate responsibilities, in this order:

1. Taking ownership is the journal's open (a new owner epoch; every unused
   permit and receipt of an earlier owner is revoked; WP09-R07). Not here.
2. Rebuild is pure: the runtime state is folded from the journal's verified
   events (``rebuild_with_head``, ``RuntimeStateMachine.load``); it writes
   nothing, calls no model, runs no tool, sends nothing.
3. ``assess`` classifies every unfinished Attempt and Run by the recovery table
   below. It only reads.
4. ``recover`` is the owner's explicit recovery: it applies the table, one
   journal transaction per step (with its ledger operation where the pairing
   requires one), so a crash part way leaves a state a second ``recover``
   continues from. Running it twice changes nothing the second time.
5. ``reconcile`` settles an unknown outcome only with trusted evidence bound to
   the Attempt: the journal's own proof that the operation never started, or the
   live gate's signed execution report (MAC with the permit key) for exactly its
   Attempt, receipt and reservation. A digest, a current file that looks right,
   or a model's opinion is never evidence. Without evidence it stays unknown;
   the owner may then record ``UNRESOLVED_EXTERNAL_EFFECT`` (A), keeping the hold.

The proof that an operation never ran: the gate and the broker run the
operation only after the DISPATCH_STARTED transition (with ``mark_active``) has
been committed. An Attempt whose journal history has no DISPATCH_STARTED never
ran, whatever its receipt's state; a revoked receipt alone proves nothing.

Nothing here retries, re-runs or resumes work. A Run's transcript lives in its
process (WP-15): an interrupted Run is never continued; without unknown work it
ends CANCELLED by the owner's recovery, with unknown work it waits in
RECONCILIATION_REQUIRED. RECOVERING and CANCELLING have deadlines (A.I17): past
them the Run is escalated to RECONCILIATION_REQUIRED (``DEADLINE_EXCEEDED``).
The deadline values are explicit inputs, not approved operating values.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Final, Protocol

from pydantic import Field

from agent_core.ports.clock import Clock
from agent_core.ports.journal import JournalPort
from agent_core.ports.ledger import LedgerPort
from agent_core.runtime.file_execution import recovery_receipts
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.runtime.state_machine import RuntimeState
from agent_core.safety import reports
from agent_core.types.base import FrozenModel
from agent_core.types.cost import ReservationState
from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import AttemptId, RunId
from agent_core.types.ledger import (
    LedgerOp,
    MarkUnknown,
    Reconcile,
    ReleaseReservation,
    VoidAllocation,
)
from agent_core.types.records import Attempt, AttemptState, Run, RunStatus, StepStatus
from agent_core.types.safety import ExecutionReport, Policy, SecretKey
from agent_core.types.transitions import (
    AbortEvidence,
    AbortReason,
    AttemptTransition,
    OutcomeEvidence,
    ReconciledOutcome,
    ReconciliationEvidence,
    ReconciliationTrigger,
    ReconciliationTriggerReason,
    RunTransition,
    Termination,
    TerminationReason,
)
from agent_core.types.values import Budget, Currency, sha256_digest

A = AttemptState
R = RunStatus
RS = ReservationState

_TERMINAL_RUN: Final = frozenset({R.COMPLETED, R.CANCELLED, R.FAILED, R.ABORTED_SAFETY})
_INTERRUPTED: Final = frozenset(
    {R.QUEUED, R.RUNNING, R.WAITING_TOOL, R.WAITING_APPROVAL, R.PAUSED_BUDGET, R.VERIFYING}
)
_FINAL: Final = frozenset({A.COMPLETED, A.FAILED_DEFINITE, A.ABORTED_BEFORE_DISPATCH})
_SETTLED_RESERVATION: Final = frozenset({RS.USAGE_REPORTED, RS.RECONCILED, RS.RELEASED, RS.VOID})


class RecoveryError(AgentCoreError):
    code = "RECOVERY_REFUSED"


class ReconciliationEvidenceError(RecoveryError):
    """The evidence is not trusted evidence of this Attempt (fixed reason)."""

    code = "RECONCILIATION_EVIDENCE_INVALID"


# ================================================================ the recovery table


@dataclass(frozen=True)
class Rule:
    """What a crash at one Attempt state means (WP-12 table; documented in
    ``WP-12_RECOVERY.md``)."""

    code: str
    provably_not_executed: bool
    permits: str  # what happens to its permit and receipt
    release: str  # what happens to its reservation
    keep_unknown: bool
    step: str  # the Step's end state


_ABORT_NO_HOLD = Rule(
    "ABORT_NOTHING_HELD",
    True,
    "no permit exists",
    "none, or the allocated id is voided",
    False,
    "FAILED",
)
_ABORT_HELD = Rule(
    "ABORT_RELEASE",
    True,
    "none, or an issued permit already revoked at the new owner epoch (never consumed: "
    "consumption is recorded with PERMIT_CONSUMED)",
    "released (never consumed)",
    False,
    "FAILED",
)
_NOT_STARTED = Rule(
    "PROVEN_NOT_STARTED",
    True,
    "consumed; its receipt was revoked at the new owner epoch, and the journal has no "
    "DISPATCH_STARTED (the operation runs only after it): not executed",
    "marked unknown, then reconciled at a recorded zero cost with the journal proof",
    False,
    "FAILED",
)
_STARTED = Rule(
    "KEEP_UNKNOWN",
    False,
    "used; the operation may have run",
    "kept (UNKNOWN) until trusted evidence reconciles it",
    True,
    "RECONCILIATION_REQUIRED",
)
_FINAL_RULE = Rule("FINAL", False, "settled", "settled", False, "unchanged")

RECOVERY_TABLE: Final[dict[AttemptState, Rule]] = {
    A.PROPOSED: _ABORT_NO_HOLD,
    A.VALIDATED: _ABORT_NO_HOLD,
    A.CONTENT_FIXED: _ABORT_NO_HOLD,
    A.POLICY_ALLOWED: _ABORT_NO_HOLD,
    A.APPROVAL_PENDING: _ABORT_NO_HOLD,
    A.APPROVED: _ABORT_NO_HOLD,
    A.BUDGET_RESERVED: _ABORT_HELD,
    A.INTENT_RECORDED: _ABORT_HELD,
    A.PERMIT_ISSUED: _ABORT_HELD,
    A.PERMIT_CONSUMED: _NOT_STARTED,
    A.DISPATCH_AUTHORIZED: _NOT_STARTED,
    A.DISPATCH_STARTED: _STARTED,
    A.OUTCOME_UNKNOWN: _STARTED,
    A.COMPLETED: _FINAL_RULE,
    A.FAILED_DEFINITE: _FINAL_RULE,
    A.ABORTED_BEFORE_DISPATCH: _FINAL_RULE,
}


class RecoveryDeadlines(FrozenModel):
    """How long a Run may stay RECOVERING or CANCELLING (A.I17). Explicit inputs;
    not owner-approved operating values."""

    recovering_ms: Annotated[int, Field(strict=True, ge=1)]
    cancelling_ms: Annotated[int, Field(strict=True, ge=1)]


@dataclass(frozen=True)
class RecoveryDeps:
    sm: RuntimeStateMachine
    ledger: LedgerPort
    # The owner's signed operations (reconciliation at a recorded cost).
    owner: Callable[..., None]
    clock: Clock
    # The permit key: verifies the live gate's signed execution reports.
    report_key: SecretKey | None = None
    journal: JournalPort | None = None

    def events(self) -> JournalPort:
        journal = self.journal if self.journal is not None else self.ledger
        return journal  # type: ignore[return-value]


# ================================================================ assessment (read only)


@dataclass(frozen=True)
class AttemptPlan:
    attempt_id: AttemptId
    state: AttemptState
    rule: str
    action: str  # ABORT, MARK_UNKNOWN, PROVE_NOT_STARTED, KEEP_UNKNOWN, NONE


@dataclass(frozen=True)
class RunPlan:
    run_id: RunId
    status: RunStatus
    attempts: tuple[AttemptPlan, ...]


@dataclass(frozen=True)
class RecoveryReport:
    runs: tuple[RunPlan, ...]
    changed: bool = False


def _run_attempts(state: RuntimeState, run_id: RunId) -> list[Attempt]:
    return [
        state.attempts[a]
        for s in state.run_steps.get(run_id, ())
        for a in state.step_attempts.get(s, ())
    ]


def _action(state: AttemptState) -> str:
    rule = RECOVERY_TABLE[state]
    if rule is _FINAL_RULE:
        return "NONE"
    if rule.provably_not_executed and state not in (A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED):
        return "ABORT"
    if state in (A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED):
        return "PROVE_NOT_STARTED"
    return "MARK_UNKNOWN" if state is A.DISPATCH_STARTED else "KEEP_UNKNOWN"


def assess(state: RuntimeState, ledger: LedgerPort | None = None) -> RecoveryReport:
    """The recovery plan of every Run that is not finished. Reads only."""
    runs = []
    for run in state.runs.values():
        if run.status in _TERMINAL_RUN:
            continue
        attempts = tuple(
            AttemptPlan(a.attempt_id, a.state, RECOVERY_TABLE[a.state].code, _action(a.state))
            for a in _run_attempts(state, run.run_id)
            if a.state not in _FINAL
        )
        runs.append(RunPlan(run.run_id, run.status, attempts))
    return RecoveryReport(runs=tuple(runs))


# ================================================================ recovery (explicit)


def _started_in_history(deps: RecoveryDeps, attempt: Attempt) -> bool:
    """Whether the journal ever recorded this Attempt's start (DISPATCH_STARTED)."""
    run_id = deps.sm.state.steps[attempt.step_id].run_id
    after = 0
    journal = deps.events()
    while True:
        events = journal.read_run(run_id, after_run_seq=after, limit=500)
        if not events:
            return False
        for event in events:
            payload = event.payload
            if payload is None:
                continue
            for t in payload.transitions:
                if (
                    isinstance(t, AttemptTransition)
                    and t.after.attempt_id == attempt.attempt_id
                    and t.after.state is A.DISPATCH_STARTED
                ):
                    return True
        last = events[-1].run_seq
        if last is None:
            return False
        after = last


def _now(deps: RecoveryDeps) -> int:
    """Never earlier than the latest recorded event: a wall clock that went back
    cannot extend a deadline."""
    latest = 0
    journal = deps.events()
    after = 0
    tenant = deps.sm.tenant_id
    while True:
        events = journal.read_tenant(tenant, after_tenant_seq=after, limit=500)
        if not events:
            break
        latest = max(latest, max(e.recorded_at_ms for e in events))
        after = events[-1].tenant_seq
    return max(deps.clock.now_ms(), latest)


def _entered(deps: RecoveryDeps, run: Run) -> int:
    """When the Run last entered its current status (the event's recorded time)."""
    entered = 0
    after = 0
    journal = deps.events()
    while True:
        events = journal.read_run(run.run_id, after_run_seq=after, limit=500)
        if not events:
            return entered
        for event in events:
            if event.payload is None:
                continue
            for t in event.payload.transitions:
                if isinstance(t, RunTransition) and t.after.status is run.status:
                    entered = event.recorded_at_ms
        last = events[-1].run_seq
        if last is None:
            return entered
        after = last


def _stopped(deps: RecoveryDeps, run_id: RunId) -> bool:
    scopes = {("GLOBAL", "global"), ("TENANT", str(deps.sm.tenant_id)), ("RUN", str(run_id))}
    return any(
        (e.scope_type.value, e.scope_id) in scopes and e.status.value == "STOPPED"
        for e in deps.ledger.stop_entries()
    )


def _set_run(deps: RecoveryDeps, run_id: RunId, status: RunStatus, evidence: Any = None) -> None:
    deps.sm.set_run_status(deps.sm.state.runs[run_id], status, evidence)


def _trigger(run_id: RunId, reason: ReconciliationTriggerReason) -> ReconciliationTrigger:
    return ReconciliationTrigger(
        kind="reconciliation_trigger",
        reason=reason,
        evidence_ref=sha256_digest(f"recovery:{reason.value}:{run_id}".encode()),
    )


def _reservation(deps: RecoveryDeps, attempt: Attempt) -> Any:
    if attempt.reservation_id is None:
        return None
    return deps.ledger.reservation(str(attempt.reservation_id))


def _abort(deps: RecoveryDeps, attempt: Attempt) -> None:
    view = _reservation(deps, attempt)
    ops: tuple[LedgerOp, ...] = ()
    if view is not None and attempt.reservation_id is not None:
        if view.state is RS.ID_ALLOCATED:
            ops = (VoidAllocation(op="void", reservation_id=attempt.reservation_id),)
        elif view.state is RS.RESERVED:
            ops = (
                ReleaseReservation(
                    op="release",
                    reservation_id=attempt.reservation_id,
                    attempt_id=attempt.attempt_id,
                    report=None,
                ),
            )
    evidence = AbortEvidence(kind="abort", reason=AbortReason.REVOKED, not_dispatched_ref=None)
    deps.sm.advance_attempt(attempt, A.ABORTED_BEFORE_DISPATCH, evidence, ledger=ops)


def _mark_unknown(deps: RecoveryDeps, attempt: Attempt) -> Attempt:
    view = _reservation(deps, attempt)
    ops: tuple[LedgerOp, ...] = ()
    if view is not None and view.state is not RS.UNKNOWN and attempt.reservation_id is not None:
        ops = (
            MarkUnknown(
                op="mark_unknown",
                reservation_id=attempt.reservation_id,
                attempt_id=attempt.attempt_id,
            ),
        )
    assert attempt.receipt_id is not None  # noqa: S101 (consumed or later)
    evidence = OutcomeEvidence(
        kind="outcome",
        receipt_id=attempt.receipt_id,
        report_ref=sha256_digest(f"recovery:unknown:{attempt.receipt_id}".encode()),
    )
    return deps.sm.advance_attempt(attempt, A.OUTCOME_UNKNOWN, evidence, ledger=ops)


def _settle(
    deps: RecoveryDeps, attempt: Attempt, target: AttemptState, actual: int, proof: str
) -> None:
    """The owner's reconciliation at a recorded cost, then the Attempt's outcome.
    Each its own transaction; a crash between them is continued by a second call
    (an already reconciled reservation is not reconciled again)."""
    view = _reservation(deps, attempt)
    if view is not None and view.state is RS.UNKNOWN and attempt.reservation_id is not None:
        deps.owner(
            Reconcile(
                op="reconcile",
                reservation_id=attempt.reservation_id,
                actual=Budget(currency=Currency.XTU, micro_units=actual),
                evidence_ref=proof,
            )
        )
    evidence = ReconciliationEvidence(
        kind="reconciliation",
        evidence_ref=proof,
        outcome=ReconciledOutcome.COMPLETED if target is A.COMPLETED else ReconciledOutcome.FAILED,
    )
    deps.sm.advance_attempt(deps.sm.state.attempts[attempt.attempt_id], target, evidence)


def _prove_not_started(deps: RecoveryDeps, attempt: Attempt) -> bool:
    if _started_in_history(deps, attempt):
        return False
    proof = sha256_digest(
        f"recovery:not-started:{attempt.attempt_id}:{attempt.receipt_id}".encode()
    )
    _settle(deps, attempt, A.FAILED_DEFINITE, 0, proof)
    return True


def _recover_attempts(deps: RecoveryDeps, run_id: RunId) -> bool:
    changed = False
    for attempt in _run_attempts(deps.sm.state, run_id):
        current = deps.sm.state.attempts[attempt.attempt_id]
        action = _action(current.state)
        if action == "ABORT":
            _abort(deps, current)
            changed = True
        elif action in ("PROVE_NOT_STARTED", "MARK_UNKNOWN"):
            current = _mark_unknown(deps, current)
            changed = True
            if action == "PROVE_NOT_STARTED":
                _prove_not_started(deps, current)
        elif action == "KEEP_UNKNOWN":
            changed = _prove_not_started(deps, current) or changed
    return changed


def _close_steps(deps: RecoveryDeps, run_id: RunId, target: StepStatus) -> bool:
    changed = False
    state = deps.sm.state
    for step_id in state.run_steps.get(run_id, ()):
        step = state.steps[step_id]
        attempts = [state.attempts[a] for a in state.step_attempts.get(step_id, ())]
        unknown = any(a.state is A.OUTCOME_UNKNOWN for a in attempts)
        if step.status in (StepStatus.PLANNED, StepStatus.IN_PROGRESS):
            deps.sm.set_step_status(step, StepStatus.RECONCILIATION_REQUIRED if unknown else target)
            changed = True
        elif step.status is StepStatus.RECONCILIATION_REQUIRED and not unknown:
            deps.sm.set_step_status(step, target)
            changed = True
    return changed


def _settled(deps: RecoveryDeps, run_id: RunId) -> bool:
    for attempt in _run_attempts(deps.sm.state, run_id):
        if attempt.state not in _FINAL:
            return False
        view = _reservation(deps, attempt)
        if view is not None and (
            view.state not in _SETTLED_RESERVATION | {RS.ID_ALLOCATED} or view.hold_micro
        ):
            return False
    return True


def _recover_run(deps: RecoveryDeps, run_id: RunId, deadlines: RecoveryDeadlines, now: int) -> bool:
    changed = False
    run = deps.sm.state.runs[run_id]
    if run.status in (R.RECOVERING, R.CANCELLING):
        limit = deadlines.recovering_ms if run.status is R.RECOVERING else deadlines.cancelling_ms
        if now - _entered(deps, run) > limit:
            _set_run(
                deps,
                run_id,
                R.RECONCILIATION_REQUIRED,
                _trigger(run_id, ReconciliationTriggerReason.DEADLINE_EXCEEDED),
            )
            changed = True
    current = deps.sm.state.runs[run_id].status
    if current in _INTERRUPTED:
        # A stopped Run is cancelled (its stop is kept); any other is recovering.
        stoppable = current in (R.RUNNING, R.WAITING_TOOL, R.WAITING_APPROVAL, R.PAUSED_BUDGET)
        target = R.CANCELLING if _stopped(deps, run_id) and stoppable else R.RECOVERING
        _set_run(deps, run_id, target)
        changed = True
    changed = _recover_attempts(deps, run_id) or changed
    unknown = any(a.state is A.OUTCOME_UNKNOWN for a in _run_attempts(deps.sm.state, run_id))
    status = deps.sm.state.runs[run_id].status
    if status is R.RECOVERING:
        reason = (
            ReconciliationTriggerReason.OUTCOME_UNKNOWN
            if unknown
            else ReconciliationTriggerReason.SESSION_LOST
        )
        _set_run(deps, run_id, R.RECONCILIATION_REQUIRED, _trigger(run_id, reason))
        changed = True
    elif status is R.CANCELLING:
        if unknown:
            _set_run(
                deps,
                run_id,
                R.RECONCILIATION_REQUIRED,
                _trigger(run_id, ReconciliationTriggerReason.OUTCOME_UNKNOWN),
            )
        else:
            _close_steps(deps, run_id, StepStatus.CANCELLED)
            if _settled(deps, run_id):
                _set_run(deps, run_id, R.CANCELLED)
        return True
    if deps.sm.state.runs[run_id].status is R.RECONCILIATION_REQUIRED:
        changed = _close_steps(deps, run_id, StepStatus.FAILED) or changed
        if not unknown and _settled(deps, run_id):
            # Nothing is unknown, and the Run cannot continue (its transcript is
            # gone): the owner's recovery ends it, never resumes it.
            termination = Termination(
                kind="termination",
                reason=TerminationReason.RECOVERY_FAILED,
                evidence_ref=sha256_digest(f"recovery:session-lost:{run_id}".encode()),
            )
            _set_run(deps, run_id, R.FAILED, termination)
            changed = True
    return changed


def recover(deps: RecoveryDeps, *, deadlines: RecoveryDeadlines) -> RecoveryReport:
    """The owner's explicit recovery of every unfinished Run (idempotent)."""
    plan = assess(deps.sm.state, deps.ledger)
    now = _now(deps)
    changed = False
    for run_plan in plan.runs:
        changed = _recover_run(deps, run_plan.run_id, deadlines, now) or changed
    return RecoveryReport(runs=plan.runs, changed=changed)


# ================================================================ reconciliation


class Reconciled(StrEnum):
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"
    STILL_UNKNOWN = "STILL_UNKNOWN"
    ALREADY_SETTLED = "ALREADY_SETTLED"


@dataclass(frozen=True)
class ExecutionEvidence:
    """The live gate's signed result report of exactly this Attempt."""

    report: ExecutionReport


@dataclass(frozen=True)
class ReconcileResult:
    outcome: Reconciled


def reconcile(
    deps: RecoveryDeps, attempt_id: AttemptId, evidence: ExecutionEvidence | None
) -> ReconcileResult:
    attempt = deps.sm.state.attempts[attempt_id]
    if attempt.state in _FINAL:
        return ReconcileResult(Reconciled.ALREADY_SETTLED)
    if attempt.state is not A.OUTCOME_UNKNOWN:
        raise RecoveryError("NOT_UNKNOWN")
    if evidence is None:
        if _prove_not_started(deps, attempt):
            return ReconcileResult(Reconciled.NOT_EXECUTED)
        return ReconcileResult(Reconciled.STILL_UNKNOWN)
    report = evidence.report
    if (
        type(evidence) is not ExecutionEvidence
        or type(report) is not ExecutionReport
        or deps.report_key is None
        or not reports.verify(deps.report_key, report)
    ):
        raise ReconciliationEvidenceError("REPORT_NOT_TRUSTED")
    if (
        report.attempt_id != attempt.attempt_id
        or report.receipt_id != attempt.receipt_id
        or report.reservation_id != attempt.reservation_id
    ):
        raise ReconciliationEvidenceError("REPORT_OF_ANOTHER_ATTEMPT")
    if not report.final or report.usage.cost is None:
        raise ReconciliationEvidenceError("COST_NOT_FINAL")  # an unknown cost is never zero
    target = A.COMPLETED if report.outcome == "COMPLETED" else A.FAILED_DEFINITE
    _settle(deps, attempt, target, report.usage.cost.micro_units, reports.report_ref(report))
    return ReconcileResult(Reconciled.EXECUTED if target is A.COMPLETED else Reconciled.FAILED)


def declare_unresolved(deps: RecoveryDeps, run_id: RunId) -> Run:
    """The owner records that an unknown external effect cannot be resolved (A):
    the Run FAILS with UNRESOLVED_EXTERNAL_EFFECT; the unknown holds are kept."""
    run = deps.sm.state.runs[run_id]
    unknown = [a for a in _run_attempts(deps.sm.state, run_id) if a.state is A.OUTCOME_UNKNOWN]
    if run.status is not R.RECONCILIATION_REQUIRED or not unknown:
        raise RecoveryError("NOTHING_UNRESOLVED")
    termination = Termination(
        kind="termination",
        reason=TerminationReason.UNRESOLVED_EXTERNAL_EFFECT,
        evidence_ref=sha256_digest(f"recovery:unresolved:{run_id}".encode()),
    )
    return deps.sm.set_run_status(run, R.FAILED, termination)


# ================================================================ temporary files


class TemporaryFiles(Protocol):
    def recover_temporaries(
        self, *, policy: Policy | None, tenant_id: str, receipts: Collection[str]
    ) -> Any: ...


@dataclass(frozen=True)
class TemporaryRecovery:
    removed: tuple[str, ...]
    kept: tuple[str, ...]
    listed_receipts: tuple[str, ...]


def recover_temporaries(
    deps: RecoveryDeps, files: TemporaryFiles, *, workspace_ref: str
) -> TemporaryRecovery:
    """An explicit owner operation, never part of ``recover``: WP-08's removal of
    the temporary files of writes on this tree whose outcome is not known
    (``recovery_receipts``, WP08-R07). The broker checks the current policy's
    trusted tree, the owner, the name form and the receipt tag under its writer
    lock; targets are never removed. The outcomes stay unknown (removing a
    temporary file neither proves nor runs anything)."""
    receipts = recovery_receipts(
        ledger=deps.ledger, attempts=deps.sm.state.attempts.values(), workspace_ref=workspace_ref
    )
    tenant = str(deps.sm.tenant_id)
    result = files.recover_temporaries(
        policy=deps.ledger.policy(tenant), tenant_id=tenant, receipts=receipts
    )
    return TemporaryRecovery(tuple(result.removed), tuple(result.kept), receipts)
