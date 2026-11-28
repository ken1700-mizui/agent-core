"""Homes of one experiment for WP2-08: each home is its own Journal and ledger
(``ledger_world.Setup``); a trial is one costly synthetic Attempt in a new Task
and Run whose grant names the experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_core.adapters.experiment_home import JournalExperimentHome
from agent_core.adapters.sqlite_home_chain import HomeChainStore
from agent_core.runtime import experiment_budget as eb
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.types.cost import BudgetScopeType, CounterKind
from agent_core.types.evaluation import ExperimentBudget, ExperimentSpec
from agent_core.types.ids import ExperimentId, HomeId
from agent_core.types.ledger import Reconcile, SetBudgetCap
from agent_core.types.model import CostStatus, Usage, UsageStatus
from support import builders as b
from support import ledger_world as lw
from support import safety_world as s

CHAIN_KEY = b"wp2-08 synthetic owner chain key, test only"


def spec(
    *,
    cap: int = 10_000,
    trial: int = 2_000,
    max_homes: int = 4,
    events_per_home: int = 1_000,
    trial_events: int = 50,
    retries: int = 2,
) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=b.new_id(ExperimentId),
        task_refs=tuple(sorted(b.digest(f"task-{i}") for i in range(3))),
        config_hashes=tuple(sorted(b.digest(f"config-{i}") for i in range(2))),
        repeats=1,
        seed=1,
        eval_set_version="1.0.0",
        grader_version="1.0.0",
        analysis_plan_ref=b.digest("plan"),
        classification=_classification(),
        budget=ExperimentBudget(
            experiment_cap_xtu=cap,
            trial_cap_xtu=trial,
            trial_event_cap=trial_events,
            events_per_home=events_per_home,
            max_homes=max_homes,
            max_retries=retries,
        ),
    )


def _classification() -> Any:
    from support import evaluation_world as ev

    return ev.rules()


@dataclass
class Home:
    st: lw.Setup
    home: JournalExperimentHome
    trials: list[Any] = field(default_factory=list)

    @property
    def home_id(self) -> HomeId:
        return self.home.home_id


def home(tmp_path: Path, name: str, home_id: HomeId | None = None) -> Home:
    (tmp_path / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    st = lw.setup(tmp_path / name)
    hid = home_id if home_id is not None else b.new_id(HomeId)
    return Home(st, JournalExperimentHome(st.journal, hid, b.TENANT, st.owner))


def chain(tmp_path: Path) -> HomeChainStore:
    return HomeChainStore.open(tmp_path / "chain" / "experiment.db", key=CHAIN_KEY)


def _usage(micro: int) -> Usage:
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cost=lw.xtu(micro),
        cost_status=CostStatus.FINAL,
        synthetic=True,
    )


def trial(
    h: Home,
    sp: ExperimentSpec,
    *,
    reserve: int = 1_000,
    cost: int | None = 300,
    final: bool = True,
) -> Any:
    """One trial: a new Task and Run with the experiment's grant and trial caps,
    one costly Attempt reserving ``reserve``; ``cost`` is its reported usage
    (final or not: a non-final report leaves a hold), ``None`` stops at the
    reservation (it stays held)."""
    st = h.st
    run = lw.run_for_task(st)
    step = lw.step_in(st, run)
    scopes = lw.scopes_of(st.sm, step, experiment=sp.experiment_id)
    lw.caps(st, scopes, micro=100_000, skip_experiment=True)
    st.owner(*eb.trial_ops(sp, run.run_id))
    attempt = lw.fixed_attempt(st, step, scopes, costly=reserve > 0)
    attempt = lw.reserve(st, attempt, reserve)
    if cost is None:
        h.trials.append(attempt)
        return attempt
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, attempt)))
    gate = lw.gate(st)
    started, _ = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))
    gate.finish(started, SyntheticResult(outcome="COMPLETED", usage=_usage(cost), final=final))
    h.trials.append(started)
    return started


def reconcile(h: Home, attempt: Any, micro: int) -> None:
    h.st.owner(
        Reconcile(
            op="reconcile",
            reservation_id=attempt.reservation_id,
            actual=lw.xtu(micro),
            evidence_ref=b.digest("synthetic-reconciliation"),
        )
    )


def usage(h: Home, sp: ExperimentSpec) -> Any:
    return h.st.journal.scope_usage(BudgetScopeType.EXPERIMENT, str(sp.experiment_id))


def work(h: Home, sp: ExperimentSpec) -> Any:
    return h.st.journal.counter(
        BudgetScopeType.EXPERIMENT, str(sp.experiment_id), CounterKind.WORK_UNITS
    )


def raise_cap_directly(h: Home, sp: ExperimentSpec, micro: int) -> None:
    h.st.owner(
        SetBudgetCap(
            op="set_budget_cap",
            scope_type=BudgetScopeType.EXPERIMENT,
            scope_id=str(sp.experiment_id),
            cap=lw.xtu(micro),
        )
    )


__all__ = ["CHAIN_KEY", "Home", "chain", "home", "reconcile", "s", "spec", "trial", "usage"]
