"""Experiment Registry and Results Store (WP2-01; D 3, contract 1, 7.1, 8).

A separate SQLite file (never the agent's journal), written only by the
evaluation side. The agent path cannot import it (import contract).

- Append only: one ``entries`` table; triggers refuse UPDATE and DELETE.
- Hash chain: each entry's hash covers its kind, body and the previous hash.
  The head is also kept in ``meta``. On open, every entry is checked again (chain,
  strict parsing, the same rules as on append); any mismatch refuses the file.
  This finds changes; it does not prevent them (a writer with file access can
  rebuild a consistent chain: the ledgers stay the source of truth, 7.1).
- Registry: ``freeze(spec)`` fixes an ExperimentSpec by its ``spec_hash``; the
  same experiment ID with another spec is refused (D.V1).
- Development store: only DEV tasks (validation and holdout never enter it).
- Records are checked against the frozen spec: rules hash, tasks, configs,
  allowed invalid reasons, trial and experiment caps (holds included),
  retries; reconciliations account for whole holds, once (contract 2.5).

Cost values here are copies for analysis; budgets are enforced by the ledgers.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from agent_core.types import evaluation as e
from agent_core.types.errors import AgentCoreError, RecordValidationError
from agent_core.types.ids import ExperimentId, TrialId
from agent_core.types.serialization import dump_json, load_json
from agent_core.types.values import Digest

APPLICATION_ID = 0x41435253  # "ACRS"
FORMAT_VERSION = 1
_DOMAIN = b"agent-core/results-store/v1\x00"

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
    """CREATE TABLE entries (
        seq INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        body TEXT NOT NULL,
        prev_hash TEXT,
        hash TEXT NOT NULL UNIQUE
    ) STRICT""",
    """CREATE TRIGGER entries_no_update BEFORE UPDATE ON entries
       BEGIN SELECT RAISE(ABORT, 'results entries are append only'); END""",
    """CREATE TRIGGER entries_no_delete BEFORE DELETE ON entries
       BEGIN SELECT RAISE(ABORT, 'results entries are append only'); END""",
)

_KINDS: dict[str, type[BaseModel]] = {
    "task": e.TaskSpec,
    "spec": e.ExperimentSpec,
    "trial": e.TrialRecord,
    "score": e.ScoreRecord,
    "reconciliation": e.CostReconciliation,
    "analysis": e.AnalysisResult,
    "decision": e.Decision,
}


class ResultsStoreRefusedError(AgentCoreError):
    """A record breaks the frozen spec or the store's rules. Nothing was written."""

    code = "RESULTS_REFUSED"


class ResultsStoreIntegrityError(AgentCoreError):
    """The stored entries do not form a valid chain of valid records."""

    code = "RESULTS_INTEGRITY"
    fatal = True


def _entry_hash(kind: str, body: str, prev: str | None) -> str:
    data = _DOMAIN + kind.encode() + b"\x00" + body.encode() + b"\x00" + (prev or "").encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class _Experiment:
    spec: e.ExperimentSpec
    trials: dict[TrialId, e.TrialRecord] = field(default_factory=dict)
    reconciliations: list[e.CostReconciliation] = field(default_factory=list)
    scores: list[e.ScoreRecord] = field(default_factory=list)
    retried: set[TrialId] = field(default_factory=set)
    retries: int = 0


class _Registry:
    """The in-memory view and the rules (the same on append and on open)."""

    def __init__(self) -> None:
        self.tasks: dict[str, e.TaskSpec] = {}
        self.experiments: dict[ExperimentId, _Experiment] = {}

    def experiment(self, experiment_id: ExperimentId) -> _Experiment:
        found = self.experiments.get(experiment_id)
        if found is None:
            raise ResultsStoreRefusedError("the experiment has no frozen spec")
        return found

    def check(self, kind: str, record: BaseModel) -> bool:
        """True when the record is new; False when it is already stored (same
        task or same spec). Raises when it breaks a rule."""
        if isinstance(record, e.TaskSpec):
            if record.source is not e.TaskSource.DEV:
                raise ResultsStoreRefusedError("only DEV tasks enter the development store")
            return record.task_ref() not in self.tasks
        if isinstance(record, e.ExperimentSpec):
            return self._check_spec(record)
        if isinstance(record, e.TrialRecord):
            self._check_trial(record)
        elif isinstance(record, e.CostReconciliation):
            self._check_reconciliation(record)
        elif isinstance(record, e.ScoreRecord):
            exp = self.experiment(record.experiment_id)
            if record.trial_id not in exp.trials:
                raise ResultsStoreRefusedError("a score needs its trial")
            if record.grader_version != exp.spec.grader_version:
                raise ResultsStoreRefusedError("the score's grader version is not the frozen one")
        elif isinstance(record, e.AnalysisResult):
            exp = self.experiment(record.experiment_id)
            if record.spec_hash != exp.spec.spec_hash():
                raise ResultsStoreRefusedError(
                    "the analysis names another spec than the frozen one"
                )
        elif isinstance(record, e.Decision):
            self.experiment(record.experiment_id)
        else:  # pragma: no cover - the kinds are fixed above
            raise ResultsStoreRefusedError(f"unknown record kind {kind}")
        return True

    def _check_spec(self, spec: e.ExperimentSpec) -> bool:
        existing = self.experiments.get(spec.experiment_id)
        if existing is not None:
            if existing.spec.spec_hash() != spec.spec_hash():
                raise ResultsStoreRefusedError("the experiment is frozen with another spec")
            return False
        missing = [t for t in spec.task_refs if t not in self.tasks]
        if missing:
            raise ResultsStoreRefusedError("the spec names a task that is not registered")
        return True

    def _check_trial(self, t: e.TrialRecord) -> None:
        exp = self.experiment(t.experiment_id)
        s = exp.spec
        if t.classification_rules_hash != s.classification.rules_hash():
            raise ResultsStoreRefusedError(
                "the trial's classification rules are not the frozen ones"
            )
        if t.task_ref not in s.task_refs:
            raise ResultsStoreRefusedError("the trial's task is not in the frozen spec")
        if t.config_hash not in s.config_hashes:
            raise ResultsStoreRefusedError("the trial's config is not in the frozen spec")
        if (
            t.invalid_reason is not None
            and t.invalid_reason not in s.classification.allowed_invalid_reasons
        ):
            raise ResultsStoreRefusedError("the invalid_reason is not allowed by the frozen rules")
        if any(t.trial_id in x.trials for x in self.experiments.values()):
            raise ResultsStoreRefusedError("a trial is stored once")
        if t.cost.upper_bound > s.budget.trial_cap_xtu:
            raise ResultsStoreRefusedError("the trial's cost is above the trial cap")
        current = e.cost_summary(exp.trials.values(), exp.reconciliations)
        if current.upper_bound + t.cost.upper_bound > s.budget.experiment_cap_xtu:
            raise ResultsStoreRefusedError("the trial's cost is above the experiment cap")
        if t.retry_of is not None:
            original = exp.trials.get(t.retry_of)
            if original is None or original.validity is not e.Validity.INVALID:
                raise ResultsStoreRefusedError(
                    "a retry repeats an INVALID_ENV trial of the experiment"
                )
            slot = (original.task_ref, original.config_hash, original.repeat_index)
            if (t.task_ref, t.config_hash, t.repeat_index) != slot:
                raise ResultsStoreRefusedError("a retry repeats the same task, config and repeat")
            if t.retry_of in exp.retried:
                raise ResultsStoreRefusedError("a retry of an invalid trial is made once")
            if exp.retries + 1 > s.budget.max_retries:
                raise ResultsStoreRefusedError("the retry is above max_retries")

    def _check_reconciliation(self, r: e.CostReconciliation) -> None:
        for exp in self.experiments.values():
            if r.trial_id in exp.trials:
                try:
                    e.cost_summary(exp.trials.values(), [*exp.reconciliations, r])
                except e.CostAccountingError as exc:
                    raise ResultsStoreRefusedError(str(exc)) from exc
                return
        raise ResultsStoreRefusedError("a reconciliation needs its trial")

    def apply(self, record: BaseModel) -> None:
        if isinstance(record, e.TaskSpec):
            self.tasks[record.task_ref()] = record
        elif isinstance(record, e.ExperimentSpec):
            self.experiments[record.experiment_id] = _Experiment(record)
        elif isinstance(record, e.TrialRecord):
            exp = self.experiments[record.experiment_id]
            exp.trials[record.trial_id] = record
            if record.retry_of is not None:
                exp.retried.add(record.retry_of)
                exp.retries += 1
        elif isinstance(record, e.CostReconciliation):
            for exp in self.experiments.values():
                if record.trial_id in exp.trials:
                    exp.reconciliations.append(record)
        elif isinstance(record, e.ScoreRecord):
            self.experiments[record.experiment_id].scores.append(record)


class SqliteResultsStore:
    """The evaluation side's store (one writer: the evaluation controller)."""

    KINDS: ClassVar[frozenset[str]] = frozenset(_KINDS)

    def __init__(
        self, conn: sqlite3.Connection, registry: _Registry, head: tuple[int, str] | None
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._head = head

    @classmethod
    def open(cls, path: Path) -> SqliteResultsStore:
        new = not path.exists()
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=True)
        try:
            if new:
                os.chmod(path, 0o600)
                conn.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                conn.execute(f"PRAGMA user_version = {FORMAT_VERSION}")
                conn.execute("COMMIT")
            registry, head = cls._verify(conn)
        except BaseException:
            conn.close()
            raise
        return cls(conn, registry, head)

    @staticmethod
    def _verify(conn: sqlite3.Connection) -> tuple[_Registry, tuple[int, str] | None]:
        if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ResultsStoreIntegrityError("the file is not an Agent Core results store")
        if conn.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION:
            raise ResultsStoreIntegrityError("unsupported results store format")
        registry = _Registry()
        previous: str | None = None
        expected = 1
        rows = conn.execute("SELECT seq, kind, body, prev_hash, hash FROM entries ORDER BY seq")
        for seq, kind, body, prev, digest in rows:
            if seq != expected or prev != previous or kind not in _KINDS:
                raise ResultsStoreIntegrityError("the results entries are not a gapless chain")
            if _entry_hash(kind, body, prev) != digest:
                raise ResultsStoreIntegrityError("a results entry does not match its hash")
            try:
                record = load_json(_KINDS[kind], body)
                if not registry.check(kind, record):
                    raise ResultsStoreIntegrityError("a results entry is stored twice")
            except (RecordValidationError, ResultsStoreRefusedError) as exc:
                raise ResultsStoreIntegrityError("a stored results entry breaks the rules") from exc
            registry.apply(record)
            previous, expected = digest, expected + 1
        head = None if previous is None else (expected - 1, previous)
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        recorded = (
            None if "head_seq" not in stored else (int(stored["head_seq"]), stored["head_hash"])
        )
        if recorded != head:
            raise ResultsStoreIntegrityError("the results head does not match its entries")
        return registry, head

    def close(self) -> None:
        self._conn.close()

    def head(self) -> tuple[int, str] | None:
        return self._head

    # ------------------------------------------------------------ writing

    def _append(self, kind: str, record: BaseModel) -> str | None:
        if not self._registry.check(kind, record):
            return None
        body = dump_json(record).decode()
        seq = 1 if self._head is None else self._head[0] + 1
        prev = None if self._head is None else self._head[1]
        digest = _entry_hash(kind, body, prev)
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO entries (seq, kind, body, prev_hash, hash) VALUES (?, ?, ?, ?, ?)",
                (seq, kind, body, prev, digest),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('head_seq', ?), ('head_hash', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(seq), digest),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        self._registry.apply(record)
        self._head = (seq, digest)
        return digest

    def register_task(self, task: e.TaskSpec) -> Digest:
        self._append("task", task)
        return task.task_ref()

    def freeze(self, spec: e.ExperimentSpec) -> Digest:
        """Fix the spec (D.V1). Freezing the same spec again returns its hash."""
        self._append("spec", spec)
        return spec.spec_hash()

    def append_trial(self, record: e.TrialRecord) -> None:
        self._append("trial", record)

    def append_reconciliation(self, record: e.CostReconciliation) -> None:
        self._append("reconciliation", record)

    def append_score(self, record: e.ScoreRecord) -> None:
        self._append("score", record)

    def append_analysis(self, record: e.AnalysisResult) -> None:
        self._append("analysis", record)

    def append_decision(self, record: e.Decision) -> None:
        self._append("decision", record)

    # ------------------------------------------------------------ reading

    def spec(self, experiment_id: ExperimentId) -> e.ExperimentSpec:
        return self._registry.experiment(experiment_id).spec

    def trials(self, experiment_id: ExperimentId) -> tuple[e.TrialRecord, ...]:
        return tuple(self._registry.experiment(experiment_id).trials.values())

    def cost_summary(self, experiment_id: ExperimentId) -> e.CostSummary:
        exp = self._registry.experiment(experiment_id)
        return e.cost_summary(exp.trials.values(), exp.reconciliations)
