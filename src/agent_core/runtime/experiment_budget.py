"""Experiment budget across homes (WP2-08; contract 7.1-7.8, D19).

Where the budget is enforced, and by what:
- each home's ledger, in the reservation's one transaction (D.V10): the
  EXPERIMENT budget cap and the EXPERIMENT work-unit cap (required for any
  reservation of an experiment; WP2-08). A home without an opening has neither
  and refuses the experiment's reservations (``BudgetCapMissingError``);
- the trial's Run caps (``trial_ops``): ``trial_cap_xtu`` and
  ``trial_event_cap`` as the Run's budget and work-unit caps.

The home chain (``HomeChainPort``) records the openings, closings and
resolutions; every ledger cap is set to an ABSOLUTE value computed from the
chain, so re-running a step after a crash never adds budget. Order of each
step: lowering a cap first (closing), recording first then raising (opening,
resolution); ``repair`` re-applies the chain's values. ``admit_trial`` refuses
a new trial whenever the chain and the ledger disagree or the remaining budget
cannot cover the trial cap (fail closed). The Results Store is a copy that
``check_chain`` compares, never the enforcement.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from agent_core.ports.experiment import ExperimentHomePort, HomeChainPort
from agent_core.types.cost import BudgetScopeType, CounterKind, ReservationState
from agent_core.types.errors import AgentCoreError
from agent_core.types.evaluation import ExperimentSpec
from agent_core.types.experiment_budget import (
    CarriedHold,
    CarriedHoldResolved,
    ClosingBody,
    ClosingStatement,
    OpeningStatement,
)
from agent_core.types.ids import ExperimentId, HomeId, ReservationId, RunId
from agent_core.types.ledger import LedgerOp, SetBudgetCap, SetCounterCap
from agent_core.types.serialization import dump_json
from agent_core.types.values import Budget, Currency

EXPERIMENT: Final = BudgetScopeType.EXPERIMENT
_EXPERIMENT_COUNTER: Final[Literal[BudgetScopeType.EXPERIMENT]] = BudgetScopeType.EXPERIMENT
_SETTLED = frozenset(
    {
        ReservationState.USAGE_REPORTED,
        ReservationState.RECONCILED,
        ReservationState.RELEASED,
        ReservationState.VOID,
    }
)


class ExperimentBudgetError(AgentCoreError):
    """The experiment budget cannot be kept with certainty: refused (fail closed)."""

    code = "EXPERIMENT_BUDGET_REFUSED"


def _refuse(reason: str) -> ExperimentBudgetError:
    return ExperimentBudgetError(reason)


def _xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


# ---------------------------------------------------------------- the chain


@dataclass(frozen=True)
class ChainState:
    experiment_id: ExperimentId
    openings: tuple[OpeningStatement, ...]
    closings: Mapping[str, ClosingStatement]
    resolutions: tuple[CarriedHoldResolved, ...]

    @property
    def tip(self) -> OpeningStatement | None:
        return self.openings[-1] if self.openings else None

    @property
    def tip_closing(self) -> ClosingStatement | None:
        tip = self.tip
        return None if tip is None else self.closings.get(str(tip.home_id))

    def resolved_in(self, home_id: HomeId) -> tuple[CarriedHoldResolved, ...]:
        return tuple(r for r in self.resolutions if r.recorded_in == home_id)

    @property
    def unresolved(self) -> tuple[CarriedHold, ...]:
        """The carried holds not yet resolved (of the open tip, or of the closed
        tip's closing, which the next opening carries)."""
        tip = self.tip
        if tip is None:
            return ()
        closing = self.tip_closing
        if closing is not None:
            return tuple(sorted((*closing.carried_unresolved, *closing.held), key=lambda h: h.key))
        done = {r.key for r in self.resolved_in(tip.home_id)}
        return tuple(h for h in tip.carried if h.key not in done)


def state(chain: HomeChainPort, experiment_id: ExperimentId) -> ChainState:
    """The chain, checked link by link (each read checks every record's MAC)."""
    openings = chain.openings(experiment_id)
    closings = {str(c.home_id): c for c in chain.closings(experiment_id)}
    resolutions = chain.resolutions(experiment_id)
    for i, opening in enumerate(openings):
        if opening.index != i:
            raise _refuse("CHAIN_INCONSISTENT:INDEX")
        closing = closings.get(str(opening.home_id))
        if closing is not None and closing.opening_hash != opening.opening_hash():
            raise _refuse("CHAIN_INCONSISTENT:CLOSING")
        if i == 0:
            continue
        previous = closings.get(str(openings[i - 1].home_id))
        if (
            previous is None
            or opening.prev_closing_hash != previous.closing_hash()
            or opening.home_id != previous.successor_home_id
        ):
            raise _refuse("CHAIN_INCONSISTENT:LINK")
    homes = {str(o.home_id) for o in openings}
    if set(closings) - homes:
        raise _refuse("CHAIN_INCONSISTENT:CLOSING_WITHOUT_OPENING")
    by_home = {str(o.home_id): o for o in openings}
    for r in resolutions:
        holder = by_home.get(str(r.recorded_in))
        if holder is None or r.key not in {h.key for h in holder.carried}:
            raise _refuse("CHAIN_INCONSISTENT:RESOLUTION")
        carried = next(h for h in holder.carried if h.key == r.key)
        if carried.xtu != r.carried_xtu:
            raise _refuse("CHAIN_INCONSISTENT:RESOLUTION_AMOUNT")
    return ChainState(experiment_id, openings, closings, resolutions)


def _check_spec(st: ChainState, spec: ExperimentSpec) -> None:
    for opening in st.openings:
        if opening.spec_hash != spec.spec_hash():
            raise _refuse("SPEC_MISMATCH")
        if opening.balance() != spec.budget.experiment_cap_xtu:
            raise _refuse("CHAIN_BALANCE")


def _expected_cap(st: ChainState, spec: ExperimentSpec) -> int:
    """The open tip's cap: its opening's, plus what its resolutions released
    (minus what they settled above the hold), never below zero."""
    tip = st.tip
    assert tip is not None  # noqa: S101 (callers check)
    raw = spec.budget.experiment_cap_xtu - tip.prior_settled_xtu - tip.carried_xtu
    raw += sum(r.delta_xtu for r in st.resolved_in(tip.home_id))
    return max(0, raw)


def _caps(experiment_id: ExperimentId, cap: int, work: int) -> tuple[LedgerOp, ...]:
    return (
        SetBudgetCap(
            op="set_budget_cap", scope_type=EXPERIMENT, scope_id=str(experiment_id), cap=_xtu(cap)
        ),
        SetCounterCap(
            op="set_counter_cap",
            scope_type=_EXPERIMENT_COUNTER,
            scope_id=str(experiment_id),
            counter=CounterKind.WORK_UNITS,
            cap=work,
        ),
    )


def _ledger_caps(
    home: ExperimentHomePort, experiment_id: ExperimentId
) -> tuple[int | None, int | None]:
    usage = home.scope_usage(EXPERIMENT, str(experiment_id))
    counter = home.counter(EXPERIMENT, str(experiment_id), CounterKind.WORK_UNITS)
    return usage.cap_micro, None if counter is None else counter.cap


def _set_caps(home: ExperimentHomePort, experiment_id: ExperimentId, cap: int, work: int) -> None:
    """Absolute values; nothing is applied when the ledger already has them."""
    if _ledger_caps(home, experiment_id) != (cap, work):
        home.apply_owner(*_caps(experiment_id, cap, work))


def _tip_of(st: ChainState, home: ExperimentHomePort) -> OpeningStatement:
    tip = st.tip
    if tip is None:
        raise _refuse("NOT_STARTED")
    if tip.home_id != home.home_id:
        raise _refuse("NOT_THE_CURRENT_HOME")
    return tip


def _empty(home: ExperimentHomePort, experiment_id: ExperimentId) -> bool:
    usage = home.scope_usage(EXPERIMENT, str(experiment_id))
    return usage.cap_micro is None and usage.hold_micro == 0 and usage.spent_micro == 0


# ---------------------------------------------------------------- the steps


def start(chain: HomeChainPort, home: ExperimentHomePort, spec: ExperimentSpec) -> OpeningStatement:
    """The genesis opening: the first home gets the whole experiment cap."""
    st = state(chain, spec.experiment_id)
    opening = OpeningStatement(
        experiment_id=spec.experiment_id,
        spec_hash=spec.spec_hash(),
        home_id=home.home_id,
        index=0,
        prev_closing_hash=None,
        prior_settled_xtu=0,
        carried=(),
        cap_xtu=spec.budget.experiment_cap_xtu,
        deficit_xtu=0,
        work_units_cap=spec.budget.events_per_home,
    )
    if st.openings:
        if st.openings[0] != opening:
            raise _refuse("ALREADY_STARTED")
    elif not _empty(home, spec.experiment_id):
        raise _refuse("HOME_NOT_EMPTY")
    chain.record_opening(opening)
    repair(chain, home, spec)
    return opening


@dataclass(frozen=True)
class Admission:
    home_id: HomeId
    cap_xtu: int
    remaining_xtu: int


def admit_trial(chain: HomeChainPort, home: ExperimentHomePort, spec: ExperimentSpec) -> Admission:
    """Before each trial: the chain is intact, this home is its open tip, the
    ledger has exactly the chain's caps, and the rest covers a whole trial."""
    st = state(chain, spec.experiment_id)
    tip = _tip_of(st, home)
    if st.tip_closing is not None:
        raise _refuse("HOME_CLOSED")
    _check_spec(st, spec)
    cap = _expected_cap(st, spec)
    usage = home.scope_usage(EXPERIMENT, str(spec.experiment_id))
    counter = home.counter(EXPERIMENT, str(spec.experiment_id), CounterKind.WORK_UNITS)
    if usage.cap_micro != cap:
        raise _refuse("LEDGER_CAP_MISMATCH")
    if counter is None or counter.cap != tip.work_units_cap:
        raise _refuse("LEDGER_WORK_CAP_MISMATCH")
    if usage.blocked:
        raise _refuse("LEDGER_BLOCKED")
    remaining = cap - usage.hold_micro - usage.spent_micro
    if remaining < spec.budget.trial_cap_xtu:
        raise _refuse("REMAINING_BELOW_TRIAL_CAP")
    if counter.cap - counter.used < spec.budget.trial_event_cap:
        raise _refuse("WORK_BELOW_TRIAL_EVENTS")
    return Admission(home.home_id, cap, remaining)


def trial_ops(spec: ExperimentSpec, run_id: RunId) -> tuple[LedgerOp, ...]:
    """The trial's Run caps: ``trial_cap_xtu`` and ``trial_event_cap``."""
    return (
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=BudgetScopeType.RUN,
            scope_id=str(run_id),
            cap=_xtu(spec.budget.trial_cap_xtu),
        ),
        SetCounterCap(
            op="set_counter_cap",
            scope_type=BudgetScopeType.RUN,
            scope_id=str(run_id),
            counter=CounterKind.WORK_UNITS,
            cap=spec.budget.trial_event_cap,
        ),
    )


def close(
    chain: HomeChainPort,
    home: ExperimentHomePort,
    spec: ExperimentSpec,
    *,
    invalid_trial_xtu: int = 0,
    retry_xtu: int = 0,
) -> ClosingStatement:
    """Between trials: lower this home's caps to what it used (so it takes no
    new work, even at zero XTU), then record the closing and its successor."""
    st = state(chain, spec.experiment_id)
    tip = _tip_of(st, home)
    if st.tip_closing is not None:
        return st.tip_closing
    _check_spec(st, spec)
    exp = str(spec.experiment_id)
    usage = home.scope_usage(EXPERIMENT, exp)
    counter = home.counter(EXPERIMENT, exp, CounterKind.WORK_UNITS)
    if counter is None:
        raise _refuse("LEDGER_WORK_CAP_MISMATCH")
    if invalid_trial_xtu + retry_xtu > usage.spent_micro:
        raise ValueError("invalid and retried trials' costs are part of the settled total")
    _set_caps(home, spec.experiment_id, usage.spent_micro + usage.hold_micro, counter.used)
    usage = home.scope_usage(EXPERIMENT, exp)
    held = tuple(
        CarriedHold(origin_home_id=home.home_id, reservation_id=ReservationId(rid), xtu=amount)
        for rid, amount in home.held(EXPERIMENT, exp)
    )
    if sum(h.xtu for h in held) != usage.hold_micro:
        raise _refuse("LEDGER_HOLDS_DISAGREE")
    seq, head = home.journal_head()
    body = ClosingBody(
        experiment_id=spec.experiment_id,
        home_id=home.home_id,
        opening_hash=tip.opening_hash(),
        settled_xtu=usage.spent_micro,
        held=tuple(sorted(held, key=lambda h: h.key)),
        carried_unresolved=st.unresolved,
        invalid_trial_xtu=invalid_trial_xtu,
        retry_xtu=retry_xtu,
        journal_seq=seq,
        journal_head=head,
    )
    closing = ClosingStatement.of(body)
    chain.record_closing(closing)
    return closing


def open_successor(
    chain: HomeChainPort,
    new_home: ExperimentHomePort,
    spec: ExperimentSpec,
    *,
    previous: ExperimentHomePort,
) -> OpeningStatement:
    """The one successor of the closed tip: it carries the settled total and
    the unresolved holds as numbers and gets the rest of the experiment cap."""
    st = state(chain, spec.experiment_id)
    _check_spec(st, spec)
    existing = next((o for o in st.openings if o.home_id == new_home.home_id), None)
    if existing is not None:
        if existing is not st.tip:
            raise _refuse("NOT_THE_CURRENT_HOME")
        repair(chain, new_home, spec)
        return existing
    tip = st.tip
    closing = st.tip_closing
    if tip is None or closing is None:
        raise _refuse("PREVIOUS_NOT_CLOSED")
    if new_home.home_id != closing.successor_home_id:
        raise _refuse("NOT_THE_SUCCESSOR")
    if previous.home_id != closing.home_id:
        raise _refuse("NOT_THE_PREVIOUS_HOME")
    if tip.index + 2 > spec.budget.max_homes:
        raise _refuse("MAX_HOMES")
    prev_cap, prev_work = _ledger_caps(previous, spec.experiment_id)
    prev_counter = previous.counter(EXPERIMENT, str(spec.experiment_id), CounterKind.WORK_UNITS)
    if (
        prev_cap != closing.settled_xtu + closing.held_xtu
        or prev_counter is None
        or prev_work != prev_counter.used
    ):
        raise _refuse("PREVIOUS_NOT_CLOSED_IN_LEDGER")
    if not _empty(new_home, spec.experiment_id):
        raise _refuse("HOME_NOT_EMPTY")
    prior = (
        tip.prior_settled_xtu
        + closing.settled_xtu
        + sum(r.settled_xtu for r in st.resolved_in(tip.home_id))
    )
    carried = tuple(sorted((*closing.carried_unresolved, *closing.held), key=lambda h: h.key))
    raw = spec.budget.experiment_cap_xtu - prior - sum(h.xtu for h in carried)
    opening = OpeningStatement(
        experiment_id=spec.experiment_id,
        spec_hash=spec.spec_hash(),
        home_id=new_home.home_id,
        index=tip.index + 1,
        prev_closing_hash=closing.closing_hash(),
        prior_settled_xtu=prior,
        carried=carried,
        cap_xtu=max(0, raw),
        deficit_xtu=max(0, -raw),
        work_units_cap=spec.budget.events_per_home,
    )
    chain.record_opening(opening)
    repair(chain, new_home, spec)
    return opening


def resolve(
    chain: HomeChainPort,
    current: ExperimentHomePort,
    origin: ExperimentHomePort,
    hold: CarriedHold,
    spec: ExperimentSpec,
) -> CarriedHoldResolved:
    """A carried hold settled in its origin home (its ledger is the source),
    recorded once in the current home, whose cap rises by what was released."""
    st = state(chain, spec.experiment_id)
    tip = _tip_of(st, current)
    if st.tip_closing is not None:
        raise _refuse("HOME_CLOSED")
    _check_spec(st, spec)
    done = next((r for r in st.resolutions if r.key == hold.key), None)
    if done is not None:
        return done
    if hold not in st.unresolved:
        raise _refuse("NOT_CARRIED")
    if origin.home_id != hold.origin_home_id:
        raise _refuse("NOT_THE_ORIGIN_HOME")
    view = origin.reservation(str(hold.reservation_id))
    if view is None or view.state not in _SETTLED or view.hold_micro != 0 or not view.spent_known:
        raise _refuse("NOT_SETTLED")
    entries = origin.ledger_entries(str(hold.reservation_id))
    origin_ref = (
        "sha256:"
        + hashlib.sha256(b"".join(dump_json(e) for e in entries) + dump_json(view)).hexdigest()
    )
    record = CarriedHoldResolved(
        experiment_id=spec.experiment_id,
        recorded_in=tip.home_id,
        origin_home_id=hold.origin_home_id,
        reservation_id=hold.reservation_id,
        carried_xtu=hold.xtu,
        settled_xtu=view.spent_micro,
        origin_ref=origin_ref,
    )
    chain.record_resolution(record)
    repair(chain, current, spec)
    return record


def repair(chain: HomeChainPort, home: ExperimentHomePort, spec: ExperimentSpec) -> None:
    """Re-apply the open tip's caps from the chain (after a crash, or a cap
    changed outside the chain). Absolute values: repeating changes nothing."""
    st = state(chain, spec.experiment_id)
    tip = _tip_of(st, home)
    if st.tip_closing is not None:
        raise _refuse("HOME_CLOSED")
    _check_spec(st, spec)
    _set_caps(home, spec.experiment_id, _expected_cap(st, spec), tip.work_units_cap)


# ---------------------------------------------------------------- the check at the end


@dataclass(frozen=True)
class ResultsTotals:
    """The Results Store's copy of the experiment's totals (never the source)."""

    settled_xtu: int
    held_xtu: int


@dataclass(frozen=True)
class ChainCheck:
    settled_xtu: int
    unresolved_xtu: int
    consistent: bool
    completable: bool
    mismatches: tuple[str, ...]


def check_chain(
    chain: HomeChainPort,
    homes: Mapping[HomeId, ExperimentHomePort],
    spec: ExperimentSpec,
    *,
    results: ResultsTotals | None = None,
) -> ChainCheck:
    """Recompute from every home's ledger (the source) and compare with the
    chain and the Results Store's copy. Any difference: not completable."""
    st = state(chain, spec.experiment_id)
    _check_spec(st, spec)
    mismatches: list[str] = []
    exp = str(spec.experiment_id)
    usages = {}
    for opening in st.openings:
        home = homes.get(opening.home_id)
        if home is None:
            raise _refuse("HOME_MISSING")
        usages[str(opening.home_id)] = home.scope_usage(EXPERIMENT, exp)
    ledger_settled = sum(u.spent_micro for u in usages.values())
    ledger_held = sum(u.hold_micro for u in usages.values())
    chain_settled = sum(c.settled_xtu for c in st.closings.values())
    chain_settled += sum(r.settled_xtu for r in st.resolutions)
    chain_held = sum(h.xtu for h in st.unresolved)
    tip = st.tip
    if tip is not None and st.tip_closing is None:
        chain_settled += usages[str(tip.home_id)].spent_micro
        chain_held += usages[str(tip.home_id)].hold_micro
    if ledger_settled != chain_settled:
        mismatches.append("SETTLED_MISMATCH")
    if ledger_held != chain_held:
        mismatches.append("HELD_MISMATCH")
    consistent = not mismatches
    if results is not None and (results.settled_xtu, results.held_xtu) != (
        ledger_settled,
        ledger_held,
    ):
        mismatches.append("RESULTS_STORE_MISMATCH")
    if ledger_held:
        mismatches.append("UNRESOLVED_HOLDS")
    return ChainCheck(
        settled_xtu=ledger_settled,
        unresolved_xtu=ledger_held,
        consistent=consistent,
        completable=not mismatches,
        mismatches=tuple(mismatches),
    )
