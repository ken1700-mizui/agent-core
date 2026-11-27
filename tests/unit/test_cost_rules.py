"""Cost rules (WP-09B; D 10.2, 11; E 4.1-4.3, 38; E.Z9): pure arithmetic and transitions."""

from __future__ import annotations

import pytest

from agent_core.adapters.replay_model import REPLAY_USAGE
from agent_core.cost.rules import (
    RESERVATION_TRANSITIONS,
    check_transition,
    checked_add,
    checked_mul,
    checked_sum,
    known_cost,
    required_amount,
)
from agent_core.types.cost import (
    FixedEstimate,
    ModelCallEstimate,
    PriceItem,
    ReservationState,
)
from agent_core.types.errors import CostOverflowError, ReservationStateError
from agent_core.types.model import CostStatus, Usage, UsageStatus
from agent_core.types.values import MAX_INT64, Budget, Currency

R = ReservationState


def test_checked_arithmetic_refuses_overflow_and_negatives() -> None:
    assert checked_add(MAX_INT64 - 1, 1) == MAX_INT64
    with pytest.raises(CostOverflowError):
        checked_add(MAX_INT64, 1)
    with pytest.raises(CostOverflowError):
        checked_mul(1 << 32, 1 << 32)
    with pytest.raises(CostOverflowError):
        checked_sum([MAX_INT64, 1])
    for bad in (-1, True, 1.0):
        with pytest.raises(CostOverflowError):
            checked_add(bad, 0)  # type: ignore[arg-type]
    assert checked_sum([]) == 0


def test_model_estimate_rounds_up_with_integers_only() -> None:
    price = PriceItem(input_micro_per_mtok=1_000_000, output_micro_per_mtok=4_000_000)
    estimate = ModelCallEstimate(kind="model", price=price, max_input_tokens=1, max_output_tokens=3)
    # 1 * 1e6 / 1e6 = 1; 3 * 4e6 / 1e6 = 12; exact.
    assert required_amount(estimate) == 13
    tiny = PriceItem(input_micro_per_mtok=1, output_micro_per_mtok=1)
    # Any non-zero usage rounds up to at least one micro unit (reservations round up).
    assert (
        required_amount(
            ModelCallEstimate(kind="model", price=tiny, max_input_tokens=1, max_output_tokens=0)
        )
        == 1
    )
    fixed = FixedEstimate(kind="fixed", amount=Budget(currency=Currency.XTU, micro_units=7))
    assert required_amount(fixed) == 7


def test_huge_estimates_overflow_instead_of_wrapping() -> None:
    price = PriceItem(input_micro_per_mtok=MAX_INT64, output_micro_per_mtok=MAX_INT64)
    estimate = ModelCallEstimate(
        kind="model", price=price, max_input_tokens=10_000_000, max_output_tokens=10_000_000
    )
    with pytest.raises(CostOverflowError):
        required_amount(estimate)


def test_unknown_cost_is_never_a_known_zero() -> None:
    unknown = Usage(
        status=UsageStatus.FINAL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=None,
        cost_status=CostStatus.UNKNOWN,
        synthetic=True,
    )
    assert known_cost(unknown) is None
    assert known_cost(REPLAY_USAGE) == 0


def test_reservation_transitions_are_explicit() -> None:
    assert RESERVATION_TRANSITIONS[R.ID_ALLOCATED] == frozenset({R.RESERVED, R.VOID})
    check_transition(R.ID_ALLOCATED, R.RESERVED)
    check_transition(R.RESERVED, R.CONSUMED)
    check_transition(R.CONSUMED, R.ACTIVE)
    check_transition(R.ACTIVE, R.USAGE_REPORTED)
    check_transition(R.UNKNOWN, R.RECONCILED)
    for current, target in (
        (R.RESERVED, R.RESERVED),  # activating twice
        (R.ID_ALLOCATED, R.CONSUMED),  # an allocated ID is not a reservation
        (R.ACTIVE, R.RELEASED),  # dispatched: never released
        (R.UNKNOWN, R.RELEASED),  # unknown outcome: hold kept
        (R.RELEASED, R.RELEASED),  # double release
        (R.VOID, R.RESERVED),  # a void ID is never used again
        (R.RECONCILED, R.UNKNOWN),
    ):
        with pytest.raises(ReservationStateError):
            check_transition(current, target)
    terminal = {R.RECONCILED, R.RELEASED, R.VOID}
    assert all(not RESERVATION_TRANSITIONS[t] for t in terminal)
    assert set(RESERVATION_TRANSITIONS) == set(R)
