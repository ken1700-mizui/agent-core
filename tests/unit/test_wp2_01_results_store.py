"""WP2-01: Experiment Registry and Results Store (separate SQLite, append only).

- ``freeze(spec)`` fixes an ExperimentSpec by its ``spec_hash``; a different
  spec under the same experiment ID is refused (D.V1; D 16.1: 100%).
- The development store accepts only DEV tasks (validation and holdout content
  never enters the public repository's stores).
- Records are checked against the frozen spec (rules hash, allowed invalid
  reasons, trial cap, retries) and chained by hash; any change of a stored row
  is found when the store is opened.
- The store is not reachable from the agent path (import contract).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.sqlite_results import (
    ResultsStoreIntegrityError,
    ResultsStoreRefusedError,
    SqliteResultsStore,
)
from agent_core.types import evaluation as e
from agent_core.types.ids import ReservationId
from support import builders as b
from support.evaluation_world import spec, trial


def _store(tmp_path: Path) -> SqliteResultsStore:
    return SqliteResultsStore.open(tmp_path / "results.db")


def _task(key: str, source: e.TaskSource = e.TaskSource.DEV) -> e.TaskSpec:
    return e.TaskSpec(
        task_key=key,
        version="1.0.0",
        source=source,
        goal_ref=b.digest(f"goal-{key}"),
        done_criteria=(b.digest(f"criterion-{key}"),),
        grader_ref=b.digest("grader"),
        repo_cluster="cluster_a",
        stratum="small",
    )


def _frozen(store: SqliteResultsStore, **overrides: Any) -> e.ExperimentSpec:
    tasks = [_task(f"dev_task_{i}") for i in range(3)]
    for t in tasks:
        store.register_task(t)
    s = spec(task_refs=tuple(sorted(t.task_ref() for t in tasks)), **overrides)
    store.freeze(s)
    return s


def _trial_for(s: e.ExperimentSpec, **overrides: Any) -> e.TrialRecord:
    values: dict[str, Any] = {
        "experiment_id": s.experiment_id,
        "task_ref": s.task_refs[0],
        "config_hash": s.config_hashes[0],
        "classification_rules_hash": s.classification.rules_hash(),
    }
    values.update(overrides)
    return trial(**values)


# ---------------------------------------------------------------- registry


def test_freeze_returns_the_spec_hash_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    assert store.freeze(s) == s.spec_hash()
    assert store.spec(s.experiment_id) == s


def test_a_changed_spec_is_refused_after_freezing(tmp_path: Path) -> None:
    """D 16.1: every change after freezing is refused (100%)."""
    store = _store(tmp_path)
    s = _frozen(store)
    changes: list[dict[str, Any]] = [
        {"seed": s.seed + 1},
        {"repeats": s.repeats + 1},
        {"grader_version": "1.0.1"},
        {"analysis_plan_ref": b.digest("other plan")},
        {"classification": s.classification.model_copy(update={"version": "2.0.0"})},
        {"budget": s.budget.model_copy(update={"max_retries": 5})},
    ]
    for change in changes:
        with pytest.raises(ResultsStoreRefusedError, match="frozen"):
            store.freeze(s.model_copy(update=change))
    assert store.spec(s.experiment_id) == s


def test_only_dev_tasks_enter_the_development_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for source in (e.TaskSource.VALIDATION, e.TaskSource.HOLDOUT):
        with pytest.raises(ResultsStoreRefusedError, match="DEV"):
            store.register_task(_task("x", source))


def test_a_spec_with_unregistered_tasks_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ResultsStoreRefusedError, match="task"):
        store.freeze(spec())


# ---------------------------------------------------------------- trials


def test_records_need_a_frozen_spec_and_its_rules(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ResultsStoreRefusedError, match="frozen"):
        store.append_trial(trial())
    s = _frozen(store)
    with pytest.raises(ResultsStoreRefusedError, match="rules"):
        store.append_trial(_trial_for(s, classification_rules_hash=b.digest("other rules")))
    with pytest.raises(ResultsStoreRefusedError, match="task"):
        store.append_trial(_trial_for(s, task_ref=b.digest("unknown task")))
    store.append_trial(_trial_for(s))
    assert len(store.trials(s.experiment_id)) == 1


def test_invalid_reasons_outside_the_rules_are_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    record = _trial_for(
        s,
        task_outcome=e.TaskOutcome.INVALID_ENV,
        invalid_reason=e.InvalidReason.RUNNER_FAILURE,  # not in the frozen list
        failure_attribution=e.FailureAttribution.ENV,
        run_completed=False,
    )
    with pytest.raises(ResultsStoreRefusedError, match="invalid_reason"):
        store.append_trial(record)


def test_a_trial_above_its_cap_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    over = e.TrialCost(
        settled_xtu=s.budget.trial_cap_xtu,
        held=(e.HeldCost(reservation_ref=b.new_id(ReservationId), xtu=1),),
    )
    with pytest.raises(ResultsStoreRefusedError, match="trial cap"):
        store.append_trial(_trial_for(s, cost=over))


def test_the_experiment_cap_counts_holds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store, budget=spec().budget.model_copy(update={"experiment_cap_xtu": 1_500}))
    hold = e.HeldCost(reservation_ref=b.new_id(ReservationId), xtu=900)
    store.append_trial(_trial_for(s, cost=e.TrialCost(settled_xtu=0, held=(hold,))))
    with pytest.raises(ResultsStoreRefusedError, match="experiment cap"):
        store.append_trial(_trial_for(s, cost=e.TrialCost(settled_xtu=700, held=())))


def test_a_trial_is_stored_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    record = _trial_for(s)
    store.append_trial(record)
    with pytest.raises(ResultsStoreRefusedError, match="once"):
        store.append_trial(record)


def test_retries_follow_the_rules(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    valid = _trial_for(s)
    store.append_trial(valid)
    with pytest.raises(ResultsStoreRefusedError, match="retry"):
        store.append_trial(_trial_for(s, retry_of=valid.trial_id))  # only invalid trials
    bad = _trial_for(
        s,
        task_outcome=e.TaskOutcome.INVALID_ENV,
        invalid_reason=e.InvalidReason.SANDBOX_HOST_FAILURE,
        failure_attribution=e.FailureAttribution.ENV,
        run_completed=False,
        repeat_index=1,
    )
    store.append_trial(bad)
    with pytest.raises(ResultsStoreRefusedError, match="retry"):
        store.append_trial(_trial_for(s, retry_of=bad.trial_id, repeat_index=0))  # other slot
    store.append_trial(_trial_for(s, retry_of=bad.trial_id, repeat_index=1))
    with pytest.raises(ResultsStoreRefusedError, match="retry"):
        store.append_trial(_trial_for(s, retry_of=bad.trial_id, repeat_index=1))  # once


def test_retry_count_is_capped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store, budget=spec().budget.model_copy(update={"max_retries": 1}))
    previous = None
    for i in range(3):  # the original, its one allowed retry, a second retry
        bad = _trial_for(
            s,
            task_outcome=e.TaskOutcome.INVALID_ENV,
            invalid_reason=e.InvalidReason.SANDBOX_HOST_FAILURE,
            failure_attribution=e.FailureAttribution.ENV,
            run_completed=False,
            retry_of=previous,
        )
        if i == 2:
            with pytest.raises(ResultsStoreRefusedError, match="max_retries"):
                store.append_trial(bad)
            return
        store.append_trial(bad)
        previous = bad.trial_id


def test_reconciliations_are_checked_and_stored_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    hold = e.HeldCost(reservation_ref=b.new_id(ReservationId), xtu=30)
    record = _trial_for(s, cost=e.TrialCost(settled_xtu=10, held=(hold,)))
    store.append_trial(record)
    rec = e.CostReconciliation(
        trial_id=record.trial_id,
        reservation_ref=hold.reservation_ref,
        settled_xtu=25,
        released_xtu=5,
        evidence_ref=b.digest("ledger"),
    )
    store.append_reconciliation(rec)
    with pytest.raises(ResultsStoreRefusedError, match="once"):
        store.append_reconciliation(rec)
    summary = store.cost_summary(s.experiment_id)
    assert (summary.settled_total, summary.held_total, summary.released_total) == (35, 0, 5)


def test_scores_analysis_and_decisions_are_appended(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    record = _trial_for(s)
    store.append_trial(record)
    store.append_score(
        e.ScoreRecord(
            trial_id=record.trial_id,
            experiment_id=s.experiment_id,
            grader_version=s.grader_version,
            grading_status=e.GradingStatus.GRADED,
            task_outcome=e.TaskOutcome.SUCCESS,
            points=1,
            max_points=1,
            evidence_ref=b.digest("score"),
        )
    )
    with pytest.raises(ResultsStoreRefusedError, match="grader"):
        store.append_score(
            e.ScoreRecord(
                trial_id=record.trial_id,
                experiment_id=s.experiment_id,
                grader_version="9.9.9",
                grading_status=e.GradingStatus.GRADED,
                task_outcome=e.TaskOutcome.SUCCESS,
                points=1,
                max_points=1,
                evidence_ref=b.digest("score"),
            )
        )


# ---------------------------------------------------------------- integrity


def test_reopening_keeps_every_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    store.append_trial(_trial_for(s))
    head = store.head()
    store.close()
    again = _store(tmp_path)
    assert again.head() == head
    assert len(again.trials(s.experiment_id)) == 1
    assert again.spec(s.experiment_id) == s


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE entries SET body = replace(body, '\"settled_xtu\":100', '\"settled_xtu\":1')",
        "DELETE FROM entries WHERE seq = (SELECT max(seq) FROM entries)",
        "DELETE FROM entries WHERE seq = 2",
        "UPDATE entries SET hash = 'sha256:' || substr(hash, 8, 63) || '0' WHERE seq = 1",
    ],
)
def test_a_changed_store_is_refused_when_opened(tmp_path: Path, damage: str) -> None:
    store = _store(tmp_path)
    s = _frozen(store)
    store.append_trial(_trial_for(s))
    store.close()
    conn = sqlite3.connect(tmp_path / "results.db")
    try:
        conn.execute("DROP TRIGGER IF EXISTS entries_no_update")
        conn.execute("DROP TRIGGER IF EXISTS entries_no_delete")
        conn.execute(damage)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ResultsStoreIntegrityError):
        _store(tmp_path)


def test_rows_cannot_be_changed_in_place(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _frozen(store)
    store.close()
    conn = sqlite3.connect(tmp_path / "results.db")
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE entries SET body = '{}'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM entries")
    finally:
        conn.close()


def test_the_store_is_a_separate_file_from_the_journal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _frozen(store)
    store.close()
    conn = sqlite3.connect(tmp_path / "results.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "events" not in tables
    assert {"entries", "meta"} <= tables
