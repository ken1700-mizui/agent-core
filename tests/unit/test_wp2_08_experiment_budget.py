"""WP2-08: the experiment budget across homes (contract 7.1-7.8, D19).

Enforcement is each home's ledger (one transaction checks every scope,
D.V10): the EXPERIMENT budget cap and, since WP2-08, an EXPERIMENT work-unit
cap (so a closed home refuses even zero-XTU trials). The home chain store
holds the signed closing, opening and resolution records with UNIQUE
constraints and one runner's lock; caps are always set to absolute values
computed from the chain, so a re-run after a crash never adds budget. The
Results Store is a copy that is checked, never the enforcement.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_home_chain import (
    ChainBusyError,
    ChainConflictError,
    ChainIntegrityError,
    HomeChainStore,
)
from agent_core.runtime import experiment_budget as eb
from agent_core.types.cost import BudgetScopeType, CounterKind
from agent_core.types.errors import (
    BudgetCapMissingError,
    BudgetExceededError,
    CounterCapExceededError,
)
from agent_core.types.experiment_budget import CarriedHold
from agent_core.types.ledger import SetCounterCap
from support import experiment_world as ew
from support import ledger_world as lw


@pytest.fixture
def sp() -> Any:
    return ew.spec()


def _opened(tmp_path: Path, sp: Any) -> tuple[HomeChainStore, ew.Home]:
    chain = ew.chain(tmp_path)
    h1 = ew.home(tmp_path, "h1")
    eb.start(chain, h1.home, sp)
    return chain, h1


def _successor(tmp_path: Path, closing: Any, name: str = "h2") -> ew.Home:
    return ew.home(tmp_path, name, home_id=closing.successor_home_id)


# ---------------------------------------------------------------- one home


def test_a_home_without_an_opening_cannot_use_the_experiment(tmp_path: Path, sp: Any) -> None:
    h = ew.home(tmp_path, "h")
    with pytest.raises(BudgetCapMissingError):
        ew.trial(h, sp)


def test_the_first_home_gets_the_experiment_cap(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    assert ew.usage(h1, sp).cap_micro == 10_000
    assert ew.work(h1, sp).cap == sp.budget.events_per_home
    assert eb.admit_trial(chain, h1.home, sp).remaining_xtu == 10_000
    with pytest.raises(eb.ExperimentBudgetError):
        eb.start(chain, ew.home(tmp_path, "again").home, sp)  # one genesis per experiment


def test_the_experiment_cap_is_enforced_by_the_ledger(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    for _ in range(4):
        ew.trial(h1, sp, reserve=2_000, cost=None)  # 8,000 held
    assert eb.admit_trial(chain, h1.home, sp).remaining_xtu == 2_000  # one whole trial left
    ew.trial(h1, sp, reserve=1_500, cost=None)  # 9,500 held
    with pytest.raises(eb.ExperimentBudgetError, match="REMAINING_BELOW_TRIAL_CAP"):
        eb.admit_trial(chain, h1.home, sp)
    ew.trial(h1, sp, reserve=500, cost=None)  # exactly 10,000 (the ledger allows it)
    with pytest.raises(BudgetExceededError, match="EXPERIMENT"):
        ew.trial(h1, sp, reserve=300, cost=None)  # the ledger refuses, admission or not


def test_the_trial_cap_is_enforced_by_the_ledger(tmp_path: Path, sp: Any) -> None:
    _, h1 = _opened(tmp_path, sp)
    with pytest.raises(BudgetExceededError, match="RUN"):
        ew.trial(h1, sp, reserve=2_001, cost=None)


def test_the_experiment_work_cap_is_a_non_monetary_limit(tmp_path: Path) -> None:
    sp = ew.spec(events_per_home=150, trial_events=50, retries=0, max_homes=4)
    _, h1 = _opened(tmp_path, sp)
    for _ in range(150):  # one work unit each; zero-XTU trials still count
        ew.trial(h1, sp, reserve=0, cost=None)
    with pytest.raises(CounterCapExceededError, match="WORK_UNITS"):
        ew.trial(h1, sp, reserve=0, cost=None)
    assert ew.usage(h1, sp).spent_micro == 0


def test_an_experiment_grant_without_the_work_cap_is_refused(tmp_path: Path, sp: Any) -> None:
    _, h1 = _opened(tmp_path, sp)
    st = h1.st
    run = lw.run_for_task(st)
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step, experiment=b_other())
    lw.caps(st, scopes, micro=100_000, skip_experiment_counter=True)
    attempt = lw.fixed_attempt(st, step, scopes, costly=True)
    with pytest.raises(BudgetCapMissingError, match="EXPERIMENT_WORK_UNITS"):
        lw.reserve(st, attempt, 300)


def b_other() -> Any:
    from agent_core.types.ids import ExperimentId
    from support import builders as b

    return b.new_id(ExperimentId)


# ---------------------------------------------------------------- closing and opening


def test_closing_refuses_new_trials_even_at_zero_cost(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    ew.trial(h1, sp, cost=300)
    eb.close(chain, h1.home, sp)
    assert ew.usage(h1, sp).cap_micro == 300  # spent + hold
    with pytest.raises(CounterCapExceededError):
        ew.trial(h1, sp, reserve=0, cost=None)
    with pytest.raises(eb.ExperimentBudgetError, match="HOME_CLOSED"):
        eb.admit_trial(chain, h1.home, sp)


def test_switching_homes_never_raises_the_experiment_cap(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    ew.trial(h1, sp, cost=300)  # settled 300
    ew.trial(h1, sp, reserve=1_000, cost=None)  # held 1,000
    closing = eb.close(chain, h1.home, sp, invalid_trial_xtu=300, retry_xtu=0)
    h2 = _successor(tmp_path, closing)
    opening = eb.open_successor(chain, h2.home, sp, previous=h1.home)
    assert (opening.prior_settled_xtu, opening.carried_xtu, opening.cap_xtu) == (300, 1_000, 8_700)
    assert opening.prior_settled_xtu + opening.carried_xtu + opening.cap_xtu == 10_000
    assert ew.usage(h2, sp).cap_micro == 8_700
    assert ew.usage(h2, sp).hold_micro == 0  # the carried hold is a number, not a reservation
    assert closing.invalid_trial_xtu == 300  # counted inside the settled total, not freed


def test_invalid_and_retried_trials_are_still_counted(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    for _ in range(3):  # a valid trial, one classified invalid, and its retry
        ew.trial(h1, sp, cost=300)
    with pytest.raises(ValueError, match="part of the settled total"):
        eb.close(chain, h1.home, sp, invalid_trial_xtu=600, retry_xtu=600)
    closing = eb.close(chain, h1.home, sp, invalid_trial_xtu=300, retry_xtu=300)
    assert closing.settled_xtu == 900  # nothing is given back for the invalid trial
    h2 = _successor(tmp_path, closing)
    assert eb.open_successor(chain, h2.home, sp, previous=h1.home).cap_xtu == 9_100


def test_only_one_successor(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    closing = eb.close(chain, h1.home, sp)
    stranger = ew.home(tmp_path, "stranger")
    with pytest.raises(eb.ExperimentBudgetError, match="NOT_THE_SUCCESSOR"):
        eb.open_successor(chain, stranger.home, sp, previous=h1.home)
    h2 = _successor(tmp_path, closing)
    first = eb.open_successor(chain, h2.home, sp, previous=h1.home)
    assert eb.open_successor(chain, h2.home, sp, previous=h1.home) == first  # idempotent
    with pytest.raises(ChainConflictError):
        chain.record_opening(first.model_copy(update={"cap_xtu": first.cap_xtu - 1}))


def test_the_number_of_homes_is_bounded(tmp_path: Path) -> None:
    sp = ew.spec(max_homes=2, events_per_home=1_000, trial_events=50)
    chain, h1 = _opened(tmp_path, sp)
    h2 = _successor(tmp_path, eb.close(chain, h1.home, sp))
    eb.open_successor(chain, h2.home, sp, previous=h1.home)
    h3 = _successor(tmp_path, eb.close(chain, h2.home, sp), "h3")
    with pytest.raises(eb.ExperimentBudgetError, match="MAX_HOMES"):
        eb.open_successor(chain, h3.home, sp, previous=h2.home)


# ---------------------------------------------------------------- carried holds


def _with_carried(tmp_path: Path, sp: Any) -> tuple[HomeChainStore, ew.Home, ew.Home, Any]:
    chain, h1 = _opened(tmp_path, sp)
    held = ew.trial(h1, sp, reserve=1_000, cost=300, final=False)  # UNKNOWN, hold 1,000
    closing = eb.close(chain, h1.home, sp)
    h2 = _successor(tmp_path, closing)
    eb.open_successor(chain, h2.home, sp, previous=h1.home)
    return chain, h1, h2, held


def test_a_carried_hold_is_resolved_once_and_never_subtracted_twice(
    tmp_path: Path, sp: Any
) -> None:
    chain, h1, h2, held = _with_carried(tmp_path, sp)
    assert ew.usage(h2, sp).cap_micro == 9_000
    hold = eb.state(chain, sp.experiment_id).unresolved[0]
    ew.reconcile(h1, held, 400)  # in the origin home (its ledger is the source)
    record = eb.resolve(chain, h2.home, h1.home, hold, sp)
    assert (record.settled_xtu, record.delta_xtu) == (400, 600)
    assert ew.usage(h2, sp).cap_micro == 9_600  # raised by the released 600 only
    assert eb.resolve(chain, h2.home, h1.home, hold, sp) == record  # again: no change
    assert ew.usage(h2, sp).cap_micro == 9_600
    with pytest.raises(ChainConflictError):
        chain.record_resolution(record.model_copy(update={"settled_xtu": 100}))
    check = eb.check_chain(chain, {h1.home_id: h1.home, h2.home_id: h2.home}, sp)
    assert (check.settled_xtu, check.unresolved_xtu, check.completable) == (400, 0, True)


def test_an_unsettled_hold_cannot_be_resolved(tmp_path: Path, sp: Any) -> None:
    chain, h1, h2, _ = _with_carried(tmp_path, sp)
    hold = eb.state(chain, sp.experiment_id).unresolved[0]
    with pytest.raises(eb.ExperimentBudgetError, match="NOT_SETTLED"):
        eb.resolve(chain, h2.home, h1.home, hold, sp)
    forged = CarriedHold(origin_home_id=h1.home_id, reservation_id=hold.reservation_id, xtu=5)
    with pytest.raises(eb.ExperimentBudgetError, match="NOT_CARRIED"):
        eb.resolve(chain, h2.home, h1.home, forged, sp)


def test_repeated_migration_keeps_the_origin_home(tmp_path: Path, sp: Any) -> None:
    chain, h1, h2, held = _with_carried(tmp_path, sp)
    ew.trial(h2, sp, cost=300)  # the costly tool's manifest cost
    h3 = _successor(tmp_path, eb.close(chain, h2.home, sp), "h3")
    opening = eb.open_successor(chain, h3.home, sp, previous=h2.home)
    assert [c.origin_home_id for c in opening.carried] == [h1.home_id]
    assert (opening.prior_settled_xtu, opening.carried_xtu, opening.cap_xtu) == (300, 1_000, 8_700)
    ew.reconcile(h1, held, 1_000)
    eb.resolve(chain, h3.home, h1.home, opening.carried[0], sp)
    assert ew.usage(h3, sp).cap_micro == 8_700  # settled in full: nothing released
    homes = {h.home_id: h.home for h in (h1, h2, h3)}
    check = eb.check_chain(chain, homes, sp)
    assert (check.settled_xtu, check.unresolved_xtu, check.consistent) == (1_300, 0, True)


def test_an_unrecorded_settlement_in_an_old_home_is_detected(tmp_path: Path, sp: Any) -> None:
    chain, h1, h2, held = _with_carried(tmp_path, sp)
    ew.reconcile(h1, held, 400)  # settled in the old home, no resolution recorded
    check = eb.check_chain(chain, {h1.home_id: h1.home, h2.home_id: h2.home}, sp)
    assert not check.consistent
    assert not check.completable


def test_the_results_store_copy_is_checked_not_trusted(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    ew.trial(h1, sp, cost=300)
    homes = {h1.home_id: h1.home}
    good = eb.check_chain(chain, homes, sp, results=eb.ResultsTotals(settled_xtu=300, held_xtu=0))
    assert good.completable
    bad = eb.check_chain(chain, homes, sp, results=eb.ResultsTotals(settled_xtu=0, held_xtu=0))
    assert not bad.completable
    assert "RESULTS_STORE_MISMATCH" in bad.mismatches
    assert ew.usage(h1, sp).cap_micro == 10_000  # the copy changes nothing in the ledger


# ---------------------------------------------------------------- crashes


class _CrashError(RuntimeError):
    pass


def _crash_on(monkeypatch: pytest.MonkeyPatch, target: Any, name: str) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise _CrashError(name)

    monkeypatch.setattr(target, name, boom)


def test_a_crash_after_lowering_before_the_closing_record(
    tmp_path: Path, sp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, h1 = _opened(tmp_path, sp)
    ew.trial(h1, sp, cost=300)
    with monkeypatch.context() as m:
        _crash_on(m, chain, "record_closing")
        with pytest.raises(_CrashError):
            eb.close(chain, h1.home, sp)
    assert ew.usage(h1, sp).cap_micro == 300  # already closed for new work
    closing = eb.close(chain, h1.home, sp)  # the re-run gives the closing
    h2 = _successor(tmp_path, closing)
    assert eb.open_successor(chain, h2.home, sp, previous=h1.home).cap_xtu == 9_700


def test_a_crash_between_closing_and_opening_gives_one_successor(
    tmp_path: Path, sp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, h1 = _opened(tmp_path, sp)
    closing = eb.close(chain, h1.home, sp)
    h2 = _successor(tmp_path, closing)
    with monkeypatch.context() as m:
        _crash_on(m, chain, "record_opening")
        with pytest.raises(_CrashError):
            eb.open_successor(chain, h2.home, sp, previous=h1.home)
    assert ew.usage(h2, sp).cap_micro is None  # nothing granted yet
    assert eb.close(chain, h1.home, sp) == closing  # same closing, same successor
    eb.open_successor(chain, h2.home, sp, previous=h1.home)
    assert len(eb.state(chain, sp.experiment_id).openings) == 2


def test_a_crash_after_the_opening_record_is_repaired(
    tmp_path: Path, sp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, h1 = _opened(tmp_path, sp)
    closing = eb.close(chain, h1.home, sp)
    h2 = _successor(tmp_path, closing)
    with monkeypatch.context() as m:
        _crash_on(m, h2.home, "apply_owner")
        with pytest.raises(_CrashError):
            eb.open_successor(chain, h2.home, sp, previous=h1.home)
    with pytest.raises(eb.ExperimentBudgetError, match="LEDGER_CAP_MISMATCH"):
        eb.admit_trial(chain, h2.home, sp)
    eb.repair(chain, h2.home, sp)
    assert eb.admit_trial(chain, h2.home, sp).remaining_xtu == 10_000
    eb.repair(chain, h2.home, sp)  # repeating changes nothing
    assert ew.usage(h2, sp).cap_micro == 10_000


def test_a_crash_after_the_resolution_record_is_repaired_once(
    tmp_path: Path, sp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, h1, h2, held = _with_carried(tmp_path, sp)
    hold = eb.state(chain, sp.experiment_id).unresolved[0]
    ew.reconcile(h1, held, 400)
    with monkeypatch.context() as m:
        _crash_on(m, h2.home, "apply_owner")
        with pytest.raises(_CrashError):
            eb.resolve(chain, h2.home, h1.home, hold, sp)
    assert ew.usage(h2, sp).cap_micro == 9_000
    with pytest.raises(eb.ExperimentBudgetError, match="LEDGER_CAP_MISMATCH"):
        eb.admit_trial(chain, h2.home, sp)
    eb.repair(chain, h2.home, sp)
    eb.resolve(chain, h2.home, h1.home, hold, sp)  # the re-run is a no-op
    eb.repair(chain, h2.home, sp)
    assert ew.usage(h2, sp).cap_micro == 9_600


# ---------------------------------------------------------------- fail closed


def test_a_cap_raised_outside_the_chain_refuses_new_trials(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    ew.raise_cap_directly(h1, sp, 50_000)
    with pytest.raises(eb.ExperimentBudgetError, match="LEDGER_CAP_MISMATCH"):
        eb.admit_trial(chain, h1.home, sp)
    eb.repair(chain, h1.home, sp)  # lowered back to the chain's value
    assert ew.usage(h1, sp).cap_micro == 10_000


def test_a_raised_work_cap_is_detected(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    h1.st.owner(
        SetCounterCap(
            op="set_counter_cap",
            scope_type=BudgetScopeType.EXPERIMENT,
            scope_id=str(sp.experiment_id),
            counter=CounterKind.WORK_UNITS,
            cap=10**6,
        )
    )
    with pytest.raises(eb.ExperimentBudgetError, match="LEDGER_WORK_CAP_MISMATCH"):
        eb.admit_trial(chain, h1.home, sp)


def test_a_tampered_chain_refuses_new_trials(tmp_path: Path, sp: Any) -> None:
    chain, h1 = _opened(tmp_path, sp)
    chain.close()
    db = sqlite3.connect(tmp_path / "chain" / "experiment.db")
    db.execute("UPDATE openings SET body = replace(body, '10000', '90000')")
    db.commit()
    db.close()
    reopened = ew.chain(tmp_path)
    with pytest.raises(ChainIntegrityError):
        eb.admit_trial(reopened, h1.home, sp)


def test_the_chain_has_one_runner(tmp_path: Path, sp: Any) -> None:
    chain = ew.chain(tmp_path)
    with pytest.raises(ChainBusyError):
        ew.chain(tmp_path)
    chain.close()
    ew.chain(tmp_path).close()


def test_the_chain_file_is_private(tmp_path: Path, sp: Any) -> None:
    chain, _ = _opened(tmp_path, sp)
    mode = (tmp_path / "chain" / "experiment.db").stat().st_mode & 0o777
    assert mode == 0o600
    chain.close()


# ---------------------------------------------------------------- compatibility


def test_no_stored_format_changes() -> None:
    """WP2-08 widens the owner operation SetCounterCap (EXPERIMENT) and adds a
    read; the journal and ledger schema stay at the Batch 1 version, so every
    existing home opens unchanged (its schema objects are checked on open)."""
    from agent_core.adapters.sqlite_journal import JOURNAL_SCHEMA_VERSION

    assert JOURNAL_SCHEMA_VERSION == 8


def test_runs_outside_an_experiment_need_no_experiment_cap(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)  # no experiment: Phase 1 behaviour
    lw.caps(st, scopes)
    lw.reserve(st, lw.fixed_attempt(st, step, scopes), 1_000)
    assert st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id)).hold_micro == 1_000
