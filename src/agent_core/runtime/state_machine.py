"""Pure state machine for Task, Run, Step and Attempt (WP-04). No I/O.

- Transition tables (``*_TRANSITIONS``) list every allowed edge explicitly.
  Terminal states have no outgoing edge (A.I7).
- Planners (``plan_*``) take the current ``RuntimeState``, a snapshot and a
  target, check the edge, the preconditions and the evidence, and return the
  transition record with the computed ``after`` snapshot. They never trust a
  caller-made ``after``.
- ``apply_transition`` re-plans a recorded transition and requires it to be
  identical, so a journal event cannot smuggle in a state or field change.
- ``apply_event`` / ``rebuild`` fold journal events; anything unknown or
  inconsistent raises ``StateRebuildError`` and is never skipped.

Evidence is checked for consistency only (IDs, hashes, kinds). Confirming that
it is genuine is the ``EvidenceVerifier`` port's job (runtime/service.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from agent_core.types.errors import (
    DuplicateEntityError,
    IllegalTransitionError,
    StaleStateError,
    StateIndexError,
    StateRebuildError,
    StateTransitionError,
    TransitionPreconditionError,
    UnknownEntityError,
)
from agent_core.types.event_hash import event_hash
from agent_core.types.events import Event
from agent_core.types.ids import (
    AttemptId,
    PermitId,
    ReceiptId,
    ReservationId,
    RunId,
    StepId,
    TaskId,
)
from agent_core.types.records import (
    TERMINAL_RUN_STATUSES,
    Attempt,
    AttemptState,
    Run,
    RunStatus,
    Step,
    StepStatus,
    Task,
    TaskStatus,
)
from agent_core.types.transitions import (
    RUNTIME_EVENT_SCHEMA_VERSION,
    RUNTIME_EVENT_TYPE,
    AbortEvidence,
    ApprovalEvidence,
    AttemptEvidence,
    AttemptTransition,
    ContentFixedEvidence,
    DispatchEvidence,
    EntityAction,
    IntentEvidence,
    OutcomeEvidence,
    PermitConsumedEvidence,
    PermitIssuedEvidence,
    PolicyDecisionEvidence,
    ReconciledOutcome,
    ReconciliationEvidence,
    ReconciliationResolution,
    ReconciliationTrigger,
    ReconciliationTriggerReason,
    ReservationEvidence,
    ResolutionDecision,
    RunEvidence,
    RuntimePayload,
    RunTransition,
    StepTransition,
    TaskAction,
    TaskTransition,
    Termination,
    Transition,
    ValidationEvidence,
    VerificationEvidence,
)

A, R, S, T = AttemptState, RunStatus, StepStatus, TaskStatus


def _table[E](edges: dict[E, set[E]]) -> Mapping[E, frozenset[E]]:
    return MappingProxyType({k: frozenset(v) for k, v in edges.items()})


_STOP = {R.FAILED, R.ABORTED_SAFETY}

# A 6. PAUSED_BUDGET -> CANCELLING is a WP-04 decision (the diagram shows WAITING_*).
RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = _table(
    {
        R.CREATED: {R.QUEUED} | _STOP,
        R.QUEUED: {R.RUNNING, R.RECOVERING} | _STOP,
        R.RUNNING: {
            R.WAITING_TOOL,
            R.WAITING_APPROVAL,
            R.PAUSED_BUDGET,
            R.VERIFYING,
            R.CANCELLING,
            R.RECONCILIATION_REQUIRED,
            R.RECOVERING,
        }
        | _STOP,
        R.WAITING_TOOL: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
        R.WAITING_APPROVAL: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
        R.PAUSED_BUDGET: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
        R.VERIFYING: {R.COMPLETED, R.RUNNING, R.RECOVERING} | _STOP,
        R.RECOVERING: {R.QUEUED, R.RECONCILIATION_REQUIRED} | _STOP,
        R.CANCELLING: {R.CANCELLED, R.RECONCILIATION_REQUIRED} | _STOP,
        R.RECONCILIATION_REQUIRED: {R.RUNNING, R.CANCELLED} | _STOP,
        R.COMPLETED: set(),
        R.CANCELLED: set(),
        R.FAILED: set(),
        R.ABORTED_SAFETY: set(),
    }
)

_ABORT = A.ABORTED_BEFORE_DISPATCH
# E 3.1 with E 38 (reservation ID allocated at CONTENT_FIXED).
ATTEMPT_TRANSITIONS: Mapping[AttemptState, frozenset[AttemptState]] = _table(
    {
        A.PROPOSED: {A.VALIDATED, _ABORT},
        A.VALIDATED: {A.CONTENT_FIXED, _ABORT},
        A.CONTENT_FIXED: {A.POLICY_ALLOWED, _ABORT},
        A.POLICY_ALLOWED: {A.APPROVAL_PENDING, A.BUDGET_RESERVED, _ABORT},
        A.APPROVAL_PENDING: {A.APPROVED, _ABORT},
        A.APPROVED: {A.BUDGET_RESERVED, _ABORT},
        A.BUDGET_RESERVED: {A.INTENT_RECORDED, _ABORT},
        A.INTENT_RECORDED: {A.PERMIT_ISSUED, _ABORT},
        A.PERMIT_ISSUED: {A.PERMIT_CONSUMED, _ABORT},
        A.PERMIT_CONSUMED: {A.DISPATCH_AUTHORIZED, A.OUTCOME_UNKNOWN, _ABORT},
        A.DISPATCH_AUTHORIZED: {A.DISPATCH_STARTED, A.OUTCOME_UNKNOWN, _ABORT},
        A.DISPATCH_STARTED: {A.COMPLETED, A.FAILED_DEFINITE, A.OUTCOME_UNKNOWN},
        A.OUTCOME_UNKNOWN: {A.COMPLETED, A.FAILED_DEFINITE},
        A.COMPLETED: set(),
        A.FAILED_DEFINITE: set(),
        _ABORT: set(),
    }
)

STEP_TRANSITIONS: Mapping[StepStatus, frozenset[StepStatus]] = _table(
    {
        S.PLANNED: {S.IN_PROGRESS, S.CANCELLED},
        S.IN_PROGRESS: {S.COMMITTED, S.FAILED, S.RECONCILIATION_REQUIRED, S.CANCELLED},
        S.RECONCILIATION_REQUIRED: {S.COMMITTED, S.FAILED, S.CANCELLED},
        S.COMMITTED: set(),
        S.FAILED: set(),
        S.CANCELLED: set(),
    }
)

TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = _table(
    {
        T.CREATED: {T.ACTIVE, T.CANCELLED, T.FAILED},
        T.ACTIVE: {T.PAUSED, T.COMPLETED, T.FAILED, T.CANCELLED},
        T.PAUSED: {T.ACTIVE, T.FAILED, T.CANCELLED},
        T.COMPLETED: set(),
        T.FAILED: set(),
        T.CANCELLED: set(),
    }
)

TERMINAL_TASK_STATUSES = frozenset({T.COMPLETED, T.FAILED, T.CANCELLED})
TERMINAL_STEP_STATUSES = frozenset({S.COMMITTED, S.FAILED, S.CANCELLED})
FINAL_ATTEMPT_STATES = frozenset({A.COMPLETED, A.FAILED_DEFINITE, _ABORT})
_OUTCOME_TARGETS = frozenset({A.COMPLETED, A.FAILED_DEFINITE, A.OUTCOME_UNKNOWN, _ABORT})
_PEP_INVOLVED = frozenset({A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED})
# Run states in which attempts may make progress, and in which results may arrive.
_ACTIVE_RUN = frozenset({R.RUNNING, R.WAITING_TOOL, R.WAITING_APPROVAL})
_RESULT_RUN = _ACTIVE_RUN | {
    R.CANCELLING,
    R.RECONCILIATION_REQUIRED,
    R.RECOVERING,
    # WP04-R03: a terminal run never resumes, but its unresolved attempts can still
    # be settled (result, unknown, abort or reconciliation) and the record kept.
    R.FAILED,
    R.ABORTED_SAFETY,
}


def check_edge(table: Mapping[Any, frozenset[Any]], current: Any, target: Any, what: str) -> None:
    if target not in table[current]:
        raise IllegalTransitionError(f"{what}: {current} -> {target} is not allowed")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TransitionPreconditionError(message)


def _frozen[K, V](items: dict[K, V]) -> Mapping[K, V]:
    return MappingProxyType(items)


# ---------------------------------------------------------------- state


@dataclass(frozen=True)
class RuntimeState:
    """Immutable view of all entities of one tenant, folded from the Journal."""

    tasks: Mapping[TaskId, Task] = field(default_factory=lambda: _frozen({}))
    runs: Mapping[RunId, Run] = field(default_factory=lambda: _frozen({}))
    steps: Mapping[StepId, Step] = field(default_factory=lambda: _frozen({}))
    attempts: Mapping[AttemptId, Attempt] = field(default_factory=lambda: _frozen({}))
    run_steps: Mapping[RunId, tuple[StepId, ...]] = field(default_factory=lambda: _frozen({}))
    step_attempts: Mapping[StepId, tuple[AttemptId, ...]] = field(
        default_factory=lambda: _frozen({})
    )
    # Last journal run_seq per run (positions come from the Journal, never from IDs).
    run_seqs: Mapping[RunId, int] = field(default_factory=lambda: _frozen({}))
    # P-7 M1, derived (never a source of truth): per Run, the number of its
    # Attempts outside FINAL_ATTEMPT_STATES; a Run with none has no entry. Kept
    # by ``_install`` and checked against the Attempts by ``check_indexes``.
    unresolved_attempts: Mapping[RunId, int] = field(default_factory=lambda: _frozen({}))
    # P-7 2nd stage, derived (never a source of truth): per kind, each ID an
    # Attempt holds and that Attempt. An ID once used is never used again, by any
    # Attempt in any state (``_unused``). Kept by ``_install``, checked by
    # ``check_indexes``.
    used_reservation_ids: Mapping[ReservationId, AttemptId] = field(
        default_factory=lambda: _frozen({})
    )
    used_permit_ids: Mapping[PermitId, AttemptId] = field(default_factory=lambda: _frozen({}))
    used_receipt_ids: Mapping[ReceiptId, AttemptId] = field(default_factory=lambda: _frozen({}))

    @classmethod
    def empty(cls) -> RuntimeState:
        return cls()

    def steps_of_run(self, run_id: RunId) -> tuple[Step, ...]:
        return tuple(self.steps[s] for s in self.run_steps.get(run_id, ()))

    def attempts_of_step(self, step_id: StepId) -> tuple[Attempt, ...]:
        return tuple(self.attempts[a] for a in self.step_attempts.get(step_id, ()))

    def attempts_of_run(self, run_id: RunId) -> tuple[Attempt, ...]:
        return tuple(a for s in self.run_steps.get(run_id, ()) for a in self.attempts_of_step(s))

    def run_of_attempt(self, attempt: Attempt) -> Run:
        return self.runs[self.steps[attempt.step_id].run_id]

    def _replace(self, **changes: Mapping[Any, Any]) -> RuntimeState:
        values = {name: getattr(self, name) for name in self.__dataclass_fields__}
        values.update(changes)
        return RuntimeState(**values)

    def _with(self, name: str, key: Any, value: Any) -> RuntimeState:
        items = dict(getattr(self, name))
        items[key] = value
        return self._replace(**{name: _frozen(items)})

    def used_ids(self, kind: str) -> Mapping[Any, AttemptId]:
        """The used IDs of one kind (``reservation_id``, ``permit_id``, ``receipt_id``)."""
        table: Mapping[Any, AttemptId] = getattr(self, USED_ID_TABLES[kind])
        return table

    def run_settled(self, run_id: RunId) -> bool:
        """No Attempt of the Run is unresolved (answered by the M1 index)."""
        return run_id not in self.unresolved_attempts


# The Attempt fields whose values are never used twice, and their index tables.
USED_ID_TABLES: dict[str, str] = {
    "reservation_id": "used_reservation_ids",
    "permit_id": "used_permit_ids",
    "receipt_id": "used_receipt_ids",
}


def check_indexes(state: RuntimeState) -> None:
    """Fail closed unless every derived index equals what the entities give."""
    counts: dict[RunId, int] = {}
    used: dict[str, dict[Any, AttemptId]] = {kind: {} for kind in USED_ID_TABLES}
    for attempt in state.attempts.values():
        if attempt.state not in FINAL_ATTEMPT_STATES:
            step = state.steps.get(attempt.step_id)
            if step is None:
                raise StateIndexError("an attempt has no step")
            counts[step.run_id] = counts.get(step.run_id, 0) + 1
        for kind, ids in used.items():
            value = getattr(attempt, kind)
            if value is not None:
                if value in ids:
                    raise StateIndexError(f"two attempts hold the same {kind}")
                ids[value] = attempt.attempt_id
    if dict(state.unresolved_attempts) != counts:
        raise StateIndexError("the unresolved attempt index does not match the attempts")
    for kind, ids in used.items():
        if dict(state.used_ids(kind)) != ids:
            raise StateIndexError(f"the used {kind} index does not match the attempts")


def _current[K, V](items: Mapping[K, V], key: K, before: V, what: str) -> V:
    if key not in items:
        raise UnknownEntityError(f"unknown {what}")
    if items[key] != before:
        raise StaleStateError(f"{what} snapshot is not the current state")
    return items[key]


def _all_final(attempts: Iterable[Attempt]) -> bool:
    return all(a.state in FINAL_ATTEMPT_STATES for a in attempts)


def _task_settled(state: RuntimeState, task: Task) -> bool:
    """No attempt of any run of the task is unresolved (WP04-R03).

    A terminal run does not settle its attempts: an OUTCOME_UNKNOWN or an
    in-flight attempt (reservation held, permit consumed) stays unresolved.
    """
    return all(state.run_settled(r) for r in task.runs)


# ---------------------------------------------------------------- tasks


def plan_task_create(state: RuntimeState, task: Task) -> TaskTransition:
    if task.task_id in state.tasks:
        raise DuplicateEntityError("task already exists")
    _require(task.status is T.CREATED and task.runs == (), "a new task starts CREATED, no runs")
    return TaskTransition(entity="task", action=TaskAction.CREATE, before=None, after=task)


def plan_task_status(state: RuntimeState, before: Task, target: TaskStatus) -> TaskTransition:
    _current(state.tasks, before.task_id, before, "task")
    check_edge(TASK_TRANSITIONS, before.status, target, "task")
    runs = [state.runs[r] for r in before.runs]
    if target is T.COMPLETED:
        _require(bool(runs), "a task completes only through a completed run")
        _require(runs[-1].status is R.COMPLETED, "the last run must be COMPLETED")
    if target in (T.COMPLETED, T.CANCELLED, T.FAILED, T.PAUSED):
        _require(all(r.status in TERMINAL_RUN_STATUSES for r in runs), "a run is still open")
    if target in (T.COMPLETED, T.CANCELLED, T.PAUSED, T.ACTIVE):
        # FAILED stays possible: accepting a permanently unknown effect (A 6.1).
        _require(_task_settled(state, before), "unresolved side effects remain in the task")
    after = before.evolve(status=target)
    return TaskTransition(entity="task", action=TaskAction.SET_STATUS, before=before, after=after)


def plan_task_attach(state: RuntimeState, before: Task, run_id: RunId) -> TaskTransition:
    _current(state.tasks, before.task_id, before, "task")
    _require(before.status is T.ACTIVE, "runs attach only to an ACTIVE task")
    run = state.runs.get(run_id)
    _require(run is not None and run.task_id == before.task_id, "run belongs to another task")
    _require(run_id not in before.runs, "run is already attached")
    after = before.evolve(runs=(*before.runs, run_id))
    return TaskTransition(entity="task", action=TaskAction.ATTACH_RUN, before=before, after=after)


# ---------------------------------------------------------------- runs


def plan_run_create(state: RuntimeState, run: Run) -> RunTransition:
    if run.run_id in state.runs:
        raise DuplicateEntityError("run already exists")
    task = state.tasks.get(run.task_id)
    if task is None:
        raise UnknownEntityError("unknown task")
    _require(task.status is T.ACTIVE, "a run starts only in an ACTIVE task")
    _require(
        all(state.runs[r].status in TERMINAL_RUN_STATUSES for r in task.runs),
        "the task already has an open run",
    )
    _require(
        _task_settled(state, task),
        "unresolved side effects remain; a new run could repeat them (WP04-R03)",
    )
    expected_parent = task.runs[-1] if task.runs else None
    _require(run.parent_run_id == expected_parent, "a new run continues from the task's last run")
    _require(run.status is R.CREATED, "a new run starts CREATED")
    _require(not run.cancel_requested, "a new run has no cancel request")
    return RunTransition(
        entity="run", action=EntityAction.CREATE, before=None, after=run, evidence=None
    )


def _no_evidence(evidence: object) -> None:
    _require(evidence is None, "this transition takes no evidence")


def plan_run_status(
    state: RuntimeState, before: Run, target: RunStatus, evidence: RunEvidence | None
) -> RunTransition:
    _current(state.runs, before.run_id, before, "run")
    check_edge(RUN_TRANSITIONS, before.status, target, "run")
    task = state.tasks[before.task_id]
    run_id = before.run_id

    def settled() -> bool:
        return state.run_settled(run_id) and all(
            s.status in TERMINAL_STEP_STATUSES for s in state.steps_of_run(run_id)
        )

    def unknown() -> bool:
        if state.run_settled(run_id):
            return False  # an OUTCOME_UNKNOWN attempt is never final
        return any(a.state is A.OUTCOME_UNKNOWN for a in state.attempts_of_run(run_id))

    source = before.status

    if target in (R.FAILED, R.ABORTED_SAFETY):
        _require(isinstance(evidence, Termination), "a termination reason is required")
    elif target is R.COMPLETED:
        verification = _evidence(evidence, VerificationEvidence)
        _require(verification.passed, "verification did not pass")
        refs = [c.criterion_ref for c in verification.criteria]
        _require(
            len(set(refs)) == len(refs) and set(refs) == set(task.done_criteria),
            "verification must cover exactly the task's done criteria",
        )
        _require(settled(), "unsettled attempts, reservations or steps remain (A.I16)")
    elif target is R.VERIFYING:
        _no_evidence(evidence)
        _require(settled(), "attempts and steps must be settled before verification")
    elif target is R.RECONCILIATION_REQUIRED:
        trigger = _evidence(evidence, ReconciliationTrigger)
        if trigger.reason is ReconciliationTriggerReason.OUTCOME_UNKNOWN:
            _require(unknown(), "no attempt has an unknown outcome")
    elif target is R.CANCELLED:
        if source is R.RECONCILIATION_REQUIRED:
            _require(
                isinstance(evidence, ReconciliationResolution)
                and evidence.decision is ResolutionDecision.CANCEL,
                "a CANCEL resolution is required",
            )
        else:
            _no_evidence(evidence)
        _require(settled(), "unsettled attempts or steps remain (A.I16)")
    elif target is R.RUNNING and source is R.VERIFYING:
        _require(
            isinstance(evidence, VerificationEvidence) and not evidence.passed,
            "returning to RUNNING needs a failed verification",
        )
    elif target is R.RUNNING and source is R.RECONCILIATION_REQUIRED:
        _require(
            isinstance(evidence, ReconciliationResolution)
            and evidence.decision is ResolutionDecision.RESUME,
            "a RESUME resolution is required",
        )
        _require(not unknown(), "unknown outcomes must be reconciled first")
        _require(not before.cancel_requested, "a cancel request cannot be resumed (WP04-R04)")
    elif target is R.QUEUED and source is R.RECOVERING:
        _no_evidence(evidence)
        _require(not unknown(), "unknown outcomes need reconciliation, not a restart")
    else:
        _no_evidence(evidence)
    if target in (R.QUEUED, R.RUNNING):
        _require(task.status is T.ACTIVE, "the task is not ACTIVE")
    cancel = before.cancel_requested or target is R.CANCELLING
    after = before.evolve(status=target, cancel_requested=cancel)
    return RunTransition(
        entity="run", action=EntityAction.SET_STATUS, before=before, after=after, evidence=evidence
    )


# ---------------------------------------------------------------- steps


def plan_step_create(state: RuntimeState, step: Step) -> StepTransition:
    if step.step_id in state.steps:
        raise DuplicateEntityError("step already exists")
    run = state.runs.get(step.run_id)
    if run is None:
        raise UnknownEntityError("unknown run")
    _require(run.status is R.RUNNING, "steps are created only while the run is RUNNING")
    _require(
        state.run_settled(step.run_id),
        "an attempt of the run is unresolved; a new step could repeat it (WP04-R03)",
    )
    _require(step.status is S.PLANNED, "a new step starts PLANNED")
    _require(
        step.run_seq == state.run_seqs.get(step.run_id, 0) + 1,
        "step run_seq must be the journal position of its creation",
    )
    return StepTransition(entity="step", action=EntityAction.CREATE, before=None, after=step)


def plan_step_status(state: RuntimeState, before: Step, target: StepStatus) -> StepTransition:
    _current(state.steps, before.step_id, before, "step")
    check_edge(STEP_TRANSITIONS, before.status, target, "step")
    run = state.runs[before.run_id]
    attempts = state.attempts_of_step(before.step_id)
    if target is S.IN_PROGRESS:
        _require(run.status is R.RUNNING, "the run is not RUNNING")
    elif target is S.COMMITTED:
        _require(
            run.status in (R.RUNNING, R.RECONCILIATION_REQUIRED) and not run.cancel_requested,
            "results are not used after a cancel request or outside RUNNING (late results)",
        )
        _require(
            bool(attempts) and _all_final(attempts) and attempts[-1].state is A.COMPLETED,
            "a step commits only after its latest attempt COMPLETED",
        )
    elif target is S.FAILED:
        _require(run.status in (R.RUNNING, R.RECONCILIATION_REQUIRED), "the run is not RUNNING")
        _require(_all_final(attempts), "attempts are still open or unknown")
    elif target is S.RECONCILIATION_REQUIRED:
        _require(any(a.state is A.OUTCOME_UNKNOWN for a in attempts), "no unknown outcome")
    elif target is S.CANCELLED:
        _require(
            run.status in (R.RUNNING, R.CANCELLING, R.RECONCILIATION_REQUIRED),
            "the run is not RUNNING, CANCELLING or RECONCILIATION_REQUIRED",
        )
        _require(_all_final(attempts), "attempts are still open or unknown")
    after = before.evolve(status=target)
    return StepTransition(entity="step", action=EntityAction.SET_STATUS, before=before, after=after)


# ---------------------------------------------------------------- attempts


def plan_attempt_create(state: RuntimeState, attempt: Attempt) -> AttemptTransition:
    if attempt.attempt_id in state.attempts:
        raise DuplicateEntityError("attempt already exists; a retry needs a new attempt")
    step = state.steps.get(attempt.step_id)
    if step is None:
        raise UnknownEntityError("unknown step")
    run = state.runs[step.run_id]
    _require(
        run.status is R.RUNNING and not run.cancel_requested,
        "attempts are created only while the run is RUNNING and not cancelled",
    )
    _require(step.status is S.IN_PROGRESS, "the step is not IN_PROGRESS")
    previous = state.attempts_of_step(step.step_id)
    _require(_all_final(previous), "an earlier attempt is still open or unknown (A.I4)")
    _require(not previous or previous[-1].state is not A.COMPLETED, "the step already succeeded")
    fresh = (
        attempt.state is A.PROPOSED
        and attempt.action_hash is None
        and attempt.permit_id is None
        and attempt.receipt_id is None
        and attempt.reservation_id is None
        and attempt.approval_required is None
    )
    _require(fresh, "a new attempt starts PROPOSED with nothing fixed")
    return AttemptTransition(
        entity="attempt",
        action=EntityAction.CREATE,
        before=None,
        after=attempt,
        evidence=None,
        late=False,
    )


def _evidence[E](evidence: object, kind: type[E]) -> E:
    if not isinstance(evidence, kind):
        raise TransitionPreconditionError(f"{kind.__name__} is required")
    return evidence


def _unused(state: RuntimeState, name: str, value: object) -> None:
    # The same rule as a scan of every Attempt, answered by the derived index
    # (``value`` is never None here: the evidence types require it).
    _require(value not in state.used_ids(name), f"{name} is already used by another attempt")


def plan_attempt_status(
    state: RuntimeState,
    before: Attempt,
    target: AttemptState,
    evidence: AttemptEvidence | None,
) -> AttemptTransition:
    _current(state.attempts, before.attempt_id, before, "attempt")
    check_edge(ATTEMPT_TRANSITIONS, before.state, target, "attempt")
    run = state.run_of_attempt(before)
    if target in _OUTCOME_TARGETS:
        _require(run.status in _RESULT_RUN, "the run cannot accept results now")
    else:
        _require(
            run.status in _ACTIVE_RUN and not run.cancel_requested,
            "attempts progress only while the run is active and not cancelled",
        )
    # A result after a cancel request, or in a terminal run, is late (A 6): it is
    # recorded and settles the attempt, but it is never used to make progress.
    late = target in _OUTCOME_TARGETS and (
        run.cancel_requested or run.status in TERMINAL_RUN_STATUSES
    )
    action = before.action_hash
    changes: dict[str, Any] = {}

    if target is A.VALIDATED:
        _evidence(evidence, ValidationEvidence)
    elif target is A.CONTENT_FIXED:
        ev_fixed = _evidence(evidence, ContentFixedEvidence)
        _unused(state, "reservation_id", ev_fixed.reservation_id)
        changes = {"action_hash": ev_fixed.action_hash, "reservation_id": ev_fixed.reservation_id}
    elif target is A.POLICY_ALLOWED:
        ev_policy = _evidence(evidence, PolicyDecisionEvidence)
        _require(ev_policy.action_hash == action, "policy decision is for another action")
        changes = {"approval_required": ev_policy.approval_required}
    elif target is A.APPROVAL_PENDING:
        _no_evidence(evidence)
        _require(before.approval_required is True, "this attempt does not require approval")
    elif target is A.APPROVED:
        ev_approval = _evidence(evidence, ApprovalEvidence)
        _require(ev_approval.action_hash == action, "approval is for another action")
        _require(
            ev_approval.reservation_id == before.reservation_id,
            "approval is for another reservation (AUDIT-01)",
        )
    elif target is A.BUDGET_RESERVED:
        ev_reserve = _evidence(evidence, ReservationEvidence)
        if before.state is A.POLICY_ALLOWED:
            _require(before.approval_required is False, "approval is required first")
        _require(ev_reserve.action_hash == action, "reservation is for another action")
        _require(
            ev_reserve.reservation_id == before.reservation_id,
            "reservation must activate the allocated ID (AUDIT-01)",
        )
    elif target is A.INTENT_RECORDED:
        ev_intent = _evidence(evidence, IntentEvidence)
        _require(ev_intent.action_hash == action, "intent is for another action")
    elif target is A.PERMIT_ISSUED:
        ev_permit = _evidence(evidence, PermitIssuedEvidence)
        _require(ev_permit.action_hash == action, "permit is for another action")
        _require(
            ev_permit.reservation_id == before.reservation_id, "permit names another reservation"
        )
        _unused(state, "permit_id", ev_permit.permit_id)
        changes = {"permit_id": ev_permit.permit_id}
    elif target is A.PERMIT_CONSUMED:
        ev_consumed = _evidence(evidence, PermitConsumedEvidence)
        _require(ev_consumed.permit_id == before.permit_id, "receipt is for another permit")
        _unused(state, "receipt_id", ev_consumed.receipt_id)
        changes = {"receipt_id": ev_consumed.receipt_id}
    elif target in (A.DISPATCH_AUTHORIZED, A.DISPATCH_STARTED):
        ev_dispatch = _evidence(evidence, DispatchEvidence)
        _require(ev_dispatch.receipt_id == before.receipt_id, "report is for another receipt")
    elif target in (A.COMPLETED, A.FAILED_DEFINITE) and before.state is A.OUTCOME_UNKNOWN:
        ev_rec = _evidence(evidence, ReconciliationEvidence)
        wanted = ReconciledOutcome.COMPLETED if target is A.COMPLETED else ReconciledOutcome.FAILED
        _require(ev_rec.outcome is wanted, "reconciliation reached another outcome")
    elif target in (A.COMPLETED, A.FAILED_DEFINITE, A.OUTCOME_UNKNOWN):
        ev_outcome = _evidence(evidence, OutcomeEvidence)
        _require(ev_outcome.receipt_id == before.receipt_id, "result is for another receipt")
    elif target is _ABORT:
        ev_abort = _evidence(evidence, AbortEvidence)
        if before.state in _PEP_INVOLVED:
            _require(
                ev_abort.not_dispatched_ref is not None,
                "after consumption, a live PEP must report that nothing was sent",
            )
    after = before.evolve(state=target, **changes)
    return AttemptTransition(
        entity="attempt",
        action=EntityAction.SET_STATUS,
        before=before,
        after=after,
        evidence=evidence,
        late=late,
    )


# ---------------------------------------------------------------- applying

# The tables of ``RuntimeState`` whose entries ``_install`` adds to an ordered tuple.
_ORDERED = frozenset({"run_steps", "step_attempts"})


class _Writer(Protocol):
    """Where ``_install`` writes: the one place the entity tables change.

    ``_CopyOnWrite`` is the live path (a new immutable state per transition);
    ``_Builder`` is the Rebuild's private, in-place one (P-7 M2). Both are
    written by the same ``_install`` after the same ``_replan`` checks.
    """

    def current(self, name: str) -> Mapping[Any, Any]: ...

    def put(self, name: str, key: Any, value: Any) -> None: ...

    def append(self, name: str, key: Any, value: Any) -> None: ...

    def drop(self, name: str, key: Any) -> None: ...


class _CopyOnWrite:
    """Copies a table of ``base`` the first time it is written, never ``base``."""

    def __init__(self, base: RuntimeState) -> None:
        self._base = base
        self._tables: dict[str, dict[Any, Any]] = {}

    def current(self, name: str) -> Mapping[Any, Any]:
        table = self._tables.get(name)
        return table if table is not None else getattr(self._base, name)

    def _own(self, name: str) -> dict[Any, Any]:
        if name not in self._tables:
            self._tables[name] = dict(getattr(self._base, name))
        return self._tables[name]

    def put(self, name: str, key: Any, value: Any) -> None:
        self._own(name)[key] = value

    def append(self, name: str, key: Any, value: Any) -> None:
        table = self._own(name)
        table[key] = (*table.get(key, ()), value)

    def drop(self, name: str, key: Any) -> None:
        self._own(name).pop(key, None)

    def result(self) -> RuntimeState:
        if not self._tables:
            return self._base
        return self._base._replace(**{n: _frozen(t) for n, t in self._tables.items()})


class _Builder:
    """The Rebuild's private state (P-7 M2): plain dictionaries written in place.

    ``view`` is a ``RuntimeState`` over read-only views of those dictionaries, so
    the ``plan_*`` checks read the state as it is built. Ordered tables hold
    lists while building. ``freeze`` turns them into tuples and returns the
    immutable state once; the builder is unusable afterwards. Nothing of it
    leaves the Rebuild: on any failure it is dropped.
    """

    def __init__(self) -> None:
        names = tuple(RuntimeState.__dataclass_fields__)
        self._tables: dict[str, dict[Any, Any]] | None = {n: {} for n in names}
        self.view = RuntimeState(**{n: MappingProxyType(t) for n, t in self._tables.items()})

    def _live(self) -> dict[str, dict[Any, Any]]:
        if self._tables is None:
            raise StateRebuildError("the rebuild builder is already frozen")
        return self._tables

    def current(self, name: str) -> Mapping[Any, Any]:
        return self._live()[name]

    def put(self, name: str, key: Any, value: Any) -> None:
        self._live()[name][key] = value

    def append(self, name: str, key: Any, value: Any) -> None:
        self._live()[name].setdefault(key, []).append(value)

    def drop(self, name: str, key: Any) -> None:
        self._live()[name].pop(key, None)

    def freeze(self) -> RuntimeState:
        tables = self._live()
        self._tables = None
        for name in _ORDERED:
            table = tables[name]
            for key, items in table.items():
                table[key] = tuple(items)
        return RuntimeState(**{n: _frozen(t) for n, t in tables.items()})


def _count(writer: _Writer, run_id: RunId, delta: int) -> None:
    count = writer.current("unresolved_attempts").get(run_id, 0) + delta
    if count < 0:
        raise StateIndexError("the unresolved attempt count of a run is below zero")
    if count:
        writer.put("unresolved_attempts", run_id, count)
    else:
        writer.drop("unresolved_attempts", run_id)


def _use_id(writer: _Writer, kind: str, value: Any, attempt_id: AttemptId) -> None:
    table = USED_ID_TABLES[kind]
    if value in writer.current(table):
        raise StateIndexError(f"the {kind} is already in the index")
    writer.put(table, value, attempt_id)


def _release_id(writer: _Writer, kind: str, value: Any, attempt_id: AttemptId) -> None:
    table = USED_ID_TABLES[kind]
    if writer.current(table).get(value) != attempt_id:
        raise StateIndexError(f"the {kind} index does not name this attempt")
    writer.drop(table, value)


def _install(writer: _Writer, transition: Transition) -> None:
    if isinstance(transition, TaskTransition):
        writer.put("tasks", transition.after.task_id, transition.after)
        return
    if isinstance(transition, RunTransition):
        writer.put("runs", transition.after.run_id, transition.after)
        return
    if isinstance(transition, StepTransition):
        step = transition.after
        writer.put("steps", step.step_id, step)
        if transition.action is EntityAction.CREATE:
            writer.append("run_steps", step.run_id, step.step_id)
        return
    attempt = transition.after
    previous = writer.current("attempts").get(attempt.attempt_id)
    was_open = previous is not None and previous.state not in FINAL_ATTEMPT_STATES
    now_open = attempt.state not in FINAL_ATTEMPT_STATES
    if was_open != now_open:
        run_id = writer.current("steps")[attempt.step_id].run_id
        _count(writer, run_id, 1 if now_open else -1)
    for kind in USED_ID_TABLES:
        held = None if previous is None else getattr(previous, kind)
        value = getattr(attempt, kind)
        if held != value:
            if held is not None:
                _release_id(writer, kind, held, attempt.attempt_id)
            if value is not None:
                _use_id(writer, kind, value, attempt.attempt_id)
    writer.put("attempts", attempt.attempt_id, attempt)
    if transition.action is EntityAction.CREATE:
        writer.append("step_attempts", attempt.step_id, attempt.attempt_id)


def _replan(state: RuntimeState, transition: Transition) -> Transition:
    if isinstance(transition, TaskTransition):
        if transition.action is TaskAction.CREATE:
            return plan_task_create(state, transition.after)
        if transition.before is None:
            raise TransitionPreconditionError("missing 'before' snapshot")
        if transition.action is TaskAction.ATTACH_RUN:
            added = transition.after.runs[len(transition.before.runs) :]
            _require(len(added) == 1, "exactly one run is attached")
            return plan_task_attach(state, transition.before, added[0])
        return plan_task_status(state, transition.before, transition.after.status)
    if isinstance(transition, RunTransition):
        if transition.action is EntityAction.CREATE:
            return plan_run_create(state, transition.after)
        if transition.before is None:
            raise TransitionPreconditionError("missing 'before' snapshot")
        return plan_run_status(
            state, transition.before, transition.after.status, transition.evidence
        )
    if isinstance(transition, StepTransition):
        if transition.action is EntityAction.CREATE:
            return plan_step_create(state, transition.after)
        if transition.before is None:
            raise TransitionPreconditionError("missing 'before' snapshot")
        return plan_step_status(state, transition.before, transition.after.status)
    if transition.action is EntityAction.CREATE:
        return plan_attempt_create(state, transition.after)
    if transition.before is None:
        raise TransitionPreconditionError("missing 'before' snapshot")
    return plan_attempt_status(
        state, transition.before, transition.after.state, transition.evidence
    )


def _checked(state: RuntimeState, transition: Transition) -> None:
    if _replan(state, transition) != transition:
        raise TransitionPreconditionError("recorded transition does not follow the rules")


def apply_transition(state: RuntimeState, transition: Transition) -> RuntimeState:
    """Re-plan ``transition`` against ``state``; it must match exactly."""
    _checked(state, transition)
    writer = _CopyOnWrite(state)
    _install(writer, transition)
    return writer.result()


def _run_of(state: RuntimeState, transition: Transition) -> RunId | None:
    if isinstance(transition, TaskTransition):
        if transition.action is TaskAction.ATTACH_RUN:
            return transition.after.runs[-1]
        return None
    if isinstance(transition, (RunTransition, StepTransition)):
        return transition.after.run_id
    step = state.steps.get(transition.after.step_id)
    return None if step is None else step.run_id


class _Fold(Protocol):
    """A state being folded: the immutable live one or the Rebuild's builder."""

    @property
    def view(self) -> RuntimeState: ...

    def transition(self, transition: Transition) -> None: ...

    def set_run_seq(self, run_id: RunId, run_seq: int) -> None: ...


class _ImmutableFold:
    def __init__(self, state: RuntimeState) -> None:
        self.view = state

    def transition(self, transition: Transition) -> None:
        self.view = apply_transition(self.view, transition)

    def set_run_seq(self, run_id: RunId, run_seq: int) -> None:
        writer = _CopyOnWrite(self.view)
        writer.put("run_seqs", run_id, run_seq)
        self.view = writer.result()


class _BuilderFold:
    def __init__(self) -> None:
        self.builder = _Builder()

    @property
    def view(self) -> RuntimeState:
        return self.builder.view

    def transition(self, transition: Transition) -> None:
        _checked(self.builder.view, transition)
        _install(self.builder, transition)

    def set_run_seq(self, run_id: RunId, run_seq: int) -> None:
        self.builder.put("run_seqs", run_id, run_seq)


def _fold_payload(fold: _Fold, payload: RuntimePayload) -> RunId | None:
    runs: set[RunId] = set()
    created: set[RunId] = set()
    attached: set[RunId] = set()
    for transition in payload.transitions:
        run_id = _run_of(fold.view, transition)
        if run_id is not None:
            runs.add(run_id)
        if isinstance(transition, RunTransition) and transition.action is EntityAction.CREATE:
            created.add(transition.after.run_id)
        if isinstance(transition, TaskTransition) and transition.action is TaskAction.ATTACH_RUN:
            attached.add(transition.after.runs[-1])
        fold.transition(transition)
    _require(len(runs) <= 1, "one event covers at most one run")
    _require(created == attached, "a run is created and attached in the same event")
    return next(iter(runs), None)


def apply_payload(
    state: RuntimeState, payload: RuntimePayload
) -> tuple[RuntimeState, RunId | None]:
    """Apply every transition of one event atomically. Returns the event's run.

    A created step's ``run_seq`` is checked by ``plan_step_create`` against the
    run's next position; ``apply_event`` checks that the event has that position.
    """
    fold = _ImmutableFold(state)
    run_id = _fold_payload(fold, payload)
    return fold.view, run_id


def _fold_event(fold: _Fold, event: Event) -> None:
    try:
        if event.type != RUNTIME_EVENT_TYPE:
            raise StateRebuildError("unknown event type")
        if event.schema_version != RUNTIME_EVENT_SCHEMA_VERSION or event.payload is None:
            raise StateRebuildError("runtime events need schema_version 3 and a payload")
        if event.run_id is not None:
            expected = fold.view.run_seqs.get(event.run_id, 0) + 1
            if event.run_seq != expected:
                raise StateRebuildError("run_seq is not the next position of the run")
        run_id = _fold_payload(fold, event.payload)
        if run_id != event.run_id:
            raise StateRebuildError("event run_id does not match its transitions")
        for transition in event.payload.transitions:
            if (
                isinstance(transition, RunTransition)
                and transition.action is EntityAction.CREATE
                and transition.after.owner_epoch != event.owner_epoch
            ):
                raise StateRebuildError("run owner_epoch differs from its creating event")
    except StateTransitionError as exc:
        raise StateRebuildError(f"invalid transition in event: {exc.code}") from exc
    if event.run_id is not None and event.run_seq is not None:
        fold.set_run_seq(event.run_id, event.run_seq)


def apply_event(state: RuntimeState, event: Event) -> RuntimeState:
    """Fold one stored journal event. Anything unknown or inconsistent is an error."""
    fold = _ImmutableFold(state)
    _fold_event(fold, event)
    return fold.view


def rebuild(events: Iterable[Event]) -> RuntimeState:
    """Full rebuild from an empty state. Deterministic. See ``rebuild_with_head``."""
    return rebuild_with_head(events)[0]


def rebuild_with_head(events: Iterable[Event]) -> tuple[RuntimeState, Event | None, str | None]:
    """Fold a tenant's complete event sequence from an empty state.

    The sequence must start at ``tenant_seq`` 1, be consecutive, belong to one
    tenant and form the hash chain (each ``prev_hash`` is the previous event's
    hash). Returns the state, the last event and its hash (the head the state
    was built from). Starting from a later position needs a verified snapshot:
    a separate contract, not provided before WP-12.

    P-7 M2: the events are folded into a private builder by the same checks
    and ``_install`` as ``apply_event``; the immutable state is made once at the
    end and its derived indexes are checked. On any failure nothing is returned.
    """
    fold = _BuilderFold()
    tenant = None
    expected_seq = 1
    previous: Event | None = None
    previous_hash: str | None = None
    for event in events:
        if tenant is None:
            tenant = event.tenant_id
        elif event.tenant_id != tenant:
            raise StateRebuildError("events of several tenants")
        if event.tenant_seq != expected_seq:
            raise StateRebuildError("a full rebuild starts at tenant_seq 1 without gaps")
        if event.prev_hash != previous_hash:
            raise StateRebuildError("hash chain broken")
        _fold_event(fold, event)
        previous, previous_hash = event, event_hash(event)
        expected_seq += 1
    state = fold.builder.freeze()
    check_indexes(state)
    return state, previous, previous_hash
