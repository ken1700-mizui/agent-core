"""The single-agent loop for simple tasks (WP-15; A 3, E 7.1-7.3, E 29.5, E.Z15).

One call of ``AgentLoop.run`` moves a Run forward until it completes, fails, or
has to wait (approval, budget, stop, reconciliation), and then returns control
to the caller. It never spins while waiting.

On every call, before anything else (WP15-R01, R02):

- a terminal Run returns at once;
- a Run of this loop is entered by one call at a time (a reentrant or concurrent
  call is refused, ``RUN_BUSY``); after a failure this loop could not finish
  recording, it refuses to touch the Run again (``LOOP_BROKEN``);
- an Attempt whose outcome is unknown sends the Run to RECONCILIATION_REQUIRED;
  a started Attempt whose outcome is not even recorded as unknown is left alone
  (``UNRESOLVED_ATTEMPT``, WP-12): neither is ever run again;
- a stop (RUN, TENANT, GLOBAL) wins over everything that waits: an approval, a
  budget pause, a context that did not fit. Waiting Attempts are aborted before
  dispatch and the Run ends CANCELLED, unless an outcome or a cost is not known
  (then RECONCILIATION_REQUIRED, or CANCELLING with ``UNSETTLED_COSTS``);
- this loop continues only the Runs it started: a Run that already has loop
  work (Steps of a model call, tool call or verification) but no transcript in
  this process is refused (``SESSION_UNAVAILABLE``); nothing is reset.

Each iteration, in order: the limits; the context, built from the trusted
runtime assembly (WP14-R01) and verified against it (R03, R04) (if the required
items do not fit, nothing is sent: B1 4.3); the model call, its own Step and
Attempt through the whole authorization path (``AttemptPipeline``) and the
execution gate; the model's answer as data (D4), one tool request taken as a
proposal and run as a Step and Attempt of its own (approval waits for the owner,
never granted here; files only through ``execute_file``); an answer without a
tool request is a claim, not a proof: the loop verifies the done criteria itself.

Verification (WP15-R03): every criterion's file is read through the authorization
path and the broker; only if all hold is each read again (the confirmation) and
the two must agree; a committed write to a criterion's file must be what was
read (the ApplyPlan); then, at the decision, the stop, the Run grant, the
access epoch, unsettled costs and the time are checked again; only then
VERIFYING -> COMPLETED. A failed check goes back to RUNNING and, within the
repair limit, to a new model call with the failure in the context (I4).

Limits (WP15-R04): before every model call, tool call and verification read the
stop, unsettled costs, the elapsed time (a monotonic clock, highest value seen)
and the operation's own count are checked; the ledger enforces its own caps as
well. ``LoopOutcome`` keeps this process's counts apart from the ledger's.

Failures (WP15-R05): a refusal before the permit is consumed ends the Attempt
ABORTED_BEFORE_DISPATCH (its reservation released or voided); a consumed permit
whose operation did not start is reported not dispatched by the live gate; a
started operation that failed is unknown and is never run again. The original
error is kept: a failure of the clean-up is added to it as a note, never raised
in its place.

Phase 1 limits: simple tasks only (no planning call; a complex task is refused);
one tool request per turn; the transcript of a Run lives in this process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StringConstraints

from agent_core.context.engine import build_context, verify_build
from agent_core.cost.rules import required_for
from agent_core.ports.clock import Clock
from agent_core.ports.files import FileOperationPort
from agent_core.ports.ledger import LedgerPort
from agent_core.ports.model import ModelPort
from agent_core.runtime.context_assembly import (
    RuleCatalog,
    Turn,
    TurnKind,
    assemble_context_inputs,
    bind_model_descriptor,
)
from agent_core.runtime.execution_gate import SyntheticExecutionGate, SyntheticResult
from agent_core.runtime.file_execution import run_file_operation
from agent_core.runtime.pipeline import AttemptPipeline
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.runtime.verification import CriteriaCatalog, FileCriterion
from agent_core.safety.descriptor import AttemptRef, model_action, pin_of, tool_action
from agent_core.safety.workspace import file_digest
from agent_core.tool.registry import ToolRegistry
from agent_core.types.base import FrozenModel
from agent_core.types.context import ContextProfile, SyntheticTexts
from agent_core.types.cost import BudgetScopeType, CounterKind, PriceTable, ReservationState
from agent_core.types.criteria import CriterionCheck
from agent_core.types.errors import (
    AgentCoreError,
    CompletionRefusedError,
    ContextBudgetExceededError,
    ContextError,
    CostError,
    SafetyError,
    ToolError,
)
from agent_core.types.ids import ApprovalId, AttemptId, ModelRequestId, RunId, TenantId
from agent_core.types.model import (
    CostStatus,
    GenerationParams,
    ModelResponse,
    TextBlock,
    ToolUseBlock,
    Usage,
    UsageStatus,
)
from agent_core.types.model_errors import ModelNotSentError
from agent_core.types.records import (
    Attempt,
    AttemptState,
    Run,
    RunStatus,
    Step,
    StepKind,
    StepStatus,
)
from agent_core.types.safety import (
    ActionDescriptor,
    Consumer,
    ModelContent,
    ModelProviderKind,
    ModelTarget,
    StopStatus,
    ToolContent,
)
from agent_core.types.tools import ToolRegistration, canonical_json
from agent_core.types.transitions import (
    AbortReason,
    ApplyPlanStatus,
    CriterionResult,
    ReconciliationTrigger,
    ReconciliationTriggerReason,
    Termination,
    TerminationReason,
    VerificationEvidence,
)
from agent_core.types.values import Budget, Count, Currency, Digest, sha256_digest

A = AttemptState
_STARTED: Final = frozenset({A.DISPATCH_STARTED, A.OUTCOME_UNKNOWN})
_NOT_STARTED_CONSUMED: Final = frozenset({A.PERMIT_CONSUMED, A.DISPATCH_AUTHORIZED})
_BEFORE_CONSUMPTION: Final = frozenset(
    {
        A.PROPOSED,
        A.VALIDATED,
        A.CONTENT_FIXED,
        A.POLICY_ALLOWED,
        A.APPROVAL_PENDING,
        A.APPROVED,
        A.BUDGET_RESERVED,
        A.INTENT_RECORDED,
        A.PERMIT_ISSUED,
    }
)
_FINAL: Final = frozenset({A.COMPLETED, A.FAILED_DEFINITE, A.ABORTED_BEFORE_DISPATCH})
_SETTLED_RESERVATIONS: Final = frozenset(
    {
        ReservationState.USAGE_REPORTED,
        ReservationState.RECONCILED,
        ReservationState.RELEASED,
        ReservationState.VOID,
    }
)
_TERMINAL: Final = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.ABORTED_SAFETY}
)
_STOPPABLE: Final = frozenset(
    {
        RunStatus.RUNNING,
        RunStatus.WAITING_APPROVAL,
        RunStatus.PAUSED_BUDGET,
        RunStatus.CANCELLING,
    }
)
_OPEN_STEPS: Final = frozenset({StepStatus.PLANNED, StepStatus.IN_PROGRESS})
_EXPIRY_MS: Final = 60_000

ErrorName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]


class TaskProfile(FrozenModel):
    """How the owner classified a Task (Phase 1 plans only ``simple`` ones)."""

    goal_ref: Digest
    complexity: Literal["simple", "complex"]


class LoopLimits(FrozenModel):
    """Every loop limit is explicit. The design's provisional 3 repairs is a test
    value, not an owner-approved operating limit."""

    max_repairs: Count
    max_model_calls: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    max_tool_calls: Count
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    max_elapsed_ms: Annotated[int, Field(strict=True, ge=1, le=86_400_000)]


class LoopOutcome(FrozenModel):
    """``model_calls``, ``tool_calls`` (the model's tool requests that ran) and
    ``verification_reads`` count this process's work on the Run; the ``ledger_*``
    counts are the ledger's persisted totals for the Run (every tool operation,
    verification reads included). ``error`` is an exception class name, never a
    message."""

    status: RunStatus
    reason: str
    model_calls: Count
    tool_calls: Count
    verification_reads: Count
    repairs: Count
    ledger_model_calls: Count | None
    ledger_tool_calls: Count | None
    pending_action_hash: Digest | None
    error: ErrorName | None = None


@dataclass(frozen=True)
class LoopConfig:
    tenant_id: TenantId
    workspace_ref: str
    profile: ContextProfile
    params: GenerationParams
    price_table: PriceTable
    rules: RuleCatalog
    texts: SyntheticTexts
    criteria: CriteriaCatalog
    tasks: tuple[TaskProfile, ...]
    limits: LoopLimits
    cost_cap_micro: int
    user_input: str


@dataclass(frozen=True)
class LoopDeps:
    sm: RuntimeStateMachine
    ledger: LedgerPort
    gate: SyntheticExecutionGate
    model: ModelPort
    registry: ToolRegistry
    tools: tuple[ToolRegistration, ...]
    files: FileOperationPort
    clock: Clock
    # Milliseconds from a monotonic source (``time.monotonic``): the loop's elapsed
    # time. The wall clock (``clock``) is not used for it (it can go back).
    monotonic: Callable[[], int]
    new_id: Callable[[type[Any]], Any]


@dataclass
class _Pending:
    """A tool call waiting for the owner's approval."""

    attempt_id: AttemptId
    content: ToolContent
    registration: ToolRegistration
    write_content: bytes | None
    pair_id: str


@dataclass
class _Session:
    """The in-process record of one Run (the transcript and the counters)."""

    started_mono: int
    high_mono: int
    turns: list[Turn] = field(default_factory=list)
    seq: int = 0
    pairs: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    verification_reads: int = 0
    repairs: int = 0
    iterations: int = 0
    pending: _Pending | None = None

    def add(
        self,
        kind: TurnKind,
        text: str,
        ref: str,
        *,
        pair_id: str | None = None,
        latest: bool = False,
    ) -> None:
        self.seq += 1
        self.turns.append(
            Turn(kind=kind, text=text, seq=self.seq, ref=ref, pair_id=pair_id, latest=latest)
        )


class _Stop(Exception):  # noqa: N818 (control flow inside the loop only)
    def __init__(self, outcome: LoopOutcome) -> None:
        super().__init__(outcome.reason)
        self.outcome = outcome


class _Turn(StrEnum):
    TOOL = "TOOL"
    DONE = "DONE"
    REFUSED = "REFUSED"


def _not_sent() -> Usage:
    """Nothing left the process: zero tokens and a known zero cost."""
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=Budget(currency=Currency.XTU, micro_units=0),
        cost_status=CostStatus.FINAL,
        synthetic=True,
    )


def _xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def _name(exc: BaseException | None) -> str | None:
    return None if exc is None else type(exc).__name__


class AgentLoop:
    def __init__(self, deps: LoopDeps, config: LoopConfig) -> None:
        self._deps = deps
        self._config = config
        self._sessions: dict[RunId, _Session] = {}
        self._busy: set[RunId] = set()
        self._broken: set[RunId] = set()
        self._pipeline = AttemptPipeline(
            sm=deps.sm,
            ledger=deps.ledger,
            clock=deps.clock,
            pep_instance_id=deps.gate.pep_instance_id,
            new_id=deps.new_id,
        )

    # ------------------------------------------------------------ entry

    def continues(self, run_id: RunId) -> bool:
        """Whether this loop holds the Run's transcript and can go on with it (a
        caller asks this before recording an approval the loop could not use)."""
        return run_id in self._sessions and run_id not in self._broken

    def run(self, run_id: RunId, *, approval_id: ApprovalId | None = None) -> LoopOutcome:
        if run_id in self._busy:
            return self._outcome(None, run_id, "RUN_BUSY")  # reentrant: nothing is touched
        if run_id in self._broken:
            return self._outcome(None, run_id, "LOOP_BROKEN")
        self._busy.add(run_id)
        try:
            return self._run(run_id, approval_id)
        except _Stop as stop:
            return stop.outcome
        except BaseException:
            # This loop could not finish recording what happened: it never goes on
            # from here (the journal holds what was recorded; WP-12 reconciles).
            self._broken.add(run_id)
            raise
        finally:
            self._busy.discard(run_id)

    # ------------------------------------------------------------ outcomes

    def _ledger_count(self, run_id: RunId, counter: CounterKind) -> int | None:
        view = self._deps.ledger.counter(BudgetScopeType.RUN, str(run_id), counter)
        return None if view is None else view.used

    def _outcome(
        self,
        session: _Session | None,
        run_id: RunId,
        reason: str,
        pending: str | None = None,
        error: str | None = None,
    ) -> LoopOutcome:
        return LoopOutcome(
            status=self._deps.sm.state.runs[run_id].status,
            reason=reason,
            model_calls=session.model_calls if session else 0,
            tool_calls=session.tool_calls if session else 0,
            verification_reads=session.verification_reads if session else 0,
            repairs=session.repairs if session else 0,
            ledger_model_calls=self._ledger_count(run_id, CounterKind.MODEL_CALLS),
            ledger_tool_calls=self._ledger_count(run_id, CounterKind.TOOL_CALLS),
            pending_action_hash=pending,
            error=error,
        )

    def _set(self, run_id: RunId, status: RunStatus, evidence: Any = None) -> Run:
        run = self._deps.sm.state.runs[run_id]
        return self._deps.sm.set_run_status(run, status, evidence)

    def _stop(
        self,
        session: _Session | None,
        run_id: RunId,
        status: RunStatus,
        reason: str,
        evidence: Any = None,
        error: str | None = None,
    ) -> _Stop:
        if self._deps.sm.state.runs[run_id].status is not status:
            self._set(run_id, status, evidence)
        return _Stop(self._outcome(session, run_id, reason, error=error))

    def _terminate(
        self,
        session: _Session | None,
        run_id: RunId,
        status: RunStatus,
        reason: TerminationReason,
        detail: str,
        error: str | None = None,
    ) -> _Stop:
        evidence = Termination(
            kind="termination",
            reason=reason,
            evidence_ref=sha256_digest(f"{run_id}:{detail}".encode()),
        )
        return self._stop(session, run_id, status, detail, evidence, error)

    def _fail(
        self, session: _Session | None, run_id: RunId, reason: TerminationReason, detail: str
    ) -> _Stop:
        return self._terminate(session, run_id, RunStatus.FAILED, reason, detail)

    def _unsafe(
        self, session: _Session | None, run_id: RunId, detail: str, error: str | None = None
    ) -> _Stop:
        return self._terminate(
            session,
            run_id,
            RunStatus.ABORTED_SAFETY,
            TerminationReason.SAFETY_ABORT,
            detail,
            error,
        )

    # ------------------------------------------------------------ the run

    def _attempts(self, run_id: RunId) -> list[Attempt]:
        state = self._deps.sm.state
        return [
            state.attempts[a]
            for s in state.run_steps.get(run_id, ())
            for a in state.step_attempts.get(s, ())
        ]

    def _run(self, run_id: RunId, approval_id: ApprovalId | None) -> LoopOutcome:
        state = self._deps.sm.state
        run = state.runs[run_id]
        session = self._sessions.get(run_id)
        if session is not None:
            self._elapsed(session)  # every call observes the time (highest value kept)
        if run.status in _TERMINAL:
            return self._outcome(session, run_id, run.status.value)
        self._unresolved(session, run_id)
        if self._stopped(run_id) and run.status in _STOPPABLE:
            raise self._cancel(session, run_id)
        if run.status not in (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL):
            return self._outcome(session, run_id, run.status.value)
        if session is None:
            if self._attempts(run_id):
                # Loop work exists (every loop Step has an Attempt), but not its
                # transcript: never continued as if it were new.
                return self._outcome(None, run_id, "SESSION_UNAVAILABLE")
            if run.status is not RunStatus.RUNNING:
                return self._outcome(None, run_id, "SESSION_UNAVAILABLE")
            now = self._deps.monotonic()
            session = _Session(started_mono=now, high_mono=now)
            session.add(TurnKind.USER_INPUT, self._config.user_input, "task:request", latest=True)
            self._sessions[run_id] = session
        if run.status is RunStatus.WAITING_APPROVAL:
            if approval_id is None or session.pending is None:
                return self._outcome(
                    session, run_id, "WAITING_APPROVAL", self._pending_hash(session)
                )
            self._set(run_id, RunStatus.RUNNING)
            self._resume_tool(run_id, session, approval_id)
        task = state.tasks[run.task_id]
        profile = next((p for p in self._config.tasks if p.goal_ref == task.goal_ref), None)
        if profile is None or profile.complexity != "simple":
            raise self._fail(
                session, run_id, TerminationReason.PLANNING_UNSUPPORTED, "PLANNING_UNSUPPORTED"
            )
        while True:
            session.iterations += 1
            if session.iterations > self._config.limits.max_iterations:
                raise self._fail(
                    session, run_id, TerminationReason.LIMIT_EXCEEDED, "LIMIT_EXCEEDED"
                )
            kind, use, pair_id = self._model_turn(run_id, session)
            if kind is _Turn.TOOL:
                assert use is not None  # noqa: S101
                assert pair_id is not None  # noqa: S101
                self._tool_turn(run_id, session, use, pair_id)
            elif kind is _Turn.DONE:
                if self._verify(run_id, session):
                    return self._outcome(session, run_id, "VERIFIED")
                if session.repairs >= self._config.limits.max_repairs:
                    raise self._fail(
                        session, run_id, TerminationReason.VERIFICATION_FAILED, "REPAIR_LIMIT"
                    )
                session.repairs += 1  # one more repair round, within the limit

    def _pending_hash(self, session: _Session) -> str | None:
        if session.pending is None:
            return None
        return self._deps.sm.state.attempts[session.pending.attempt_id].action_hash

    # ------------------------------------------------------------ checks

    def _unresolved(self, session: _Session | None, run_id: RunId) -> None:
        """Started work is never run again. An unknown outcome sends the Run to
        RECONCILIATION_REQUIRED; a started one not recorded as unknown is left for
        WP-12 untouched."""
        attempts = self._attempts(run_id)
        status = self._deps.sm.state.runs[run_id].status
        if any(a.state is A.OUTCOME_UNKNOWN for a in attempts):
            if status is RunStatus.RECONCILIATION_REQUIRED:
                raise _Stop(self._outcome(session, run_id, "OUTCOME_UNKNOWN"))
            if status in (RunStatus.RUNNING, RunStatus.CANCELLING):
                raise self._reconcile(session, run_id)
            raise _Stop(self._outcome(session, run_id, "UNRESOLVED_ATTEMPT"))
        if any(a.state in _STARTED for a in attempts):
            raise _Stop(self._outcome(session, run_id, "UNRESOLVED_ATTEMPT"))

    def _reconcile(
        self, session: _Session | None, run_id: RunId, error: str | None = None
    ) -> _Stop:
        trigger = ReconciliationTrigger(
            kind="reconciliation_trigger",
            reason=ReconciliationTriggerReason.OUTCOME_UNKNOWN,
            evidence_ref=sha256_digest(f"{run_id}:unknown".encode()),
        )
        return self._stop(
            session, run_id, RunStatus.RECONCILIATION_REQUIRED, "OUTCOME_UNKNOWN", trigger, error
        )

    def _stopped(self, run_id: RunId) -> bool:
        stopped = {
            (e.scope_type.value, e.scope_id)
            for e in self._deps.ledger.stop_entries()
            if e.status is StopStatus.STOPPED
        }
        return bool(
            stopped
            & {("GLOBAL", "global"), ("TENANT", str(self._config.tenant_id)), ("RUN", str(run_id))}
        )

    def _unsettled(self, run_id: RunId) -> bool:
        """A cost that is not final, or a hold left, of a finished Attempt."""
        for attempt in self._attempts(run_id):
            if attempt.state not in _FINAL or attempt.reservation_id is None:
                continue
            view = self._deps.ledger.reservation(str(attempt.reservation_id))
            if view is None:
                continue
            if (
                view.state not in _SETTLED_RESERVATIONS
                or view.hold_micro != 0
                or (view.state is ReservationState.USAGE_REPORTED and not view.spent_known)
            ):
                return True
        return False

    def _cancel(self, session: _Session | None, run_id: RunId, error: str | None = None) -> _Stop:
        """CANCELLING, then CANCELLED. Every Attempt that has not started is aborted
        before dispatch and its Step cancelled. An unknown outcome is not a normal
        cancel (RECONCILIATION_REQUIRED); nor is an unsettled cost (CANCELLING)."""
        deps = self._deps
        if deps.sm.state.runs[run_id].status is not RunStatus.CANCELLING:
            self._set(run_id, RunStatus.CANCELLING)
        if session is not None:
            session.pending = None
        for attempt in self._attempts(run_id):
            if attempt.state in _BEFORE_CONSUMPTION:
                self._pipeline.abort(attempt, AbortReason.STOPPED)
            elif attempt.state in _NOT_STARTED_CONSUMED:
                deps.gate.abort_not_dispatched(attempt, AbortReason.STOPPED)
        state = deps.sm.state
        for step_id in state.run_steps.get(run_id, ()):
            step = state.steps[step_id]
            attempts = [state.attempts[a] for a in state.step_attempts.get(step_id, ())]
            if step.status in _OPEN_STEPS and all(a.state in _FINAL for a in attempts):
                deps.sm.set_step_status(step, StepStatus.CANCELLED)
        if any(a.state is A.OUTCOME_UNKNOWN for a in self._attempts(run_id)):
            return self._reconcile(session, run_id, error)
        if any(a.state in _STARTED for a in self._attempts(run_id)):
            return _Stop(self._outcome(session, run_id, "UNRESOLVED_ATTEMPT", error=error))
        if self._unsettled(run_id):
            return _Stop(self._outcome(session, run_id, "UNSETTLED_COSTS", error=error))
        return self._stop(session, run_id, RunStatus.CANCELLED, "STOPPED", error=error)

    def _elapsed(self, session: _Session) -> int:
        """Monotonic time since the Run's start in this process; a source that goes
        back never gives time back (the highest value seen is kept)."""
        session.high_mono = max(session.high_mono, self._deps.monotonic())
        return session.high_mono - session.started_mono

    def _guard(
        self, run_id: RunId, session: _Session, op: Literal["model", "tool", "read"]
    ) -> None:
        """Before every operation: the stop, unsettled costs, the time, the count."""
        if self._stopped(run_id):
            raise self._cancel(session, run_id)
        if self._unsettled(run_id):
            raise self._stop(session, run_id, RunStatus.PAUSED_BUDGET, "UNSETTLED_COSTS")
        if self._over(session, op):
            raise self._fail(session, run_id, TerminationReason.LIMIT_EXCEEDED, "LIMIT_EXCEEDED")

    def _over(self, session: _Session, op: Literal["model", "tool", "read"]) -> bool:
        limits = self._config.limits
        over = self._elapsed(session) > limits.max_elapsed_ms
        if op == "model":
            over = over or session.model_calls >= limits.max_model_calls
        elif op == "tool":
            over = over or session.tool_calls >= limits.max_tool_calls
        return over

    def _new_step(self, run_id: RunId, kind: StepKind) -> Step:
        step = self._deps.sm.create_step(self._deps.sm.state.runs[run_id], kind)
        return self._deps.sm.set_step_status(step, StepStatus.IN_PROGRESS)

    def _end_step(self, step: Step, attempt: Attempt) -> None:
        current = self._deps.sm.state.attempts[attempt.attempt_id]
        status = {
            A.COMPLETED: StepStatus.COMMITTED,
            A.FAILED_DEFINITE: StepStatus.FAILED,
            A.ABORTED_BEFORE_DISPATCH: StepStatus.FAILED,
        }.get(current.state, StepStatus.RECONCILIATION_REQUIRED)
        self._deps.sm.set_step_status(self._deps.sm.state.steps[step.step_id], status)

    def _ref(self, run: Run, step: Step, attempt: Attempt) -> AttemptRef:
        return AttemptRef(
            tenant_id=self._config.tenant_id,
            task_id=run.task_id,
            run_id=run.run_id,
            step_id=step.step_id,
            attempt_id=attempt.attempt_id,
        )

    # ------------------------------------------------------------ failures (R05)

    def _after_failure(
        self,
        run_id: RunId,
        session: _Session,
        step: Step,
        attempt_id: AttemptId,
        exc: BaseException,
    ) -> _Stop:
        """The state after an operation failed. Not started (refused before the
        start): aborted and reported not dispatched. Started: unknown, never run
        again. The original error is never replaced by a clean-up failure."""
        deps = self._deps
        try:
            current = deps.sm.state.attempts[attempt_id]
            if current.state is A.OUTCOME_UNKNOWN:
                self._end_step(step, current)
                stop = self._reconcile(session, run_id, _name(exc))
            elif current.state in _NOT_STARTED_CONSUMED:
                aborted = deps.gate.abort_not_dispatched(current, AbortReason.REVOKED)
                self._end_step(step, aborted)
                stop = self._after_abort(run_id, session, "NOT_DISPATCHED", _name(exc))
            elif current.state in _BEFORE_CONSUMPTION:
                aborted = self._pipeline.abort(current, AbortReason.REVOKED)
                self._end_step(step, aborted)
                stop = self._after_abort(run_id, session, "NOT_DISPATCHED", _name(exc))
            else:
                raise exc  # started and not recorded as unknown: nothing safe to add
        except BaseException as cleanup:  # noqa: BLE001 (the original is re-raised)
            if cleanup is not exc:
                exc.add_note(f"agent loop clean-up failed: {type(cleanup).__name__}")
            raise exc from None
        if not isinstance(exc, Exception):
            raise exc  # an interrupt: recorded safely, then passed on
        return stop

    def _after_abort(
        self, run_id: RunId, session: _Session, detail: str, error: str | None
    ) -> _Stop:
        if self._stopped(run_id):
            return self._cancel(session, run_id, error)
        return self._unsafe(session, run_id, detail, error)

    # ------------------------------------------------------------ authorization

    def _decide(
        self,
        attempt: Attempt,
        descriptor: ActionDescriptor,
        content: Any,
        registration: ToolRegistration | None,
    ) -> Attempt:
        """Decision (approval handled by the caller)."""
        policy = self._deps.ledger.policy(str(self._config.tenant_id))
        if policy is None:
            raise SafetyError("POLICY_MISSING")
        attempt = self._pipeline.fix(attempt, descriptor)
        return self._pipeline.decide(attempt, descriptor, content, registration, policy)

    def _reserve_and_permit(
        self, run_id: RunId, session: _Session, attempt: Attempt, consumer: Consumer
    ) -> Attempt:
        deps = self._deps
        step = deps.sm.state.steps[attempt.step_id]
        descriptor = deps.ledger.recorded_descriptor(str(attempt.attempt_id))
        assert descriptor is not None  # noqa: S101 (recorded by the decision)
        required = required_for(descriptor.cost_basis, self._config.price_table)
        try:
            attempt = self._pipeline.reserve(
                attempt, amount=required, cost_cap=descriptor.cost_cap.micro_units
            )
        except CostError as exc:
            # The budget does not allow it: nothing runs; the Run waits for the owner.
            current = deps.sm.state.attempts[attempt.attempt_id]
            self._end_step(step, self._pipeline.abort(current, AbortReason.CANCELLED))
            if self._stopped(run_id):
                raise self._cancel(session, run_id, _name(exc)) from None
            raise self._stop(
                session, run_id, RunStatus.PAUSED_BUDGET, "BUDGET", error=_name(exc)
            ) from None
        try:
            return self._pipeline.authorize(attempt, consumer)
        except SafetyError as exc:
            # The permit or its consumption was refused (a stop, a permit that does
            # not check): nothing ran.
            current = deps.sm.state.attempts[attempt.attempt_id]
            if current.state in _BEFORE_CONSUMPTION:
                self._end_step(step, self._pipeline.abort(current, AbortReason.REVOKED))
                if self._stopped(run_id):
                    raise self._cancel(session, run_id, _name(exc)) from None
                raise self._unsafe(session, run_id, "PERMIT_REFUSED", _name(exc)) from None
            raise self._after_failure(run_id, session, step, attempt.attempt_id, exc) from None

    # ------------------------------------------------------------ the model call

    def _model_turn(
        self, run_id: RunId, session: _Session
    ) -> tuple[_Turn, ToolUseBlock | None, str | None]:
        deps, config = self._deps, self._config
        self._guard(run_id, session, "model")
        run = deps.sm.state.runs[run_id]
        try:
            inputs = assemble_context_inputs(
                view=deps.sm.state,
                ledger=deps.ledger,
                tenant_id=config.tenant_id,
                task_id=run.task_id,
                run_id=run_id,
                tools=deps.tools,
                rules=config.rules,
                texts=config.texts,
                turns=tuple(session.turns),
            )
        except ContextError as exc:
            raise self._unsafe(session, run_id, "CONTEXT_INVALID", _name(exc)) from None
        step = self._new_step(run_id, StepKind.MODEL_CALL)
        attempt = self._pipeline.start(step, sha256_digest(b"model-request-v1"))
        try:
            result = build_context(
                inputs=inputs,
                profile=config.profile,
                params=config.params,
                attempt_id=attempt.attempt_id,
                model_request_id=deps.new_id(ModelRequestId),
            )
            verify_build(result, inputs=inputs, profile=config.profile, params=config.params)
        except ContextBudgetExceededError:
            # B1 4.3: nothing is sent; the owner decides (no replanning in Phase 1).
            self._end_step(step, self._pipeline.abort(attempt, AbortReason.CANCELLED))
            raise self._stop(
                session, run_id, RunStatus.WAITING_APPROVAL, "CONTEXT_BUDGET_EXCEEDED"
            ) from None
        except ContextError as exc:
            # The context does not verify: nothing is sent, the Run stops for safety.
            self._end_step(step, self._pipeline.abort(attempt, AbortReason.POLICY_DENIED))
            raise self._unsafe(session, run_id, "CONTEXT_INVALID", _name(exc)) from None
        policy = deps.ledger.policy(str(config.tenant_id))
        assert policy is not None  # noqa: S101 (checked by the assembly)
        content = ModelContent(
            kind="model",
            request_record=result.request_record_blob.decode("utf-8"),
            rendered_input=result.rendered_blob.decode("utf-8"),
        )
        descriptor = model_action(
            model=ModelTarget(profile=result.request.profile, provider=ModelProviderKind.FAKE),
            request_record=result.request_record_blob,
            rendered_input=result.rendered_blob,
            price_table_id=policy.price_table_id,
            price_table_version=policy.price_table_version,
            attempt=self._ref(run, step, attempt),
            cost_cap=_xtu(config.cost_cap_micro),
            reservation_id=self._pipeline.new_reservation_id(),
            policy_version=policy.policy_version,
            access_epoch=deps.ledger.access_epoch(str(config.tenant_id)),
            expires_at_ms=deps.clock.now_ms() + _EXPIRY_MS,
        )
        try:
            bind_model_descriptor(result, descriptor)
        except ContextError as exc:
            self._end_step(step, self._pipeline.abort(attempt, AbortReason.POLICY_DENIED))
            raise self._unsafe(session, run_id, "CONTEXT_INVALID", _name(exc)) from None
        try:
            attempt = self._decide(attempt, descriptor, content, None)
            if attempt.approval_required:
                raise SafetyError("MODEL_CALL_NEEDS_APPROVAL")  # not supported in Phase 1
        except SafetyError as exc:
            current = deps.sm.state.attempts[attempt.attempt_id]
            self._end_step(step, self._pipeline.abort(current, AbortReason.POLICY_DENIED))
            raise self._terminate(
                session,
                run_id,
                RunStatus.FAILED,
                TerminationReason.SAFETY_ABORT,
                "MODEL_CALL_DENIED",
                _name(exc),
            ) from None
        attempt = self._reserve_and_permit(run_id, session, attempt, Consumer.BROKER)
        responses: list[ModelResponse] = []

        def call(authorization: Any) -> SyntheticResult:
            # The request sent is exactly the one the permit was issued for.
            if authorization.descriptor.request_hash != result.request.request_hash:
                return SyntheticResult(outcome="FAILED_DEFINITE", usage=_not_sent(), final=True)
            try:
                response = deps.model.complete(result.request)
            except ModelNotSentError as exc:
                return SyntheticResult(outcome="FAILED_DEFINITE", usage=exc.usage, final=True)
            # Any other failure leaves the outcome unknown (the gate keeps the hold).
            if (
                type(response) is not ModelResponse
                or response.model_request_id != result.request.model_request_id
            ):
                raise AgentCoreError("MODEL_RESPONSE_INVALID")
            responses.append(response)
            return SyntheticResult(
                outcome="COMPLETED", usage=response.usage, final=response.final_usage
            )

        session.model_calls += 1
        try:
            done = deps.gate.execute(attempt, content=content, registration=None, run=call)
        except BaseException as exc:
            raise self._after_failure(run_id, session, step, attempt.attempt_id, exc) from exc
        self._end_step(step, done)
        if done.state is not A.COMPLETED or not responses:
            session.add(
                TurnKind.OBSERVATION,
                "The model call did not complete.",
                f"attempt:{done.attempt_id}",
            )
            return _Turn.REFUSED, None, None
        response = responses[0]
        text = "\n".join(b.text for b in response.content if isinstance(b, TextBlock))
        uses = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if text:
            session.add(TurnKind.MODEL_RESPONSE, text, f"attempt:{done.attempt_id}")
        if not uses:
            return _Turn.DONE, None, None
        if len(uses) > 1:
            session.add(
                TurnKind.OBSERVATION,
                "Refused: only one tool request per turn is supported.",
                f"attempt:{done.attempt_id}",
            )
            return _Turn.REFUSED, None, None
        use = uses[0]
        session.pairs += 1
        pair_id = f"p{session.pairs}"
        session.add(
            TurnKind.TOOL_CALL,
            f"tool_use {use.name} {use.arguments}",
            f"attempt:{done.attempt_id}",
            pair_id=pair_id,
        )
        return _Turn.TOOL, use, pair_id

    # ------------------------------------------------------------ the tool call

    def _registration(self, run_id: RunId, name: str) -> ToolRegistration | None:
        grant = self._deps.ledger.run_grant(str(run_id))
        policy = self._deps.ledger.policy(str(self._config.tenant_id))
        if grant is None or policy is None:
            return None
        allowed = set(grant.tools) & set(policy.tools)
        found = [r for r in self._deps.tools if r.manifest.tool_id == name and pin_of(r) in allowed]
        return found[0] if len(found) == 1 else None

    def _refuse_tool(self, session: _Session, pair_id: str, reason: str, ref: str) -> None:
        session.add(TurnKind.TOOL_RESULT, f"Refused: {reason}.", ref, pair_id=pair_id)

    def _tool_turn(self, run_id: RunId, session: _Session, use: ToolUseBlock, pair_id: str) -> None:
        deps, config = self._deps, self._config
        self._guard(run_id, session, "tool")
        registration = self._registration(run_id, use.name)
        if registration is None:
            self._refuse_tool(session, pair_id, "TOOL_NOT_AVAILABLE", "runtime:tool")
            return
        run = deps.sm.state.runs[run_id]
        step = self._new_step(run_id, StepKind.TOOL_CALL)
        attempt = self._pipeline.start(step, registration.manifest.manifest_hash)
        try:
            proposal = deps.registry.propose(
                tool_id=use.name,
                version=registration.manifest.version,
                arguments=use.arguments,
                attempt_id=attempt.attempt_id,
            )
            policy = deps.ledger.policy(str(config.tenant_id))
            if policy is None:
                raise SafetyError("POLICY_MISSING")
            descriptor = tool_action(
                registration=registration,
                canonical_input=proposal.canonical_input,
                proposal=proposal,
                attempt=self._ref(run, step, attempt),
                workspace_ref=config.workspace_ref,
                cost_cap=_xtu(config.cost_cap_micro),
                reservation_id=self._pipeline.new_reservation_id(),
                policy_version=policy.policy_version,
                access_epoch=deps.ledger.access_epoch(str(config.tenant_id)),
                expires_at_ms=deps.clock.now_ms() + _EXPIRY_MS,
            )
            content = ToolContent(kind="tool", canonical_input=proposal.canonical_input)
            attempt = self._decide(attempt, descriptor, content, registration)
        except (ToolError, SafetyError) as exc:
            current = deps.sm.state.attempts[attempt.attempt_id]
            self._end_step(step, self._pipeline.abort(current, AbortReason.POLICY_DENIED))
            reasons = getattr(exc, "reasons", ()) or (type(exc).__name__,)
            self._refuse_tool(
                session, pair_id, "/".join(str(r) for r in reasons), f"attempt:{attempt.attempt_id}"
            )
            return
        write = None
        if descriptor.apply_change is not None:
            write = str(json.loads(proposal.canonical_input)["content"]).encode("utf-8")
        pending = _Pending(attempt.attempt_id, content, registration, write, pair_id)
        if attempt.approval_required:
            # The owner approves this exact action, or nothing happens. Control returns.
            self._pipeline.await_approval(attempt)
            session.pending = pending
            self._set(run_id, RunStatus.WAITING_APPROVAL)
            raise _Stop(
                self._outcome(session, run_id, "WAITING_APPROVAL", self._pending_hash(session))
            )
        self._execute_tool(run_id, session, step, pending)

    def _resume_tool(self, run_id: RunId, session: _Session, approval_id: ApprovalId) -> None:
        pending = session.pending
        assert pending is not None  # noqa: S101
        session.pending = None
        deps = self._deps
        attempt = deps.sm.state.attempts[pending.attempt_id]
        step = deps.sm.state.steps[attempt.step_id]
        # The stop, the costs and the limits hold before the approved action runs,
        # too (WP15-R04): the waiting action is ended first, then the Run.
        if self._stopped(run_id):
            raise self._cancel(session, run_id)
        unsettled = self._unsettled(run_id)
        if unsettled or self._over(session, "tool"):
            self._end_step(step, self._pipeline.abort(attempt, AbortReason.CANCELLED))
            if unsettled:
                raise self._stop(session, run_id, RunStatus.PAUSED_BUDGET, "UNSETTLED_COSTS")
            raise self._fail(session, run_id, TerminationReason.LIMIT_EXCEEDED, "LIMIT_EXCEEDED")
        try:
            self._pipeline.approve(attempt, approval_id)
        except (SafetyError, CostError) as exc:
            current = deps.sm.state.attempts[attempt.attempt_id]
            self._end_step(step, self._pipeline.abort(current, AbortReason.APPROVAL_DENIED))
            reasons = getattr(exc, "reasons", ()) or (type(exc).__name__,)
            self._refuse_tool(
                session,
                pending.pair_id,
                "/".join(str(r) for r in reasons),
                f"attempt:{attempt.attempt_id}",
            )
            return
        self._execute_tool(run_id, session, step, pending)

    def _execute_tool(
        self, run_id: RunId, session: _Session, step: Step, pending: _Pending
    ) -> None:
        deps = self._deps
        attempt = deps.sm.state.attempts[pending.attempt_id]
        attempt = self._reserve_and_permit(run_id, session, attempt, Consumer.BROKER)
        session.tool_calls += 1
        try:
            result = run_file_operation(
                gate=deps.gate,
                ledger=deps.ledger,
                files=deps.files,
                attempt=attempt,
                content=pending.content,
                registration=pending.registration,
                write_content=pending.write_content,
                registry=deps.registry,
            )
        except BaseException as exc:
            raise self._after_failure(run_id, session, step, attempt.attempt_id, exc) from exc
        done = result.attempt
        self._end_step(step, done)
        ref = f"attempt:{done.attempt_id}"
        if done.state is A.COMPLETED and result.output is not None:
            session.add(
                TurnKind.TOOL_RESULT, result.output.canonical_output, ref, pair_id=pending.pair_id
            )
        elif done.state is A.COMPLETED:
            descriptor = deps.ledger.recorded_descriptor(str(done.attempt_id))
            change = descriptor.apply_change if descriptor is not None else None
            summary = "committed"
            if change is not None:
                size, digest = change.content_size, change.content_digest
                summary = f"committed {change.path} ({size} bytes, {digest})"
            session.add(TurnKind.TOOL_RESULT, f"write_file {summary}", ref, pair_id=pending.pair_id)
        else:
            session.add(
                TurnKind.TOOL_RESULT,
                f"The tool call ended {done.state.value}.",
                ref,
                pair_id=pending.pair_id,
            )

    # ------------------------------------------------------------ verification

    def _read(self, run_id: RunId, session: _Session, path: str) -> bytes | None:
        """One VERIFICATION read through the authorization path and the broker. A
        ledger tool call; counted apart from the model's tool requests."""
        deps, config = self._deps, self._config
        self._guard(run_id, session, "read")
        registration = self._registration(run_id, "read_file")
        if registration is None:
            return None
        run = deps.sm.state.runs[run_id]
        step = self._new_step(run_id, StepKind.VERIFICATION)
        attempt = self._pipeline.start(step, registration.manifest.manifest_hash)
        canonical = canonical_json({"path": path}).decode()
        try:
            policy = deps.ledger.policy(str(config.tenant_id))
            if policy is None:
                raise SafetyError("POLICY_MISSING")
            descriptor = tool_action(
                registration=registration,
                canonical_input=canonical,
                proposal=None,
                attempt=self._ref(run, step, attempt),
                workspace_ref=config.workspace_ref,
                cost_cap=_xtu(config.cost_cap_micro),
                reservation_id=self._pipeline.new_reservation_id(),
                policy_version=policy.policy_version,
                access_epoch=deps.ledger.access_epoch(str(config.tenant_id)),
                expires_at_ms=deps.clock.now_ms() + _EXPIRY_MS,
            )
            content = ToolContent(kind="tool", canonical_input=canonical)
            attempt = self._decide(attempt, descriptor, content, registration)
            if attempt.approval_required:
                raise SafetyError("VERIFICATION_NEEDS_APPROVAL")
        except SafetyError:
            current = deps.sm.state.attempts[attempt.attempt_id]
            self._end_step(step, self._pipeline.abort(current, AbortReason.POLICY_DENIED))
            return None
        attempt = self._reserve_and_permit(run_id, session, attempt, Consumer.BROKER)
        session.verification_reads += 1
        try:
            result = run_file_operation(
                gate=deps.gate,
                ledger=deps.ledger,
                files=deps.files,
                attempt=attempt,
                content=content,
                registration=registration,
                write_content=None,
                registry=deps.registry,
            )
        except BaseException as exc:
            raise self._after_failure(run_id, session, step, attempt.attempt_id, exc) from exc
        self._end_step(step, result.attempt)
        return result.data if result.attempt.state is A.COMPLETED else None

    def _read_all(
        self, run_id: RunId, session: _Session, specs: list[FileCriterion | None]
    ) -> tuple[list[str | None], list[AttemptId | None]]:
        """The digest of each criterion's file, and the read Attempt of each."""
        found: list[str | None] = []
        reads: list[AttemptId | None] = []
        for spec in specs:
            before = {a.attempt_id for a in self._attempts(run_id)}
            data = None if spec is None else self._read(run_id, session, spec.path)
            found.append(None if data is None else file_digest(data))
            new = [a.attempt_id for a in self._attempts(run_id) if a.attempt_id not in before]
            reads.append(new[-1] if data is not None and new else None)
        return found, reads

    def _committed_writes(self, run_id: RunId) -> dict[str, str | None]:
        """The content digest of the latest committed write of each path, by the
        journal's order, never by the order of the decisions; None where the
        journal does not prove which write was last (QR-04). Advisory: the
        completion transaction checks the order again against the reads."""
        return self._deps.ledger.committed_writes(str(run_id))

    def _verification_evidence(
        self,
        refs: list[str],
        specs: list[FileCriterion | None],
        found: list[str | None],
        passed: list[bool],
        reads: tuple[AttemptId | None, ...],
        run_id: RunId,
    ) -> VerificationEvidence:
        observed = [
            f"{spec.path if spec else ref}={digest or 'missing'}"
            for ref, spec, digest in zip(refs, specs, found, strict=True)
        ]
        expected = [
            f"{spec.path if spec else ref}={spec.expected_digest if spec else 'unknown'}"
            for ref, spec in zip(refs, specs, strict=True)
        ]
        wrote = bool(self._committed_writes(run_id))
        return VerificationEvidence(
            kind="verification",
            criteria=tuple(
                CriterionResult(criterion_ref=ref, passed=ok)
                for ref, ok in zip(refs, passed, strict=True)
            ),
            artifact_hash=sha256_digest("|".join(observed).encode()),
            expected_artifact_hash=sha256_digest("|".join(expected).encode()),
            apply_plan=ApplyPlanStatus.COMMITTED if wrote else ApplyPlanStatus.NOT_REQUIRED,
            passed=bool(refs) and all(passed),
            checks=tuple(
                CriterionCheck(
                    criterion_ref=ref,
                    path=spec.path,
                    expected_digest=spec.expected_digest,
                    observed_digest=digest,
                    read_attempt_id=read,
                )
                for ref, spec, digest, read in zip(refs, specs, found, reads, strict=True)
                if spec is not None
            )
            if reads
            else (),
        )

    def _verify(self, run_id: RunId, session: _Session) -> bool:
        """The done criteria checked on the actual files (never the model's word),
        then confirmed and bound to the decision (WP15-R03, R06)."""
        deps = self._deps
        task = deps.sm.state.tasks[deps.sm.state.runs[run_id].task_id]
        refs = list(task.done_criteria)
        specs = [self._config.criteria.spec_for(ref) for ref in refs]
        first, _ = self._read_all(run_id, session, specs)
        passed = [
            spec is not None and found == spec.expected_digest
            for spec, found in zip(specs, first, strict=True)
        ]
        if not (all(passed) and refs):
            self._guard(run_id, session, "read")
            evidence = self._verification_evidence(refs, specs, first, passed, (), run_id)
            self._set(run_id, RunStatus.VERIFYING)
            self._set(run_id, RunStatus.RUNNING, evidence)
            failures = [
                f"{spec.path if spec else ref}: expected "
                f"{spec.expected_digest if spec else 'unknown'}, found {found or 'missing'}"
                for ref, spec, found, ok in zip(refs, specs, first, passed, strict=True)
                if not ok
            ]
            session.add(
                TurnKind.OBSERVATION,
                "Verification failed: " + "; ".join(failures),
                f"verification:{len(session.turns)}",
            )
            return False
        # WP15-R06: from the confirmation reads to the completion record, this
        # Runtime's gate refuses every change (cooperating writers are held off).
        with deps.gate.verification_hold():
            self._confirm_and_complete(run_id, session, refs, specs, first, passed)
        return True

    def _confirm_and_complete(
        self,
        run_id: RunId,
        session: _Session,
        refs: list[str],
        specs: list[FileCriterion | None],
        first: list[str | None],
        passed: list[bool],
    ) -> None:
        deps = self._deps
        tenant = str(self._config.tenant_id)
        epoch = deps.ledger.access_epoch(tenant)
        # The confirmation: every file again, after all were read once. A change
        # while the files were read (by any writer) shows as a difference.
        second, reads = self._read_all(run_id, session, specs)
        if second != first:
            raise self._fail(
                session, run_id, TerminationReason.VERIFICATION_FAILED, "ARTIFACT_CHANGED"
            )
        writes = self._committed_writes(run_id)
        for spec, found in zip(specs, second, strict=True):
            assert spec is not None  # noqa: S101 (passed)
            if spec.path in writes and writes[spec.path] != found:
                # The ApplyPlan committed something else than what is there now.
                raise self._fail(
                    session, run_id, TerminationReason.VERIFICATION_FAILED, "APPLY_PLAN_MISMATCH"
                )
        # The decision: the stop, the costs, the time, the grant, the access epoch.
        self._guard(run_id, session, "read")
        if deps.ledger.run_grant(str(run_id)) is None or deps.ledger.policy(tenant) is None:
            raise self._unsafe(session, run_id, "GRANT_MISSING")
        if deps.ledger.access_epoch(tenant) != epoch:
            raise self._unsafe(session, run_id, "ACCESS_CHANGED")
        evidence = self._verification_evidence(refs, specs, second, passed, tuple(reads), run_id)
        self._set(run_id, RunStatus.VERIFYING)
        try:
            # The journal's completion transaction checks the reads, the policy, the
            # grant, the access epoch, every cost and the stops again (WP15-R06).
            self._set(run_id, RunStatus.COMPLETED, evidence)
        except (CompletionRefusedError, CostError) as exc:
            # Refused inside the transaction (a cost not settled is a CostError).
            raise self._unsafe(session, run_id, "COMPLETION_REFUSED", _name(exc)) from None
