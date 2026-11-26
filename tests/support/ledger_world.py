"""SQLite journal + budget ledger setups for WP-09B/C tests (synthetic only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.descriptor import AttemptRef, pin_of
from agent_core.types.cost import (
    BudgetScopeType,
    CounterKind,
    FixedEstimate,
    ReservationScopes,
)
from agent_core.types.ids import (
    ApprovalId,
    AttemptId,
    PepInstanceId,
    PermitId,
    ReceiptId,
    ReservationId,
)
from agent_core.types.ledger import (
    ActivateReservation,
    AllocateReservation,
    ClaimReceipt,
    ConfirmApproval,
    ConsumePermit,
    InstallPolicy,
    InstallRunGrant,
    IssuePermit,
    LedgerBatch,
    LedgerOp,
    RecordApproval,
    RecordDecision,
    RecordIntent,
    SetBudgetCap,
    SetCounterCap,
    SetRunWindow,
)
from agent_core.types.records import Attempt, AttemptState, Run, Step
from agent_core.types.safety import ActionDescriptor, ActionKind, Consumer, LedgerKeys, RunGrant
from agent_core.types.transitions import ContentFixedEvidence, PolicyDecisionEvidence
from agent_core.types.values import Budget, Currency
from support import builders as b
from support import runtime_world as w
from support import safety_world as s

PERIOD = "synthetic-2026-11"
CounterScope = Literal[BudgetScopeType.RUN, BudgetScopeType.TASK]
A = AttemptState


class ManualClock:
    def __init__(self, now: int = s.NOW) -> None:
        self.now = now

    def now_ms(self) -> int:
        return self.now


def xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def fixed(micro: int) -> FixedEstimate:
    return FixedEstimate(kind="fixed", amount=xtu(micro))


def new_keys() -> LedgerKeys:
    return LedgerKeys(owner=s.new_key(), permit=s.new_key())


def open_journal(path: Path, clock: Any, keys: LedgerKeys | None) -> SqliteJournal:
    return SqliteJournal.open(path, clock=clock, random=OsRandomSource(), keys=keys)


@dataclass
class Setup:
    path: Path
    journal: SqliteJournal
    sm: RuntimeStateMachine
    keys: LedgerKeys
    clock: ManualClock
    channel: OwnerApprovalChannel
    pep: PepInstanceId = field(default_factory=lambda: b.new_id(PepInstanceId))
    descriptors: dict[Any, ActionDescriptor] = field(default_factory=dict)
    grants: dict[Any, RunGrant] = field(default_factory=dict)

    def owner(self, *ops: LedgerOp) -> None:
        batch = LedgerBatch(ops=ops)
        self.journal.apply_owner_ledger(batch, owner_mac=self.channel.sign_ledger(batch))

    def reopen(self) -> Setup:
        """A restart: a new owner epoch and a new PEP instance."""
        if not self.journal.closed:
            self.journal.close()
        journal = open_journal(self.path, self.clock, self.keys)
        return Setup(
            self.path,
            journal,
            w.machine(journal),
            self.keys,
            self.clock,
            self.channel,
            descriptors=self.descriptors,
            grants=self.grants,
        )


def setup(tmp_path: Path, name: str = "journal.db") -> Setup:
    clock = ManualClock()
    keys = new_keys()
    path = tmp_path / name
    journal = open_journal(path, clock, keys)
    st = Setup(path, journal, w.machine(journal), keys, clock, OwnerApprovalChannel(keys.owner))
    st.owner(InstallPolicy(op="install_policy", policy=s.policy()))
    return st


def scopes_of(sm: RuntimeStateMachine, step: Step, experiment: Any = None) -> ReservationScopes:
    run = sm.state.runs[step.run_id]
    return ReservationScopes(
        tenant_id=b.TENANT,
        user_id=s.OWNER,
        task_id=run.task_id,
        run_id=run.run_id,
        step_id=step.step_id,
        period_id=PERIOD,
        experiment_id=experiment,
    )


def caps(st: Setup, scopes: ReservationScopes, micro: int = 10_000, **levels: int) -> None:
    """Every budget level, the enforced counters and the run window (explicit values)."""
    ids = {
        BudgetScopeType.STEP: str(scopes.step_id),
        BudgetScopeType.RUN: str(scopes.run_id),
        BudgetScopeType.TASK: str(scopes.task_id),
        BudgetScopeType.USER: str(scopes.user_id),
        BudgetScopeType.TENANT: str(scopes.tenant_id),
        BudgetScopeType.PERIOD: scopes.period_id,
    }
    if scopes.experiment_id is not None:
        ids[BudgetScopeType.EXPERIMENT] = str(scopes.experiment_id)
    ops: list[LedgerOp] = [
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=kind,
            scope_id=scope_id,
            cap=xtu(levels.get(kind.value.lower(), micro)),
        )
        for kind, scope_id in ids.items()
    ]
    for counter in (
        CounterKind.MODEL_CALLS,
        CounterKind.TOOL_CALLS,
        CounterKind.RETRIES,
        CounterKind.WORK_UNITS,
    ):
        counter_scopes: tuple[tuple[CounterScope, str], ...] = (
            (BudgetScopeType.RUN, str(scopes.run_id)),
            (BudgetScopeType.TASK, str(scopes.task_id)),
        )
        for kind, scope_id in counter_scopes:
            ops.append(
                SetCounterCap(
                    op="set_counter_cap",
                    scope_type=kind,
                    scope_id=scope_id,
                    counter=counter,
                    cap=levels.get(counter.value.lower(), 100),
                )
            )
    ops.append(SetRunWindow(op="set_run_window", run_id=scopes.run_id, max_elapsed_ms=3_600_000))
    if scopes.run_id not in st.grants:
        at = AttemptRef(
            tenant_id=scopes.tenant_id,
            task_id=scopes.task_id,
            run_id=scopes.run_id,
            step_id=scopes.step_id,
            attempt_id=b.new_id(AttemptId),
        )
        st.grants[scopes.run_id] = s.grant(at)
        ops.append(InstallRunGrant(op="install_run_grant", grant=st.grants[scopes.run_id]))
    st.owner(*ops)


def step_in(st: Setup, run: Run | None = None) -> Step:
    run = run if run is not None else w.running_run(st.sm)
    return w.step_in_progress(st.sm, run)


def allocate_op(
    attempt: Attempt, scopes: ReservationScopes, rsv: ReservationId
) -> AllocateReservation:
    return AllocateReservation(
        op="allocate",
        reservation_id=rsv,
        attempt_id=attempt.attempt_id,
        scopes=scopes,
        action_kind=ActionKind.TOOL_CALL,
        ttl_ms=60_000,
    )


def ref_of(st: Setup, attempt: Attempt) -> AttemptRef:
    step = st.sm.state.steps[attempt.step_id]
    run = st.sm.state.runs[step.run_id]
    return AttemptRef(
        tenant_id=b.TENANT,
        task_id=run.task_id,
        run_id=run.run_id,
        step_id=step.step_id,
        attempt_id=attempt.attempt_id,
    )


def fixed_attempt(
    st: Setup, step: Step, scopes: ReservationScopes, *, write: bool = False, **descriptor: Any
) -> Attempt:
    """PROPOSED -> VALIDATED -> CONTENT_FIXED: the reservation ID is allocated and the
    ActionDescriptor (which contains it) is fixed (E 38.1)."""
    attempt = st.sm.create_attempt(step)
    attempt = st.sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    rsv = b.new_id(ReservationId)
    build = s.write_descriptor if write else s.read_descriptor
    values: dict[str, Any] = {"reservation_id": rsv, "cost_cap": xtu(5_000)}
    values.update(descriptor)
    fixed_descriptor = build(ref_of(st, attempt), **values)
    st.descriptors[attempt.attempt_id] = fixed_descriptor
    evidence = ContentFixedEvidence(
        kind="content_fixed", action_hash=fixed_descriptor.action_hash(), reservation_id=rsv
    )
    return st.sm.advance_attempt(
        attempt, A.CONTENT_FIXED, evidence, ledger=(allocate_op(attempt, scopes, rsv),)
    )


def decision_op(st: Setup, attempt: Attempt, approval_required: bool) -> RecordDecision:
    descriptor = st.descriptors[attempt.attempt_id]
    registration = s.WRITE_TOOL if descriptor.tool == pin_of(s.WRITE_TOOL) else s.READ_TOOL
    return RecordDecision(
        op="record_decision",
        attempt_id=attempt.attempt_id,
        descriptor=descriptor,
        registration=registration,
        approval_required=approval_required,
    )


def allowed(st: Setup, attempt: Attempt, *, approval_required: bool | None = None) -> Attempt:
    """POLICY_ALLOWED with the PDP decision recorded by the ledger."""
    descriptor = st.descriptors[attempt.attempt_id]
    needed = (
        descriptor.tool == pin_of(s.WRITE_TOOL) if approval_required is None else approval_required
    )
    evidence = PolicyDecisionEvidence(
        kind="policy_decision",
        decision_ref=b.digest("decision"),
        action_hash=descriptor.action_hash(),
        policy_version=s.POLICY_VERSION,
        approval_required=needed,
    )
    return st.sm.advance_attempt(
        attempt, A.POLICY_ALLOWED, evidence, ledger=(decision_op(st, attempt, needed),)
    )


def record_approval(st: Setup, attempt: Attempt, **approval: Any) -> ApprovalId:
    signed = st.channel.approve(
        s.approval(st.descriptors[attempt.attempt_id].action_hash(), **approval)
    )
    st.journal.apply_ledger((RecordApproval(op="record_approval", signed=signed),))
    return signed.approval.approval_id


def approved(st: Setup, attempt: Attempt, approval_id: ApprovalId | None = None) -> Attempt:
    """APPROVAL_PENDING -> APPROVED with the recorded owner approval."""
    if approval_id is None:
        approval_id = record_approval(st, attempt)
    if attempt.state is A.POLICY_ALLOWED:
        attempt = st.sm.advance_attempt(attempt, A.APPROVAL_PENDING)
    evidence = w.evidence_for(attempt, A.APPROVED)
    op = ConfirmApproval(
        op="confirm_approval", attempt_id=attempt.attempt_id, approval_id=approval_id
    )
    return st.sm.advance_attempt(attempt, A.APPROVED, evidence, ledger=(op,))


def reserve(st: Setup, attempt: Attempt, amount: int = 1_000, **kw: Any) -> Attempt:
    """(POLICY_ALLOWED, APPROVED if needed) -> BUDGET_RESERVED with the activation."""
    if attempt.state is A.CONTENT_FIXED:
        attempt = allowed(st, attempt)
    if attempt.state is A.POLICY_ALLOWED and attempt.approval_required:
        attempt = approved(st, attempt)
    return st.sm.advance_attempt(
        attempt,
        A.BUDGET_RESERVED,
        w.evidence_for(attempt, A.BUDGET_RESERVED),
        ledger=(activate_op(attempt, amount, **kw),),
    )


def abort(st: Setup, attempt: Attempt, *, release: bool) -> Attempt:
    """ABORTED_BEFORE_DISPATCH, releasing the reservation only when asked."""
    from agent_core.types.ledger import ReleaseReservation

    ops: tuple[LedgerOp, ...] = ()
    if release and attempt.reservation_id is not None:
        ops = (
            ReleaseReservation(
                op="release",
                reservation_id=attempt.reservation_id,
                attempt_id=attempt.attempt_id,
                not_dispatched_ref=None,
            ),
        )
    evidence = w.evidence_for(attempt, A.ABORTED_BEFORE_DISPATCH, not_dispatched_ref=None)
    return st.sm.advance_attempt(attempt, A.ABORTED_BEFORE_DISPATCH, evidence, ledger=ops)


def end_run(st: Setup, run: Run) -> Run:
    """Finish a Run as FAILED (synthetic termination) after its attempts are final."""
    from agent_core.types.records import RunStatus, StepStatus
    from agent_core.types.transitions import Termination, TerminationReason

    for step in st.sm.state.steps_of_run(run.run_id):
        if step.status is StepStatus.IN_PROGRESS:
            st.sm.set_step_status(step, StepStatus.CANCELLED)
    termination = Termination(
        kind="termination", reason=TerminationReason.OWNER_CANCELLED, evidence_ref=b.digest("end")
    )
    return st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.FAILED, termination)


def intent(st: Setup, attempt: Attempt) -> Attempt:
    op = RecordIntent(
        op="record_intent",
        attempt_id=attempt.attempt_id,
        reservation_id=rsv(attempt),
        action_hash=action_of(attempt),
        intent_ref=b.digest("intent"),
    )
    return st.sm.advance_attempt(
        attempt, A.INTENT_RECORDED, w.evidence_for(attempt, A.INTENT_RECORDED), ledger=(op,)
    )


def issue(
    st: Setup, attempt: Attempt, *, ttl_ms: int = 30_000, consumer: Consumer = Consumer.BROKER
) -> Attempt:
    permit_id = b.new_id(PermitId)
    op = IssuePermit(
        op="issue_permit",
        attempt_id=attempt.attempt_id,
        permit_id=permit_id,
        consumer=consumer,
        ttl_ms=ttl_ms,
    )
    evidence = w.evidence_for(attempt, A.PERMIT_ISSUED, permit_id=permit_id)
    return st.sm.advance_attempt(attempt, A.PERMIT_ISSUED, evidence, ledger=(op,))


def consume_op(st: Setup, attempt: Attempt, **overrides: Any) -> ConsumePermit:
    assert attempt.permit_id is not None
    permit = st.journal.permit(attempt.permit_id)
    assert permit is not None
    values: dict[str, Any] = {
        "op": "consume_permit",
        "permit": permit,
        "consumer": Consumer.BROKER,
        "actual_action_hash": action_of(attempt),
        "receipt_id": b.new_id(ReceiptId),
        "pep_instance_id": st.pep,
        "receipt_ttl_ms": 30_000,
    }
    values.update(overrides)
    return ConsumePermit(**values)


def consume(
    st: Setup, attempt: Attempt, op: ConsumePermit | None = None, **overrides: Any
) -> Attempt:
    op = op if op is not None else consume_op(st, attempt, **overrides)
    evidence = w.evidence_for(attempt, A.PERMIT_CONSUMED, receipt_id=op.receipt_id)
    return st.sm.advance_attempt(attempt, A.PERMIT_CONSUMED, evidence, ledger=(op,))


def claim_op(st: Setup, attempt: Attempt, **overrides: Any) -> ClaimReceipt:
    assert attempt.receipt_id is not None
    receipt = st.journal.receipt(attempt.receipt_id)
    assert receipt is not None
    values: dict[str, Any] = {
        "op": "claim_receipt",
        "receipt": receipt,
        "pep_instance_id": st.pep,
        "actual_action_hash": action_of(attempt),
    }
    values.update(overrides)
    return ClaimReceipt(**values)


def claim(st: Setup, attempt: Attempt, op: ClaimReceipt | None = None) -> Attempt:
    op = op if op is not None else claim_op(st, attempt)
    evidence = w.evidence_for(attempt, A.DISPATCH_AUTHORIZED)
    return st.sm.advance_attempt(attempt, A.DISPATCH_AUTHORIZED, evidence, ledger=(op,))


def start(st: Setup, attempt: Attempt) -> Attempt:
    from agent_core.types.ledger import MarkActive

    op = MarkActive(op="mark_active", reservation_id=rsv(attempt), attempt_id=attempt.attempt_id)
    evidence = w.evidence_for(attempt, A.DISPATCH_STARTED)
    return st.sm.advance_attempt(attempt, A.DISPATCH_STARTED, evidence, ledger=(op,))


def issued(st: Setup, step: Step, *, write: bool = False) -> Attempt:
    """A fresh attempt up to PERMIT_ISSUED through the real path."""
    scopes = scopes_of(st.sm, step)
    caps(st, scopes)
    attempt = reserve(st, fixed_attempt(st, step, scopes, write=write), 1_000)
    return issue(st, intent(st, attempt))


def dispatched(st: Setup, step: Step) -> Attempt:
    """A dispatched attempt through the real ledger path (WP-09B/C)."""
    return start(st, claim(st, consume(st, issued(st, step))))


def completed_scenario(st: Setup) -> Any:
    """A Task with one completed Run (synthetic), touching every entity kind."""
    from agent_core.types.records import RunStatus, StepStatus, TaskStatus

    the_task = w.task(st.sm)
    run = w.running_run(st.sm, the_task)
    step = w.step_in_progress(st.sm, run)
    attempt = dispatched(st, step)
    st.sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    verifying = st.sm.set_run_status(st.sm.state.runs[run.run_id], RunStatus.VERIFYING)
    st.sm.set_run_status(verifying, RunStatus.COMPLETED, w.passing_verification(the_task))
    st.sm.set_task_status(st.sm.state.tasks[the_task.task_id], TaskStatus.COMPLETED)
    return the_task


def rsv(attempt: Attempt) -> ReservationId:
    assert attempt.reservation_id is not None
    return attempt.reservation_id


def action_of(attempt: Attempt) -> str:
    assert attempt.action_hash is not None
    return attempt.action_hash


def activate_op(
    attempt: Attempt, amount: int, cap: int = 5_000, required: int = 100
) -> ActivateReservation:
    return ActivateReservation(
        op="activate",
        reservation_id=rsv(attempt),
        attempt_id=attempt.attempt_id,
        action_hash=action_of(attempt),
        estimate=fixed(required),
        amount=xtu(amount),
        cost_cap=xtu(cap),
    )


def grant_only(st: Setup, scopes: ReservationScopes) -> None:
    """Install the Run grant without any budget cap (for missing-cap tests)."""
    at = AttemptRef(
        tenant_id=scopes.tenant_id,
        task_id=scopes.task_id,
        run_id=scopes.run_id,
        step_id=scopes.step_id,
        attempt_id=b.new_id(AttemptId),
    )
    st.grants[scopes.run_id] = s.grant(at)
    st.owner(InstallRunGrant(op="install_run_grant", grant=st.grants[scopes.run_id]))
