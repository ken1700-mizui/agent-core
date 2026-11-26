"""Runtime state machine service (WP-04): plans, verifies evidence, journals, then updates.

Order of every command:

1. Plan the transitions with the pure state machine (edge, preconditions,
   evidence consistency). Nothing is written if this fails.
2. Ask the ``EvidenceVerifier`` port to confirm every piece of evidence.
3. Append one runtime event (``schema_version`` 2) to the Journal.
4. Only after the Journal returned the stored event, fold that stored event
   into the state (the same ``apply_event`` used by ``rebuild``). If the append
   fails, the state is unchanged and no snapshot is returned.

The Journal is the source of truth: ``load`` rebuilds the state from it. This
module depends on ports only, never on a concrete adapter.
"""

from __future__ import annotations

from typing import Any

from agent_core.identity.generator import IdGenerator
from agent_core.ports.clock import Clock
from agent_core.ports.evidence import EvidenceVerifier
from agent_core.ports.journal import JournalPort
from agent_core.runtime.state_machine import (
    RuntimeState,
    apply_event,
    apply_payload,
    apply_transition,
    plan_attempt_create,
    plan_attempt_status,
    plan_run_create,
    plan_run_status,
    plan_step_create,
    plan_step_status,
    plan_task_attach,
    plan_task_create,
    plan_task_status,
    rebuild,
)
from agent_core.types.errors import StateDivergenceError, StateRebuildError
from agent_core.types.events import Event, EventDraft
from agent_core.types.ids import AttemptId, EventId, RunGrantId, RunId, StepId, TaskId, TenantId
from agent_core.types.records import (
    Attempt,
    AttemptState,
    Run,
    RunStatus,
    Step,
    StepKind,
    StepStatus,
    Task,
    TaskStatus,
)
from agent_core.types.transitions import (
    RUNTIME_EVENT_SCHEMA_VERSION,
    RUNTIME_EVENT_TYPE,
    AttemptEvidence,
    AttemptTransition,
    RunEvidence,
    RuntimePayload,
    RunTransition,
    Transition,
)
from agent_core.types.values import Budget, EngineVersion, NonMonetaryCaps

READ_PAGE = 1_000
# Phase 1 is synthetic only (PHASE_1_IMPLEMENTATION_PLAN section 2).
EVENT_LABELS = ("synthetic",)


class RuntimeStateMachine:
    def __init__(
        self,
        *,
        journal: JournalPort,
        ids: IdGenerator,
        clock: Clock,
        verifier: EvidenceVerifier,
        tenant_id: TenantId,
        engine: EngineVersion,
        policy_version: str,
        state: RuntimeState,
    ) -> None:
        self._journal = journal
        self._ids = ids
        self._clock = clock
        self._verifier = verifier
        self._tenant_id = tenant_id
        self._engine = engine
        self._policy_version = policy_version
        self._state = state
        self._late = False
        self._diverged = False

    @classmethod
    def load(
        cls,
        journal: JournalPort,
        *,
        ids: IdGenerator,
        clock: Clock,
        verifier: EvidenceVerifier,
        tenant_id: TenantId,
        engine: EngineVersion,
        policy_version: str,
    ) -> RuntimeStateMachine:
        """Rebuild the state from the Journal (the source of truth)."""
        events: list[Event] = []
        while True:
            page = journal.read_tenant(tenant_id, after_tenant_seq=len(events), limit=READ_PAGE)
            events.extend(page)
            if len(page) < READ_PAGE:
                break
        return cls(
            journal=journal,
            ids=ids,
            clock=clock,
            verifier=verifier,
            tenant_id=tenant_id,
            engine=engine,
            policy_version=policy_version,
            state=rebuild(events),
        )

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def last_transition_was_late(self) -> bool:
        """True when the last attempt result arrived while its Run was CANCELLING."""
        return self._late

    # ------------------------------------------------------------ tasks

    def create_task(
        self,
        *,
        goal_ref: str,
        done_criteria: tuple[str, ...],
        budget_total: Budget,
        caps_total: NonMonetaryCaps,
    ) -> Task:
        task = Task(
            task_id=self._ids.new(TaskId),
            goal_ref=goal_ref,
            done_criteria=done_criteria,
            budget_total=budget_total,
            caps_total=caps_total,
            status=TaskStatus.CREATED,
            runs=(),
        )
        self._commit((plan_task_create(self._state, task),))
        return self._state.tasks[task.task_id]

    def set_task_status(self, task: Task, target: TaskStatus) -> Task:
        self._commit((plan_task_status(self._state, task, target),))
        return self._state.tasks[task.task_id]

    # ------------------------------------------------------------ runs

    def start_run(self, task: Task, *, run_grant_ref: RunGrantId) -> Run:
        """Create the next Run of ``task``; the Task's budget and caps carry over unchanged."""
        transitions: list[Transition] = []
        state = self._state
        if task.status is TaskStatus.CREATED:
            transitions.append(plan_task_status(state, task, TaskStatus.ACTIVE))
            state = apply_transition(state, transitions[-1])
            task = state.tasks[task.task_id]
        run = Run(
            run_id=self._ids.new(RunId),
            task_id=task.task_id,
            run_grant_ref=run_grant_ref,
            status=RunStatus.CREATED,
            owner_epoch=self._journal.owner_epoch,
            parent_run_id=task.runs[-1] if task.runs else None,
        )
        transitions.append(plan_run_create(state, run))
        state = apply_transition(state, transitions[-1])
        transitions.append(plan_task_attach(state, state.tasks[task.task_id], run.run_id))
        self._commit(tuple(transitions))
        return self._state.runs[run.run_id]

    def set_run_status(
        self, run: Run, target: RunStatus, evidence: RunEvidence | None = None
    ) -> Run:
        transition = plan_run_status(self._state, run, target, evidence)
        self._commit((transition,))
        return self._state.runs[run.run_id]

    # ------------------------------------------------------------ steps

    def create_step(self, run: Run, kind: StepKind) -> Step:
        step = Step(
            step_id=self._ids.new(StepId),
            run_id=run.run_id,
            run_seq=self._state.run_seqs.get(run.run_id, 0) + 1,
            kind=kind,
            status=StepStatus.PLANNED,
        )
        self._commit((plan_step_create(self._state, step),))
        return self._state.steps[step.step_id]

    def set_step_status(self, step: Step, target: StepStatus) -> Step:
        self._commit((plan_step_status(self._state, step, target),))
        return self._state.steps[step.step_id]

    # ------------------------------------------------------------ attempts

    def create_attempt(self, step: Step) -> Attempt:
        attempt = Attempt(
            attempt_id=self._ids.new(AttemptId),
            step_id=step.step_id,
            state=AttemptState.PROPOSED,
            action_hash=None,
            permit_id=None,
            receipt_id=None,
            reservation_id=None,
            approval_required=None,
        )
        self._commit((plan_attempt_create(self._state, attempt),))
        return self._state.attempts[attempt.attempt_id]

    def advance_attempt(
        self,
        attempt: Attempt,
        target: AttemptState,
        evidence: AttemptEvidence | None = None,
    ) -> Attempt:
        transition = plan_attempt_status(self._state, attempt, target, evidence)
        self._commit((transition,))
        self._late = transition.late
        return self._state.attempts[attempt.attempt_id]

    # ------------------------------------------------------------ commit

    def _verify_evidence(self, transitions: tuple[Transition, ...]) -> None:
        for transition in transitions:
            if isinstance(transition, (RunTransition, AttemptTransition)):
                evidence: Any = transition.evidence
                if evidence is not None and transition.before is not None:
                    self._verifier.verify(evidence, subject=transition.before)

    def _commit(self, transitions: tuple[Transition, ...]) -> Event:
        if self._diverged:
            raise StateDivergenceError("state diverged from the journal; reload required")
        payload = RuntimePayload(transitions=transitions)
        # Dry run of the whole payload on the current state (no I/O, no change).
        _, run_id = apply_payload(self._state, payload)
        self._verify_evidence(transitions)

        def write(event_id: EventId) -> Event:
            draft = EventDraft(
                event_id=event_id,
                type=RUNTIME_EVENT_TYPE,
                schema_version=RUNTIME_EVENT_SCHEMA_VERSION,
                tenant_id=self._tenant_id,
                run_id=run_id,
                engine=self._engine,
                policy_version=self._policy_version,
                access_epoch=0,
                owner_epoch=self._journal.owner_epoch,
                revocation_gen=0,
                labels=EVENT_LABELS,
                refs=(),
                keyed_hashes=(),
                recorded_at_ms=self._clock.now_ms(),
                payload=payload,
            )
            return self._journal.append(draft)

        event = self._ids.issue_unique(EventId, write)
        try:
            self._state = apply_event(self._state, event)
        except StateRebuildError as exc:
            self._diverged = True
            raise StateDivergenceError("stored event does not fold into the state") from exc
        return event
