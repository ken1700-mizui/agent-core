"""Pure cost rules (WP-09B; D 10.2, E 4.1-4.3, E 38; E.Z9).

- Checked integer arithmetic: every operand is a non-negative ``int`` (not bool)
  and every result stays within 64 bits; anything else is ``CostOverflowError``.
  Python integers do not wrap, so the check is explicit, not a 128-bit trick.
- ``required_amount`` rounds reservations up (D 10.2).
- ``known_cost`` returns the cost only when it is final; unknown is ``None``,
  never zero.
- ``RESERVATION_TRANSITIONS`` lists every allowed edge (E 4.1, E 38.3).
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from agent_core.types.cost import (
    MICRO_PER_MTOK,
    FixedEstimate,
    ModelCallEstimate,
    ReservationState,
)
from agent_core.types.errors import CostOverflowError, ReservationStateError
from agent_core.types.model import CostStatus, Usage
from agent_core.types.values import MAX_INT64

R = ReservationState

RESERVATION_TRANSITIONS = MappingProxyType(
    {
        R.ID_ALLOCATED: frozenset({R.RESERVED, R.VOID}),
        R.RESERVED: frozenset({R.CONSUMED, R.RELEASED}),
        # CONSUMED -> RELEASED only with a live PEP's not-dispatched report (E.Z6).
        R.CONSUMED: frozenset({R.ACTIVE, R.UNKNOWN, R.RELEASED}),
        R.ACTIVE: frozenset({R.USAGE_REPORTED, R.UNKNOWN}),
        R.USAGE_REPORTED: frozenset({R.RECONCILED}),
        R.UNKNOWN: frozenset({R.RECONCILED}),
        R.RECONCILED: frozenset(),
        R.RELEASED: frozenset(),
        R.VOID: frozenset(),
    }
)


def _operand(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INT64:
        raise CostOverflowError("OPERAND_OUT_OF_RANGE")
    return value


def checked_add(a: int, b: int) -> int:
    total = _operand(a) + _operand(b)
    if total > MAX_INT64:
        raise CostOverflowError("SUM_OVERFLOW")
    return total


def checked_mul(a: int, b: int) -> int:
    product = _operand(a) * _operand(b)
    if product > MAX_INT64:
        raise CostOverflowError("PRODUCT_OVERFLOW")
    return product


def checked_sum(values: Iterable[int]) -> int:
    total = 0
    for value in values:
        total = checked_add(total, value)
    return total


def ceil_div(numerator: int, denominator: int) -> int:
    _operand(numerator)
    if type(denominator) is not int or denominator <= 0:
        raise CostOverflowError("DIVISOR_INVALID")
    return -(-numerator // denominator)


def required_amount(estimate: ModelCallEstimate | FixedEstimate) -> int:
    """The trusted minimum reservation for an estimate, rounded up."""
    if isinstance(estimate, FixedEstimate):
        return _operand(estimate.amount.micro_units)
    if not isinstance(estimate, ModelCallEstimate):
        raise CostOverflowError("ESTIMATE_INVALID")
    price = estimate.price
    inputs = ceil_div(
        checked_mul(estimate.max_input_tokens, price.input_micro_per_mtok), MICRO_PER_MTOK
    )
    outputs = ceil_div(
        checked_mul(estimate.max_output_tokens, price.output_micro_per_mtok), MICRO_PER_MTOK
    )
    return checked_add(inputs, outputs)


def known_cost(usage: Usage) -> int | None:
    """The final cost in micro units, or None when it is not known (never 0)."""
    if usage.cost_status is not CostStatus.FINAL or usage.cost is None:
        return None
    return _operand(usage.cost.micro_units)


def check_transition(current: ReservationState, target: ReservationState) -> None:
    if target not in RESERVATION_TRANSITIONS[current]:
        raise ReservationStateError(f"{current.value}_TO_{target.value}")
