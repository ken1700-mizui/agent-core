"""The single-agent loop for simple tasks (WP-15; A 3, E 7.1-7.3, E 29.5, E.Z15).

One call of ``AgentLoop.run`` moves a Run forward until it completes, fails, or
has to wait (approval, budget, stop, reconciliation), and then returns control
to the caller. It never spins while waiting.

Each iteration, in order:

1. the Run's state: terminal or waiting states return at once; an unresolved
   (started or unknown) Attempt sends the Run to RECONCILIATION_REQUIRED and is
   never run again; a stop (also one that arrives while an action waits for its
   approval) sends it through CANCELLING to CANCELLED; the loop limits are
   checked (the tool-call limit before each tool call);
2. the context: built from the trusted runtime assembly (WP14-R01) and verified
   against it (R03, R04); if the required items do not fit, nothing is sent
   (B1 4.3: the Run waits for the owner);
3. the model call: its own Step and Attempt through the whole authorization path
   (``AttemptPipeline``) and the execution gate; the request sent is exactly the
   verified one (its request hash is the descriptor's);
4. the model's answer is data (D4). One tool request is taken as a proposal:
   the tool must be registered and pinned by the Run grant and the policy, its
   arguments valid, its targets inside the managed tree; it then becomes a new
   Step and Attempt of its own, through the same path (approval waits for the
   owner, never granted here) and the file broker (``execute_file`` only);
5. an answer without a tool request is a claim, not a proof: the loop verifies
   the done criteria itself (VERIFICATION Steps reading each file through the
   same authorization path), moves the Run to VERIFYING, and completes it only if
   every criterion holds and no reservation is left unsettled (an unsettled cost
   pauses the Run, PAUSED_BUDGET, and nothing more runs). A failed
   verification goes back to RUNNING and, within the repair limit, to a new model
   call with the failure in the context (I4).

A refusal before the permit is consumed (policy, approval, budget, a permit that
does not check) ends the Attempt ABORTED_BEFORE_DISPATCH and releases or voids
its reservation; nothing was sent.

Phase 1 limits: simple tasks only (no planning call; a complex task is refused);
one tool request per turn; the transcript of a Run lives in this process (a new
process does not continue a Run; unresolved Attempts wait for reconciliation,
WP-12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import Field

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
from agent_core.runtime.verification import CriteriaCatalog
from agent_core.safety.descriptor import AttemptRef, model_action, pin_of, tool_action
from agent_core.safety.workspace import file_digest
from agent_core.tool.registry import ToolRegistry
from agent_core.types.base import FrozenModel
from agent_core.types.context import ContextProfile, SyntheticTexts
from agent_core.types.cost import PriceTable, ReservationState
from agent_core.types.errors import (
    AgentCoreError,
    ContextBudgetExceededError,
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
_UNRESOLVED: Final = frozenset({A.DISPATCH_STARTED, A.OUTCOME_UNKNOWN, A.DISPATCH_AUTHORIZED})
_SETTLED_RESERVATIONS: Final = frozenset(
    {
        ReservationState.USAGE_REPORTED,
        ReservationState.RECONCILED,
        ReservationState.RELEASED,
        ReservationState.VOID,
    }
)
_WAITING: Final = frozenset(
    {
        RunStatus.WAITING_APPROVAL,
        RunStatus.PAUSED_BUDGET,
        RunStatus.CANCELLING,
        RunStatus.RECONCILIATION_REQUIRED,
    }
)
_TERMINAL: Final = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.ABORTED_SAFETY}
)
_EXPIRY_MS: Final = 60_000


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
    status: RunStatus
    reason: str
    model_calls: Count
    tool_calls: Count
    repairs: Count
    pending_action_hash: Digest | None


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

    started_ms: int
    turns: list[Turn] = field(default_factory=list)
    seq: int = 0
    pairs: int = 0
    model_calls: int = 0
    tool_calls: int = 0
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


class AgentLoop:
    def __init__(self, deps: LoopDeps, config: LoopConfig) -> None:
        self._deps = deps
        self._config = config
        self._sessions: dict[RunId, _Session] = {}
        self._pipeline = AttemptPipeline(
            sm=deps.sm,
            ledger=deps.ledger,
            clock=deps.clock,
            pep_instance_id=deps.gate.pep_instance_id,
            new_id=deps.new_id,
        )

    # ------------------------------------------------------------ entry

    def run(self, run_id: RunId, *, approval_id: ApprovalId | None = None) -> LoopOutcome:
        session = self._sessions.get(run_id)
        if session is None:
            session = _Session(started_ms=self._deps.clock.now_ms())
            session.add(TurnKind.USER_INPUT, self._config.user_input, "task:request", latest=True)
            self._sessions[run_id] = session
        try:
            return self._run(run_id, session, approval_id)
        except _Stop as stop:
            return stop.outcome

    def _outcome(
        self, session: _Session, run_id: RunId, reason: str, pending: str | None = None
    ) -> LoopOutcome:
        return LoopOutcome(
            status=self._deps.sm.state.runs[run_id].status,
            reason=reason,
            model_calls=session.model_calls,
            tool_calls=session.tool_calls,
            repairs=session.repairs,
            pending_action_hash=pending,
        )

    def _set(self, run_id: RunId, status: RunStatus, evidence: Any = None) -> Run:
        run = self._deps.sm.state.runs[run_id]
        return self._deps.sm.set_run_status(run, status, evidence)

    def _stop(
        self, session: _Session, run_id: RunId, status: RunStatus, reason: str, evidence: Any = None
    ) -> _Stop:
        if self._deps.sm.state.runs[run_id].status is not status:
            self._set(run_id, status, evidence)
        return _Stop(self._outcome(session, run_id, reason))

    def _fail(
        self, session: _Session, run_id: RunId, reason: TerminationReason, detail: str
    ) -> _Stop:
        evidence = Termination(
            kind="termination",
            reason=reason,
            evidence_ref=sha256_digest(f"{run_id}:{detail}".encode()),
        )
        return self._stop(session, run_id, RunStatus.FAILED, detail, evidence)

    def _run(self, run_id: RunId, session: _Session, approval_id: ApprovalId | None) -> LoopOutcome:
        state = self._deps.sm.state
        run = state.runs[run_id]
        if run.status in _TERMINAL:
            return self._outcome(session, run_id, run.status.value)
        if run.status is RunStatus.WAITING_APPROVAL:
            if approval_id is None or session.pending is None:
                return self._outcome(
                    session, run_id, "WAITING_APPROVAL", self._pending_hash(session)
                )
            if self._stopped(run_id):
                # A stop wins over the approval: the waiting action never runs.
                raise self._cancel(session, run_id)
            self._set(run_id, RunStatus.RUNNING)
            self._resume_tool(run_id, session, approval_id)
        elif run.status is not RunStatus.RUNNING:
            return self._outcome(session, run_id, run.status.value)
        task = state.tasks[run.task_id]
        profile = next((p for p in self._config.tasks if p.goal_ref == task.goal_ref), None)
        if profile is None or profile.complexity != "simple":
            raise self._fail(
                session, run_id, TerminationReason.PLANNING_UNSUPPORTED, "PLANNING_UNSUPPORTED"
            )
        while True:
            self._check(run_id, session)
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

    def _check(self, run_id: RunId, session: _Session) -> None:
        state = self._deps.sm.state
        session.iterations += 1
        limits = self._config.limits
        attempts = [
            a
            for s in state.run_steps.get(run_id, ())
            for a in (state.attempts[i] for i in state.step_attempts.get(s, ()))
        ]
        if any(a.state in _UNRESOLVED for a in attempts):
            # Never run again: whether it happened is not known (WP-12 reconciles).
            trigger = ReconciliationTrigger(
                kind="reconciliation_trigger",
                reason=ReconciliationTriggerReason.OUTCOME_UNKNOWN,
                evidence_ref=sha256_digest(f"{run_id}:unknown".encode()),
            )
            raise self._stop(
                session, run_id, RunStatus.RECONCILIATION_REQUIRED, "OUTCOME_UNKNOWN", trigger
            )
        if self._stopped(run_id):
            raise self._cancel(session, run_id)
        elapsed = self._deps.clock.now_ms() - session.started_ms
        if (
            session.iterations > limits.max_iterations
            or session.model_calls >= limits.max_model_calls
            or session.tool_calls > limits.max_tool_calls
            or elapsed > limits.max_elapsed_ms
        ):
            raise self._fail(session, run_id, TerminationReason.LIMIT_EXCEEDED, "LIMIT_EXCEEDED")

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

    def _cancel(self, session: _Session, run_id: RunId) -> _Stop:
        """CANCELLING, then CANCELLED: the loop is synchronous, so nothing of this Run
        is in flight here (an unresolved Attempt was sent to reconciliation first).
        The runtime refuses CANCELLED if anything is still unsettled."""
        self._set(run_id, RunStatus.CANCELLING)
        pending, session.pending = session.pending, None
        if pending is not None:
            attempt = self._deps.sm.state.attempts[pending.attempt_id]
            self._pipeline.abort(attempt, AbortReason.STOPPED)
            step = self._deps.sm.state.steps[attempt.step_id]
            self._deps.sm.set_step_status(step, StepStatus.CANCELLED)
        return self._stop(session, run_id, RunStatus.CANCELLED, "STOPPED")

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

    def _authorize(
        self,
        run_id: RunId,
        session: _Session,
        attempt: Attempt,
        descriptor: ActionDescriptor,
        content: Any,
        registration: ToolRegistration | None,
        consumer: Consumer,
    ) -> Attempt:
        """Decision and reservation (approval handled by the caller)."""
        policy = self._deps.ledger.policy(str(self._config.tenant_id))
        assert policy is not None  # noqa: S101 (checked by the context assembly)
        attempt = self._pipeline.fix(attempt, descriptor)
        attempt = self._pipeline.decide(attempt, descriptor, content, registration, policy)
        return attempt

    def _reserve_and_permit(
        self, run_id: RunId, session: _Session, attempt: Attempt, consumer: Consumer
    ) -> Attempt:
        descriptor = self._deps.ledger.recorded_descriptor(str(attempt.attempt_id))
        assert descriptor is not None  # noqa: S101 (recorded by the decision)
        required = required_for(descriptor.cost_basis, self._config.price_table)
        try:
            attempt = self._pipeline.reserve(
                attempt, amount=required, cost_cap=descriptor.cost_cap.micro_units
            )
        except CostError:
            # The budget does not allow it: nothing runs; the Run waits for the owner.
            current = self._deps.sm.state.attempts[attempt.attempt_id]
            self._pipeline.abort(current, AbortReason.CANCELLED)
            self._end_step(self._deps.sm.state.steps[attempt.step_id], current)
            raise self._stop(session, run_id, RunStatus.PAUSED_BUDGET, "BUDGET") from None
        try:
            return self._pipeline.authorize(attempt, consumer)
        except SafetyError:
            # The permit or its consumption was refused (for example a permit that does
            # not match): nothing ran. End the Attempt and stop the Run for safety.
            current = self._deps.sm.state.attempts[attempt.attempt_id]
            self._pipeline.abort(current, AbortReason.REVOKED)
            self._end_step(self._deps.sm.state.steps[attempt.step_id], current)
            evidence = Termination(
                kind="termination",
                reason=TerminationReason.SAFETY_ABORT,
                evidence_ref=sha256_digest(f"{run_id}:permit".encode()),
            )
            raise self._stop(
                session, run_id, RunStatus.ABORTED_SAFETY, "PERMIT_REFUSED", evidence
            ) from None

    # ------------------------------------------------------------ the model call

    def _model_turn(
        self, run_id: RunId, session: _Session
    ) -> tuple[_Turn, ToolUseBlock | None, str | None]:
        deps, config = self._deps, self._config
        run = deps.sm.state.runs[run_id]
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
        except ContextBudgetExceededError:
            # B1 4.3: nothing is sent; the owner decides (no replanning in Phase 1).
            self._pipeline.abort(attempt, AbortReason.CANCELLED)
            self._end_step(step, attempt)
            raise self._stop(
                session, run_id, RunStatus.WAITING_APPROVAL, "CONTEXT_BUDGET_EXCEEDED"
            ) from None
        verify_build(result, inputs=inputs, profile=config.profile, params=config.params)
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
        bind_model_descriptor(result, descriptor)
        try:
            attempt = self._authorize(
                run_id, session, attempt, descriptor, content, None, Consumer.BROKER
            )
            if attempt.approval_required:
                raise SafetyError("MODEL_CALL_NEEDS_APPROVAL")  # not supported in Phase 1
        except SafetyError:
            self._pipeline.abort(
                self._deps.sm.state.attempts[attempt.attempt_id], AbortReason.POLICY_DENIED
            )
            self._end_step(step, attempt)
            raise self._fail(
                session, run_id, TerminationReason.SAFETY_ABORT, "MODEL_CALL_DENIED"
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
        except Exception:
            self._end_step(step, attempt)
            self._check(run_id, session)  # moves the Run to RECONCILIATION_REQUIRED
            raise
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
        if session.tool_calls >= config.limits.max_tool_calls:
            raise self._fail(session, run_id, TerminationReason.LIMIT_EXCEEDED, "LIMIT_EXCEEDED")
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
            assert policy is not None  # noqa: S101
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
            attempt = self._authorize(
                run_id, session, attempt, descriptor, content, registration, Consumer.BROKER
            )
        except (ToolError, SafetyError) as exc:
            self._pipeline.abort(
                self._deps.sm.state.attempts[attempt.attempt_id], AbortReason.POLICY_DENIED
            )
            self._end_step(step, attempt)
            reasons = getattr(exc, "reasons", ()) or (type(exc).__name__,)
            self._refuse_tool(
                session, pair_id, "/".join(str(r) for r in reasons), f"attempt:{attempt.attempt_id}"
            )
            return
        write = None
        if descriptor.apply_change is not None:
            parsed = proposal.canonical_input
            import json

            write = str(json.loads(parsed)["content"]).encode("utf-8")
        pending = _Pending(attempt.attempt_id, content, registration, write, pair_id)
        if attempt.approval_required:
            # The owner approves this exact action, or nothing happens. Control returns.
            self._pipeline.await_approval(attempt)
            session.pending = pending
            raise _Stop(self._wait(run_id, session))
        self._execute_tool(run_id, session, step, pending)

    def _wait(self, run_id: RunId, session: _Session) -> LoopOutcome:
        self._set(run_id, RunStatus.WAITING_APPROVAL)
        return self._outcome(session, run_id, "WAITING_APPROVAL", self._pending_hash(session))

    def _resume_tool(self, run_id: RunId, session: _Session, approval_id: ApprovalId) -> None:
        pending = session.pending
        assert pending is not None  # noqa: S101
        session.pending = None
        attempt = self._deps.sm.state.attempts[pending.attempt_id]
        step = self._deps.sm.state.steps[attempt.step_id]
        try:
            self._pipeline.approve(attempt, approval_id)
        except (SafetyError, CostError) as exc:
            self._pipeline.abort(
                self._deps.sm.state.attempts[attempt.attempt_id], AbortReason.APPROVAL_DENIED
            )
            self._end_step(step, attempt)
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
        except Exception:
            self._end_step(step, attempt)
            self._check(run_id, session)
            raise
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
        """One VERIFICATION read through the authorization path and the broker."""
        deps, config = self._deps, self._config
        registration = self._registration(run_id, "read_file")
        if registration is None:
            return None
        run = deps.sm.state.runs[run_id]
        step = self._new_step(run_id, StepKind.VERIFICATION)
        attempt = self._pipeline.start(step, registration.manifest.manifest_hash)
        policy = deps.ledger.policy(str(config.tenant_id))
        assert policy is not None  # noqa: S101
        canonical = canonical_json({"path": path}).decode()
        try:
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
            attempt = self._authorize(
                run_id, session, attempt, descriptor, content, registration, Consumer.BROKER
            )
            if attempt.approval_required:
                raise SafetyError("VERIFICATION_NEEDS_APPROVAL")
        except SafetyError:
            self._pipeline.abort(
                deps.sm.state.attempts[attempt.attempt_id], AbortReason.POLICY_DENIED
            )
            self._end_step(step, attempt)
            return None
        attempt = self._reserve_and_permit(run_id, session, attempt, Consumer.BROKER)
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
        except Exception:
            self._end_step(step, attempt)
            self._check(run_id, session)
            raise
        self._end_step(step, result.attempt)
        return result.data if result.attempt.state is A.COMPLETED else None

    def _verify(self, run_id: RunId, session: _Session) -> bool:
        """The done criteria checked on the actual files (never the model's word)."""
        deps = self._deps
        task = deps.sm.state.tasks[deps.sm.state.runs[run_id].task_id]
        results: list[CriterionResult] = []
        observed: list[str] = []
        expected: list[str] = []
        failures: list[str] = []
        for ref in task.done_criteria:
            spec = self._config.criteria.spec_for(ref)
            data = None if spec is None else self._read(run_id, session, spec.path)
            found = None if data is None else file_digest(data)
            passed = spec is not None and found == spec.expected_digest
            results.append(CriterionResult(criterion_ref=ref, passed=passed))
            observed.append(found or "missing")
            expected.append(spec.expected_digest if spec is not None else "unknown")
            if not passed:
                where = spec.path if spec is not None else ref
                failures.append(f"{where}: expected {expected[-1]}, found {observed[-1]}")
        attempts = [
            deps.sm.state.attempts[a]
            for s in deps.sm.state.run_steps[run_id]
            for a in deps.sm.state.step_attempts.get(s, ())
        ]
        unsettled = [
            a
            for a in attempts
            if a.reservation_id is not None
            and (view := deps.ledger.reservation(str(a.reservation_id))) is not None
            and (
                view.state not in _SETTLED_RESERVATIONS
                or view.hold_micro != 0
                or (view.state is ReservationState.USAGE_REPORTED and not view.spent_known)
            )
        ]
        if unsettled:
            # Unknown or unfinished costs: never a normal completion, and nothing more
            # runs; the Run waits until the cost is settled (WP-12 / the owner).
            raise self._stop(session, run_id, RunStatus.PAUSED_BUDGET, "UNSETTLED_COSTS")
        wrote = any(
            (d := deps.ledger.recorded_descriptor(str(a.attempt_id))) is not None
            and d.apply_change is not None
            and a.state is A.COMPLETED
            for a in attempts
        )
        evidence = VerificationEvidence(
            kind="verification",
            criteria=tuple(results),
            artifact_hash=sha256_digest("|".join(observed).encode()),
            expected_artifact_hash=sha256_digest("|".join(expected).encode()),
            apply_plan=ApplyPlanStatus.COMMITTED if wrote else ApplyPlanStatus.NOT_REQUIRED,
            passed=bool(results) and all(r.passed for r in results),
        )
        self._set(run_id, RunStatus.VERIFYING)
        if evidence.passed:
            self._set(run_id, RunStatus.COMPLETED, evidence)
            return True
        self._set(run_id, RunStatus.RUNNING, evidence)
        session.add(
            TurnKind.OBSERVATION,
            "Verification failed: " + "; ".join(failures),
            f"verification:{len(session.turns)}",
        )
        return False


def _xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)
