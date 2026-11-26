"""WP-09 limited fixes, stage 1: trusted cost basis (WP09-R01), Task budget and
accounting (WP09-R02), binding of scopes, targets and the attempt chain (WP09-R06).

Each test reproduces a defect found in review; it failed before the fix."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.safety.descriptor import tool_action
from agent_core.safety.pdp import decide
from agent_core.types.cost import BudgetScopeType, CounterKind, ReservationState
from agent_core.types.errors import (
    ActionDescriptorError,
    BudgetExceededError,
    CostBasisMissingError,
    CounterCapExceededError,
    PermitRejectedError,
    PolicyDeniedError,
    ReservationAmountError,
)
from agent_core.types.ids import StepId
from agent_core.types.ledger import (
    ActivateReservation,
    AllocateReservation,
    Correct,
    Reconcile,
    SetRunWindow,
)
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.records import AttemptState
from agent_core.types.safety import (
    ActionDescriptor,
    DenyReason,
    ModelCostBasis,
    ToolCostBasis,
)
from agent_core.types.tools import FieldSpec, FieldType, ObjectSchema, ResourceAccess
from support import builders as b
from support import ledger_world as lw
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = ReservationState


@pytest.fixture
def st(tmp_path: Path) -> Any:
    setup = lw.setup(tmp_path)
    yield setup
    if not setup.journal.closed:
        setup.journal.close()


def _usage(micro: int, tokens: tuple[int, int] = (10, 5)) -> Usage:
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        total_tokens=sum(tokens),
        cost=lw.xtu(micro),
        cost_status=CostStatus.FINAL,
        synthetic=True,
    )


# ---------------------------------------------------------------- WP09-R01: trusted cost basis


def test_the_caller_cannot_supply_the_estimate() -> None:
    # Before: ActivateReservation.estimate came from the runtime (FixedEstimate(1)).
    assert "estimate" not in ActivateReservation.model_fields


def test_tool_required_amount_comes_from_the_trusted_manifest(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes, costly=True)
    descriptor = st.descriptors[attempt.attempt_id]
    assert descriptor.cost_basis == ToolCostBasis(kind="tool", estimate=lw.xtu(300))
    with pytest.raises(ReservationAmountError, match="BELOW_REQUIRED"):
        lw.reserve(st, attempt, 299)
    assert lw.reserve(st, st.sm.state.attempts[attempt.attempt_id], 300).state is A.BUDGET_RESERVED


def test_model_required_amount_comes_from_the_owner_price_table(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes, model=True)
    basis = st.descriptors[attempt.attempt_id].cost_basis
    assert isinstance(basis, ModelCostBasis)
    # scripted-model-small: 1 micro per input token, 4 per output token (test prices).
    required = basis.max_input_tokens + 4 * basis.max_output_tokens
    with pytest.raises(ReservationAmountError, match="BELOW_REQUIRED"):
        lw.reserve(st, attempt, required - 1)
    assert (
        lw.reserve(st, st.sm.state.attempts[attempt.attempt_id], required).state
        is A.BUDGET_RESERVED
    )


def test_without_an_installed_price_table_a_model_call_is_not_reserved(tmp_path: Path) -> None:
    st = lw.setup(tmp_path, price_table=False)
    try:
        step = lw.step_in(st)
        scopes = lw.scopes_of(st.sm, step)
        lw.caps(st, scopes)
        attempt = lw.fixed_attempt(st, step, scopes, model=True)
        with pytest.raises(CostBasisMissingError):
            lw.reserve(st, attempt, 5_000)
    finally:
        st.journal.close()


def test_a_model_call_cannot_use_a_tool_estimate() -> None:
    at = s.ref()
    model = s.model_descriptor(at)
    with pytest.raises(ValidationError):
        ActionDescriptor.model_validate(
            {
                **model.model_dump(),
                "cost_basis": {"kind": "tool", "estimate": lw.xtu(0).model_dump()},
            }
        )
    tool = s.read_descriptor(at)
    with pytest.raises(ValidationError):
        ActionDescriptor.model_validate(
            {**tool.model_dump(), "cost_basis": model.cost_basis.model_dump()}
        )


def test_the_cost_basis_must_match_the_manifest_and_the_policy() -> None:
    at = s.ref()
    costly = s.read_descriptor(
        at, registration=s.COSTLY_TOOL, canonical_input='{"path":"workspace/a.txt"}'
    )
    lowered = costly.model_copy(
        update={"cost_basis": ToolCostBasis(kind="tool", estimate=lw.xtu(1))}
    )
    decision = decide(
        policy=s.policy(),
        grant=s.grant(at),
        descriptor=lowered,
        registration=s.COSTLY_TOOL,
        context=s.context(),
    )
    assert DenyReason.DESCRIPTOR_MISMATCH in decision.reasons
    model = s.model_descriptor(at)
    other_table = model.model_copy(
        update={
            "cost_basis": model.cost_basis.model_copy(update={"price_table_id": "another_table"})
        }
    )
    decision = decide(
        policy=s.policy(),
        grant=s.grant(at),
        descriptor=other_table,
        registration=None,
        context=s.context(),
    )
    assert DenyReason.COST_BASIS_UNTRUSTED in decision.reasons


def test_the_cost_basis_is_part_of_the_action_hash() -> None:
    at = s.ref()
    model = s.model_descriptor(at)
    lowered = model.model_copy(
        update={"cost_basis": model.cost_basis.model_copy(update={"max_output_tokens": 1})}
    )
    assert lowered.action_hash() != model.action_hash()


def test_model_ceilings_come_from_the_verified_request() -> None:
    at = s.ref()
    record, rendered = s.model_request(max_output_tokens=77)
    model = s.model_descriptor(at, request_record=record, rendered_input=rendered)
    basis = model.cost_basis
    assert isinstance(basis, ModelCostBasis)
    assert basis.max_output_tokens == 77
    assert basis.max_input_tokens == len(rendered)  # bytes: a conservative token ceiling
    with pytest.raises(ActionDescriptorError):
        s.model_descriptor(at, request_record=record, rendered_input=rendered + b" ")


def test_required_amount_is_checked_again_at_issue(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.intent(st, lw.reserve(st, lw.fixed_attempt(st, step, scopes, costly=True), 300))
    st.journal.close()
    conn = sqlite3.connect(st.path)
    conn.execute(
        "UPDATE reservations SET reserved_micro = 100, hold_micro = 100 WHERE reservation_id = ?",
        (str(attempt.reservation_id),),
    )
    conn.commit()
    conn.close()
    st2 = st.reopen(verify=False)
    try:
        with pytest.raises(PermitRejectedError, match="RESERVATION_INVALID"):
            lw.issue(st2, st2.sm.state.attempts[attempt.attempt_id])
    finally:
        st2.journal.close()


# ---------------------------------------------------------------- WP09-R02: Task budget, costs


def test_the_task_budget_total_bounds_a_larger_ledger_cap(st: Any) -> None:
    run = lw.run_for_task(st, budget=1_500)
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, task=10_000)
    first = lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000)
    second = lw.next_attempt(st, step, first)
    with pytest.raises(BudgetExceededError, match="TASK"):
        lw.reserve(st, second, 1_000)


def test_the_task_budget_applies_without_a_configured_task_cap(st: Any) -> None:
    run = lw.run_for_task(st, budget=500)
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, skip_task=True)
    with pytest.raises(BudgetExceededError, match="TASK"):
        lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000)


def test_task_non_monetary_caps_bound_the_ledger(st: Any) -> None:
    run = lw.run_for_task(st, caps={"tool_calls": 1})
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)  # ledger counters allow 100
    first = lw.reserve(st, lw.fixed_attempt(st, step, scopes), 100)
    second = lw.next_attempt(st, step, first)
    with pytest.raises(CounterCapExceededError, match="TOOL_CALLS"):
        lw.reserve(st, second, 100)


def test_task_wall_clock_is_enforced(st: Any) -> None:
    run = lw.run_for_task(st, caps={"wall_clock_ms": 1_000})
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes)
    st.clock.now += 1_500
    with pytest.raises(CounterCapExceededError, match="ELAPSED"):
        lw.reserve(st, attempt, 100)


def test_setting_the_run_window_again_does_not_reset_elapsed_time(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    st.owner(SetRunWindow(op="set_run_window", run_id=scopes.run_id, max_elapsed_ms=1_000))
    st.clock.now += 1_500
    # Before: setting the window again restarted it "from now".
    st.owner(SetRunWindow(op="set_run_window", run_id=scopes.run_id, max_elapsed_ms=1_000))
    with pytest.raises(CounterCapExceededError, match="ELAPSED"):
        lw.reserve(st, lw.fixed_attempt(st, step, scopes), 100)


def _active(st: Any, amount: int = 1_000, **caps: int) -> Any:
    """A dispatched call of the costly tool (synthetic billing: 300 micro XTU)."""
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, **caps)
    attempt = lw.reserve(st, lw.fixed_attempt(st, step, scopes, costly=True), amount)
    return lw.start(st, lw.claim(st, lw.consume(st, lw.issue(st, lw.intent(st, attempt))))), scopes


def test_reconciliation_keeps_earlier_corrections(st: Any) -> None:
    attempt, _ = _active(st)
    lw.report(st, attempt, _usage(300))
    st.owner(
        Correct(
            op="correct",
            reservation_id=attempt.reservation_id,
            delta_micro=50,
            refers_to_entry=1,
            evidence_ref=b.digest("synthetic-correction"),
        )
    )
    st.owner(
        Reconcile(
            op="reconcile",
            reservation_id=attempt.reservation_id,
            actual=lw.xtu(300),
            evidence_ref=b.digest("synthetic-bill"),
        )
    )
    view = st.journal.reservation(attempt.reservation_id)
    # Before: the reconciliation replaced the spent cost and lost the +50 correction.
    assert (view.actual_micro, view.adjust_micro, view.spent_micro) == (300, 50, 350)


def test_a_correction_beyond_the_budget_is_an_overrun(st: Any) -> None:
    attempt, scopes = _active(st, run=1_100)
    lw.report(st, attempt, _usage(300))
    st.owner(
        Correct(
            op="correct",
            reservation_id=attempt.reservation_id,
            delta_micro=900,
            refers_to_entry=1,
            evidence_ref=b.digest("synthetic-correction"),
        )
    )
    kinds = [e.kind for e in st.journal.ledger_entries(attempt.reservation_id)]
    assert "OVERRUN" in kinds
    assert st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id)).blocked
    view = st.journal.reservation(attempt.reservation_id)
    assert view.spent_micro == 1_200  # recorded in full, beyond the budget


# ---------------------------------------------------------------- WP09-R06: scopes, targets, chain


def test_budget_scopes_come_from_trusted_state(st: Any) -> None:
    # Before: the caller passed ReservationScopes and could name another Task or user.
    assert "scopes" not in AllocateReservation.model_fields
    assert "action_kind" not in AllocateReservation.model_fields
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000)
    grant = st.grants[scopes.run_id]
    for kind, scope_id in (
        (BudgetScopeType.TASK, str(scopes.task_id)),
        (BudgetScopeType.USER, str(grant.user_id)),
        (BudgetScopeType.PERIOD, grant.period_id),
    ):
        assert st.journal.scope_usage(kind, scope_id).hold_micro == 1_000


def test_targets_are_derived_from_the_arguments() -> None:
    at = s.ref()
    descriptor = s.read_descriptor(at, canonical_input='{"path":"workspace/x.txt"}')
    assert [(r.path, r.access) for r in descriptor.target_resources] == [
        ("workspace/x.txt", ResourceAccess.READ)
    ]
    assert "resources" not in tool_action.__code__.co_varnames
    with pytest.raises(ActionDescriptorError):
        s.read_descriptor(at, canonical_input='{"max_bytes":5}')  # no path argument


def test_declared_resources_need_a_required_resource_argument() -> None:
    plain = ObjectSchema(
        fields=(FieldSpec(name="path", type=FieldType.STRING, required=True, max_length=256),),
        max_bytes=1_024,
    )
    with pytest.raises(ValidationError):
        tw.body(input_schema=plain)
    with pytest.raises(ValidationError):
        FieldSpec(
            name="path",
            type=FieldType.STRING,
            required=False,
            max_length=256,
            resource=ResourceAccess.READ,
        )
    with pytest.raises(ValidationError):
        FieldSpec(name="size", type=FieldType.INTEGER, required=True, resource=ResourceAccess.READ)


def test_a_descriptor_with_emptied_targets_is_not_recorded(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    # The attempt is fixed on a descriptor whose targets were removed, while the
    # arguments still name a file: the ledger rebuilds the descriptor and refuses.
    attempt = lw.fixed_attempt(st, step, scopes, tamper={"target_resources": ()})
    with pytest.raises(PermitRejectedError, match="DESCRIPTOR_NOT_FROM_CONTENT"):
        lw.allowed(st, attempt)


def test_a_descriptor_outside_the_attempt_chain_is_not_recorded(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes)
    attempt = lw.fixed_attempt(st, step, scopes, tamper={"step_id": b.new_id(StepId)})
    with pytest.raises((PermitRejectedError, PolicyDeniedError), match="ATTEMPT_CHAIN"):
        lw.allowed(st, attempt)


def test_the_action_kind_comes_from_the_descriptor(st: Any) -> None:
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    lw.caps(st, scopes, model_calls=0)
    attempt = lw.fixed_attempt(st, step, scopes, model=True)
    basis = st.descriptors[attempt.attempt_id].cost_basis
    with pytest.raises(CounterCapExceededError, match="MODEL_CALLS"):
        lw.reserve(st, attempt, basis.max_input_tokens + 4 * basis.max_output_tokens)
    view = st.journal.counter(BudgetScopeType.RUN, str(scopes.run_id), CounterKind.TOOL_CALLS)
    assert view.used == 0
