"""Provenance index of a Journal (WP2-01; contract 3.2.3, 3.2.4).

A derived index, never a source of truth and never part of ``RuntimeState``:
the state's meaning, Rebuild, Recovery and status do not depend on it. It is
folded from the runtime payloads in journal order and needs only the payloads
(Steps and Attempts are always created by a CREATE transition).

Two uses:

- write side (``check`` and ``markers``): the runtime refuses a command of an
  evaluation Run whose transition lacks the provenance it needs, or whose
  provenance does not match its Run, Step or call; it adds the value-free
  records of abort, unknown and reconciled results itself;
- read side (``add``, ``run``, ``describe``): what was recorded, and which
  evaluation Runs are incomplete (the evaluation side never uses those).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from agent_core.types.events import Event
from agent_core.types.ids import AttemptId, RunId, StepId
from agent_core.types.provenance import (
    EMPTY_RESULTS,
    RECONCILED_RESULTS,
    AttemptProvenance,
    CallKind,
    OutcomeKind,
    OutcomeProvenance,
    OutcomeProvenanceFields,
    Provenance,
    RunProvenance,
    StepProvenance,
)
from agent_core.types.records import StepKind
from agent_core.types.transitions import (
    AttemptTransition,
    EntityAction,
    RuntimePayload,
    RunTransition,
    StepTransition,
    fixes_content,
    outcome_kind,
)

_CALL_OF_STEP = {
    StepKind.MODEL_CALL: CallKind.MODEL,
    StepKind.TOOL_CALL: CallKind.TOOL,
    StepKind.VERIFICATION: CallKind.TOOL,
}


@dataclass
class AttemptView:
    attempt_id: AttemptId
    content: AttemptProvenance | None = None
    outcomes: list[OutcomeProvenance] = field(default_factory=list)

    @property
    def outcome(self) -> OutcomeProvenance | None:
        return self.outcomes[-1] if self.outcomes else None


@dataclass
class RunView:
    run_id: RunId
    trial: RunProvenance | None = None
    v4: bool = False
    records: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


class ProvenanceIndex:
    def __init__(self) -> None:
        self._runs: dict[RunId, RunView] = {}
        self._step_run: dict[StepId, RunId] = {}
        self._step_kind: dict[StepId, StepKind] = {}
        self._steps: dict[StepId, StepProvenance] = {}
        self._attempt_step: dict[AttemptId, StepId] = {}
        self._attempts: dict[AttemptId, AttemptView] = {}

    # ------------------------------------------------------------ queries

    def run(self, run_id: RunId) -> RunView:
        return self._runs.get(run_id, RunView(run_id))

    def step(self, step_id: StepId) -> StepProvenance | None:
        return self._steps.get(step_id)

    def attempt(self, attempt_id: AttemptId) -> AttemptView:
        return self._attempts.get(attempt_id, AttemptView(attempt_id))

    def evaluation_run(self, run_id: RunId) -> RunProvenance | None:
        view = self._runs.get(run_id)
        return None if view is None else view.trial

    # ------------------------------------------------------------ the rules

    def _lookup(
        self, payload: RuntimePayload
    ) -> tuple[
        dict[StepId, RunId],
        dict[StepId, StepKind],
        dict[AttemptId, StepId],
        dict[RunId, RunProvenance],
    ]:
        step_run, step_kind = dict(self._step_run), dict(self._step_kind)
        attempt_step = dict(self._attempt_step)
        trials = {r: v.trial for r, v in self._runs.items() if v.trial is not None}
        for record in payload.provenance:
            if isinstance(record, RunProvenance):
                trials[record.run_id] = record
        for t in payload.transitions:
            if isinstance(t, StepTransition) and t.action is EntityAction.CREATE:
                step_run[t.after.step_id] = t.after.run_id
                step_kind[t.after.step_id] = t.after.kind
            elif isinstance(t, AttemptTransition) and t.action is EntityAction.CREATE:
                attempt_step[t.after.attempt_id] = t.after.step_id
        return step_run, step_kind, attempt_step, trials

    def check(self, payload: RuntimePayload) -> list[str]:
        """The problems of ``payload`` against the rules of contract 3.2.4."""
        step_run, step_kind, attempt_step, trials = self._lookup(payload)
        steps = {r.step_id: r for r in payload.provenance if isinstance(r, StepProvenance)}
        contents = {r.attempt_id: r for r in payload.provenance if isinstance(r, AttemptProvenance)}
        outcomes = {r.attempt_id: r for r in payload.provenance if isinstance(r, OutcomeProvenance)}
        problems: list[str] = []
        for t in payload.transitions:
            if isinstance(t, StepTransition) and t.action is EntityAction.CREATE:
                trial = trials.get(t.after.run_id)
                record = steps.get(t.after.step_id)
                if trial is not None and record is None:
                    problems.append("an evaluation step needs its step provenance")
                elif trial is not None and record is not None:
                    if (record.experiment_id, record.trial_id) != (
                        trial.experiment_id,
                        trial.trial_id,
                    ):
                        problems.append("step provenance names another trial than its run")
                elif record is not None and record.trial_id is not None:
                    problems.append(
                        "step provenance names a trial but its run is no evaluation run"
                    )
            if not isinstance(t, AttemptTransition):
                continue
            step_id = attempt_step.get(t.after.attempt_id, t.after.step_id)
            trial = trials.get(step_run[step_id]) if step_id in step_run else None
            if fixes_content(t):
                content = contents.get(t.after.attempt_id)
                if content is None and trial is not None:
                    problems.append("an evaluation attempt needs its content provenance")
                kind = step_kind.get(step_id)
                if (
                    content is not None
                    and kind is not None
                    and _CALL_OF_STEP[kind] is not content.call
                ):
                    problems.append("the call kind of the provenance does not match the step kind")
            needs_outcome = outcome_kind(t) is not None and trial is not None
            if needs_outcome and t.after.attempt_id not in outcomes:
                problems.append("an evaluation result needs its outcome provenance")
        return problems

    def markers(self, payload: RuntimePayload) -> tuple[OutcomeProvenance, ...]:
        """Value-free outcome records the runtime adds for evaluation Runs."""
        step_run, _, attempt_step, trials = self._lookup(payload)
        given = {r.attempt_id for r in payload.provenance if isinstance(r, OutcomeProvenance)}
        added: list[OutcomeProvenance] = []
        for t in payload.transitions:
            if not isinstance(t, AttemptTransition):
                continue
            kind = outcome_kind(t)
            if kind is None or kind not in EMPTY_RESULTS | RECONCILED_RESULTS:
                continue
            step_id = attempt_step.get(t.after.attempt_id, t.after.step_id)
            if (
                step_id in step_run
                and step_run[step_id] in trials
                and t.after.attempt_id not in given
            ):
                fields = OutcomeProvenanceFields.marker(kind)
                added.append(OutcomeProvenance.bind(fields, t.after.attempt_id))
        return tuple(added)

    # ------------------------------------------------------------ folding

    def add(self, payload: RuntimePayload, *, v4: bool) -> None:
        """Fold one stored payload (read side: problems are recorded, not raised)."""
        problems = self.check(payload)
        for record in payload.provenance:
            self._record(record)
        for t in payload.transitions:
            if isinstance(t, RunTransition) and t.action is EntityAction.CREATE:
                self._runs.setdefault(t.after.run_id, RunView(t.after.run_id)).v4 = v4
            elif isinstance(t, StepTransition) and t.action is EntityAction.CREATE:
                self._step_run[t.after.step_id] = t.after.run_id
                self._step_kind[t.after.step_id] = t.after.kind
            elif isinstance(t, AttemptTransition) and t.action is EntityAction.CREATE:
                self._attempt_step[t.after.attempt_id] = t.after.step_id
        if problems:
            run_id = self._run_of(payload)
            if run_id is not None:
                self._runs.setdefault(run_id, RunView(run_id)).missing.extend(problems)

    def _run_of(self, payload: RuntimePayload) -> RunId | None:
        for t in payload.transitions:
            if isinstance(t, RunTransition):
                return t.after.run_id
            if isinstance(t, StepTransition):
                return t.after.run_id
            if isinstance(t, AttemptTransition):
                step = self._attempt_step.get(t.after.attempt_id, t.after.step_id)
                return self._step_run.get(step)
        return None

    def _record(self, record: Provenance) -> None:
        if isinstance(record, RunProvenance):
            view = self._runs.setdefault(record.run_id, RunView(record.run_id))
            view.trial = record
            view.records += 1
            return
        if isinstance(record, StepProvenance):
            self._steps[record.step_id] = record
            run_id = self._step_run.get(record.step_id)
        else:
            view_a = self._attempts.setdefault(record.attempt_id, AttemptView(record.attempt_id))
            if isinstance(record, AttemptProvenance):
                view_a.content = record
            else:
                view_a.outcomes.append(record)
            step = self._attempt_step.get(record.attempt_id)
            run_id = None if step is None else self._step_run.get(step)
        if run_id is not None:
            self._runs.setdefault(run_id, RunView(run_id)).records += 1

    def tee(self, events: Iterable[Event]) -> Iterator[Event]:
        """Yield ``events`` unchanged, folding each runtime payload on the way."""
        for event in events:
            if event.payload is not None:
                self.add(event.payload, v4=event.schema_version >= 4)
            yield event


def build_index(events: Iterable[Event]) -> ProvenanceIndex:
    index = ProvenanceIndex()
    for _ in index.tee(events):
        pass
    return index


def describe(index: ProvenanceIndex, run_id: RunId) -> tuple[str, ...]:
    """Read-only status lines for one Run (contract 3.2.3)."""
    view = index.run(run_id)
    if view.trial is None and view.records == 0:
        if not view.v4:
            return ("provenance: not recorded (earlier journal version)",)
        return ("provenance: none recorded (not an evaluation run)",)
    lines = []
    if view.trial is not None:
        lines.append(
            f"provenance: evaluation run, experiment {view.trial.experiment_id},"
            f" trial {view.trial.trial_id}"
        )
    else:
        lines.append("provenance: recorded (not an evaluation run)")
    lines.append(f"provenance records: {view.records}")
    lines.append("provenance complete: " + ("yes" if view.complete else "no"))
    lines.extend(f"provenance missing: {m}" for m in view.missing)
    return tuple(lines)


__all__ = ["OutcomeKind", "ProvenanceIndex", "build_index", "describe"]
