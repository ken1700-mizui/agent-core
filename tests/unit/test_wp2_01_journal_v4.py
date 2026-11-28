"""WP2-01: Journal v4 (provenance) and compatibility with v3 and v1.

Design: ``docs/design/phase-2/PHASE_2_EVALUATION_CONTRACT.md`` 3.2 (3.2.4 lists
the provenance each transition needs).

- v4 adds a ``provenance`` list to the runtime payload. Each record points at a
  transition of the same event; the transitions and their rules are unchanged.
- New events are written as v4. v3 (and v1) events stay readable, their bytes
  and hashes unchanged; a v3 event never carries provenance; v4 never goes back
  to v3 in one journal; unknown versions are refused.
- Evaluation Runs (a Run created with ``RunProvenance``) need the provenance of
  each Step creation, each content fixing and each live result; a command
  without it is refused before anything is written. Markers for results that
  carry no measured value (abort, unknown, reconciled) are added by the runtime.
- Provenance never changes the state's meaning (Rebuild identity, Recovery).

``tests/fixtures/phase1_v3`` holds real journals written by the Phase 1 code
(``92c18c4``, ``tools/perf/state_identity.py generate``) as SQL text, with the
digests that code computed. They are the old format as it exists, not data made
up for a test; the deliberately broken journals below are made from them.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.runtime import provenance as pv
from agent_core.runtime import state_machine as smod
from agent_core.runtime.recovery import assess
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.types import events as ev
from agent_core.types.errors import (
    JournalCorruptionError,
    ProvenanceRequiredError,
    RecordValidationError,
    StateRebuildError,
)
from agent_core.types.event_hash import event_hash
from agent_core.types.events import Event
from agent_core.types.ids import AttemptId, ExperimentId, RunGrantId, StepId, TrialId
from agent_core.types.provenance import (
    AttemptProvenance,
    AttemptProvenanceFields,
    CallKind,
    OutcomeKind,
    OutcomeProvenance,
    OutcomeProvenanceFields,
    RunProvenanceFields,
    StepProvenance,
    StepProvenanceFields,
    ToolRef,
)
from agent_core.types.records import AttemptState as A
from agent_core.types.records import Run, RunStatus, Step, StepKind, StepStatus
from agent_core.types.safety import LedgerKeys, SecretKey
from agent_core.types.serialization import dump_json
from agent_core.types.transitions import (
    RUNTIME_EVENT_SCHEMA_VERSION,
    AbortEvidence,
    AbortReason,
    ReconciledOutcome,
    ReconciliationEvidence,
    RuntimePayload,
)
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w
from support.fakes import InMemoryJournal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "phase1_v3"
PHASE1_COMMIT = "92c18c4756f1b927a27f72deced90b96d0595d44"
SCENARIOS = ("late", "recovery", "stop", "unknown")

EXPERIMENT = b.new_id(ExperimentId)
TRIAL = b.new_id(TrialId)


def _keys() -> LedgerKeys:
    """The synthetic keys of ``tools/perf/state_identity.py`` (fixed labels)."""
    import hashlib

    def key(label: str) -> SecretKey:
        return SecretKey(hashlib.sha256(f"p7-identity-synthetic-{label}".encode()).digest())

    return LedgerKeys(owner=key("owner"), permit=key("permit"))


def _restore(name: str, tmp_path: Path) -> Path:
    path = tmp_path / name / "journal.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript((FIXTURES / f"{name}.sql").read_text())
    finally:
        conn.close()
    return path


def _setup(path: Path) -> lw.Setup:
    keys = _keys()
    clock = lw.ManualClock()
    journal = lw.open_journal(path, clock, keys)
    conn = sqlite3.connect(path)
    try:
        last = int(conn.execute("SELECT last_seq FROM owner_sequence").fetchone()[0])
    finally:
        conn.close()
    channel = OwnerApprovalChannel(keys.owner)
    return lw.Setup(path, journal, w.machine(journal), keys, clock, channel, owner_seq=[last])


def _rows(path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.execute("SELECT * FROM events ORDER BY tenant_seq"))
    finally:
        conn.close()


def _bodies(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(path)
    try:
        return [
            json.loads(r[0]) for r in conn.execute("SELECT body FROM events ORDER BY tenant_seq")
        ]
    finally:
        conn.close()


def _digest(st: lw.Setup) -> dict[str, Any]:
    return {
        "events": len(_rows(st.path)),
        "semantic": iw.digest(iw.semantic(st.sm.state)),
        "ledger": iw.digest(iw.ledger_view(st)),
    }


def _events(sm: RuntimeStateMachine, journal: InMemoryJournal) -> list[Event]:
    return list(journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=10_000))


# ---------------------------------------------------------------- the real old format


def test_fixtures_record_the_phase1_commit() -> None:
    meta = json.loads((FIXTURES / "digests.json").read_text())
    assert meta["generated_by"] == PHASE1_COMMIT
    assert sorted(meta["digests"]) == sorted(SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
def test_phase1_journals_open_with_the_same_meaning(name: str, tmp_path: Path) -> None:
    """Real v3 journals written by the Phase 1 code rebuild to the state that
    code computed (semantic, cost side and head), with no event changed."""
    expected = json.loads((FIXTURES / "digests.json").read_text())["digests"][name]
    path = _restore(name, tmp_path)
    before = _rows(path)
    assert {b_["schema_version"] for b_ in _bodies(path)} <= {1, 3}
    st = _setup(path)
    try:
        assert _digest(st) == expected
        assert st.journal.verify_tenant(b.TENANT) is not None
    finally:
        st.journal.close()
    assert _rows(path) == before


@pytest.mark.parametrize("name", SCENARIOS)
def test_v3_then_v4_in_one_journal(name: str, tmp_path: Path) -> None:
    """New work on a Phase 1 journal is written as v4 after the v3 events; the
    v3 events keep their bytes and hashes; the chain continues across versions;
    a restart rebuilds the same state; Recovery runs on the mixed journal."""
    path = _restore(name, tmp_path)
    v3_rows = _rows(path)
    st = _setup(path)
    st = iw.consumed_and_recover(st)  # new events, a restart and a recovery
    live = iw.semantic(st.sm.state)
    head = st.journal.head(b.TENANT)
    st.journal.close()

    rows = _rows(path)
    assert rows[: len(v3_rows)] == v3_rows
    versions = [body["schema_version"] for body in _bodies(path)]
    assert set(versions[: len(v3_rows)]) <= {1, 3}
    assert set(versions[len(v3_rows) :]) == {4}

    st2 = _setup(path)
    try:
        assert iw.semantic(st2.sm.state) == live
        assert st2.journal.head(b.TENANT) == head
        assert st2.journal.verify_tenant(b.TENANT) == head
        report = assess(st2.sm.state, st2.journal)
        assert report is not None
    finally:
        st2.journal.close()


def test_rebuild_is_identical_for_v3_and_v4_bodies_of_the_same_history() -> None:
    """The same transitions written as v3 and as v4 fold to the same state."""
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    w.dispatched_attempt(sm, w.step_in_progress(sm, run))
    v4 = _events(sm, journal)
    assert {e.schema_version for e in v4} == {4}
    v3 = _rechain([e.model_copy(update={"schema_version": 3}) for e in v4])
    assert iw.semantic(smod.rebuild(v4)) == iw.semantic(smod.rebuild(v3))


def _rechain(events: list[Event]) -> list[Event]:
    out: list[Event] = []
    previous: str | None = None
    for e in events:
        data = json.loads(dump_json(e))
        data["prev_hash"] = previous
        fixed = Event.model_validate_json(json.dumps(data))
        out.append(fixed)
        previous = event_hash(fixed)
    return out


def test_v4_after_v3_is_accepted_but_v3_after_v4_is_refused() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    w.running_run(sm)
    events = _events(sm, journal)
    mixed = _rechain(
        [e.model_copy(update={"schema_version": 3 if i < 2 else 4}) for i, e in enumerate(events)]
    )
    smod.rebuild(mixed)
    backwards = _rechain(
        [e.model_copy(update={"schema_version": 4 if i < 2 else 3}) for i, e in enumerate(events)]
    )
    with pytest.raises(StateRebuildError, match="version"):
        smod.rebuild(backwards)


def test_hash_chain_covers_the_provenance() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    _eval_step(sm, run)
    events = _events(sm, journal)
    tampered = []
    for e in events:
        data = json.loads(dump_json(e))
        for record in data["payload"].get("provenance", []):
            if record["stage"] == "step_created":
                record["config_hash"] = b.digest("another config")
        tampered.append(Event.model_validate_json(json.dumps(data)))
    assert [event_hash(e) for e in tampered] != [event_hash(e) for e in events]
    with pytest.raises(StateRebuildError, match="hash chain"):
        smod.rebuild(tampered)


# ---------------------------------------------------------------- versions


def test_versions_in_use() -> None:
    assert RUNTIME_EVENT_SCHEMA_VERSION == 4
    assert frozenset({1, 3, 4}) == ev.SUPPORTED_EVENT_SCHEMA_VERSIONS
    assert frozenset({2}) == ev.RETIRED_EVENT_SCHEMA_VERSIONS


@pytest.mark.parametrize("version", [0, 5, 99, -1])
def test_unknown_versions_are_refused(version: int) -> None:
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    data = json.loads(dump_json(_events(w.machine(journal), journal)[0]))
    data["schema_version"] = version
    with pytest.raises(ValidationError) as excinfo:
        Event.model_validate_json(json.dumps(data))
    assert "unsupported_schema_version" in {e["type"] for e in excinfo.value.errors()}


def test_v3_events_cannot_carry_provenance() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    _eval_run(sm)
    created = [e for e in _events(sm, journal) if e.payload and e.payload.provenance]
    data = json.loads(dump_json(created[0]))
    assert data["payload"]["provenance"]
    data["schema_version"] = 3
    with pytest.raises(ValidationError) as excinfo:
        Event.model_validate_json(json.dumps(data))
    assert "provenance_not_allowed" in {e["type"] for e in excinfo.value.errors()}


def test_v3_bytes_have_no_provenance_key() -> None:
    """Without provenance, the payload serializes exactly as in v3."""
    journal = InMemoryJournal()
    w.running_run(w.machine(journal))
    for e in _events(w.machine(journal), journal):
        assert e.payload is not None
        assert "provenance" not in json.loads(dump_json(e))["payload"]


def test_v4_needs_a_payload() -> None:
    with pytest.raises(ValidationError) as excinfo:
        b.event(schema_version=4)
    assert "payload_required" in {e["type"] for e in excinfo.value.errors()}


def test_a_reader_without_v4_refuses_v4_journals_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emulates a reader of the Phase 1 version set ({1, 3}): it refuses to open
    a journal with v4 events and changes nothing. (The real Phase 1 program was
    checked separately; see ``docs/design/phase-2/WP2-01_TYPES_AND_V4.md``.)"""
    st = lw.setup(tmp_path)
    w.running_run(st.sm)
    st.journal.close()
    before = st.path.read_bytes()
    monkeypatch.setattr(ev, "SUPPORTED_EVENT_SCHEMA_VERSIONS", frozenset({1, 3}))
    with pytest.raises(JournalCorruptionError) as excinfo:
        lw.open_journal(st.path, lw.ManualClock(), st.keys)
    cause = excinfo.value.__cause__
    assert isinstance(cause, RecordValidationError)
    assert {i.type for i in cause.issues} == {"unsupported_schema_version"}
    assert st.path.read_bytes() == before


# ---------------------------------------------------------------- provenance records


def _model() -> AttemptProvenanceFields:
    return AttemptProvenanceFields(
        call=CallKind.MODEL,
        model_profile_ref=b.digest("profile"),
        context_hash=b.digest("context"),
        prompt_template_ref=b.digest("prompt"),
        tool_ref=None,
        sandbox_profile_hash=None,
        image_digest=None,
    )


def _tool(sandbox: bool = False) -> AttemptProvenanceFields:
    return AttemptProvenanceFields(
        call=CallKind.TOOL,
        model_profile_ref=None,
        context_hash=None,
        prompt_template_ref=None,
        tool_ref=ToolRef(tool_id="builtin.read_file", version="1.0.0", manifest_hash=b.digest("m")),
        sandbox_profile_hash=b.digest("sandbox") if sandbox else None,
        image_digest=b.digest("image") if sandbox else None,
    )


def _step_fields(**overrides: Any) -> StepProvenanceFields:
    values: dict[str, Any] = {
        "config_hash": b.digest("config"),
        "task_ref": b.digest("task"),
        "eval_set_version": "1.0.0",
        "grader_version": "1.0.0",
        "experiment_id": EXPERIMENT,
        "trial_id": TRIAL,
        "plan_ref": None,
        "strategy_refs": (b.digest("ctx-strategy"),),
    }
    values.update(overrides)
    return StepProvenanceFields(**values)


@pytest.mark.parametrize(
    "fields",
    [
        {"call": CallKind.MODEL, "model_profile_ref": None},
        {"call": CallKind.MODEL, "tool_ref": _tool().tool_ref},
        {"call": CallKind.TOOL, "tool_ref": None},
        {"call": CallKind.TOOL, "context_hash": b.digest("c")},
        {"call": CallKind.TOOL, "sandbox_profile_hash": b.digest("s"), "image_digest": None},
    ],
)
def test_attempt_provenance_matches_the_kind_of_call(fields: dict[str, Any]) -> None:
    base = (_model() if fields["call"] is CallKind.MODEL else _tool()).model_dump()
    base.update(fields)
    with pytest.raises(ValidationError):
        AttemptProvenanceFields(**base)


@pytest.mark.parametrize(
    ("result", "values", "ok"),
    [
        (OutcomeKind.COMPLETED, {"wall_ms": 12}, True),
        (OutcomeKind.COMPLETED, {}, False),  # a live result needs the measured time
        (OutcomeKind.FAILED_DEFINITE, {"wall_ms": 0, "exit_ref": b.digest("e")}, True),
        (OutcomeKind.ABORTED_BEFORE_DISPATCH, {}, True),
        (OutcomeKind.ABORTED_BEFORE_DISPATCH, {"wall_ms": 1}, False),  # nothing ran
        (OutcomeKind.ABORTED_BEFORE_DISPATCH, {"exit_ref": b.digest("e")}, False),
        (OutcomeKind.OUTCOME_UNKNOWN, {}, True),
        (OutcomeKind.OUTCOME_UNKNOWN, {"wall_ms": 5}, False),  # not known
        (OutcomeKind.OUTCOME_UNKNOWN, {"verification_ref": b.digest("v")}, False),
        (OutcomeKind.RECONCILED_COMPLETED, {}, True),
        (OutcomeKind.RECONCILED_COMPLETED, {"exit_ref": b.digest("e")}, True),
        (OutcomeKind.RECONCILED_FAILED, {"wall_ms": 3}, False),  # never measured
    ],
)
def test_outcome_provenance_never_holds_values_that_do_not_exist(
    result: OutcomeKind, values: dict[str, Any], ok: bool
) -> None:
    args: dict[str, Any] = {"result": result, "wall_ms": None, "exit_ref": None}
    args["verification_ref"] = None
    args.update(values)
    if ok:
        OutcomeProvenanceFields(**args)
    else:
        with pytest.raises(ValidationError):
            OutcomeProvenanceFields(**args)


def test_step_provenance_needs_both_experiment_and_trial_or_neither() -> None:
    _step_fields(experiment_id=None, trial_id=None)
    with pytest.raises(ValidationError):
        _step_fields(trial_id=None)


def test_a_record_without_its_transition_is_refused() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    w.step_in_progress(sm, run)
    payload = _events(sm, journal)[-1].payload
    assert payload is not None
    stray = StepProvenance.bind(_step_fields(), b.new_id(StepId))
    with pytest.raises(ValidationError, match="transition"):
        RuntimePayload(transitions=payload.transitions, provenance=(stray,))


def test_one_record_per_transition_and_stage() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    sm.create_step(run, StepKind.TOOL_CALL)
    payload = _events(sm, journal)[-1].payload
    assert payload is not None
    step_id = payload.transitions[0].after.step_id  # type: ignore[union-attr]
    record = StepProvenance.bind(_step_fields(experiment_id=None, trial_id=None), step_id)
    RuntimePayload(transitions=payload.transitions, provenance=(record,))
    with pytest.raises(ValidationError, match="once"):
        RuntimePayload(transitions=payload.transitions, provenance=(record, record))


def test_outcome_record_must_name_the_transition_result() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    attempt = w.dispatched_attempt(sm, w.step_in_progress(sm, w.running_run(sm)))
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    payload = _events(sm, journal)[-1].payload
    assert payload is not None
    wrong = OutcomeProvenance.bind(
        OutcomeProvenanceFields(
            result=OutcomeKind.FAILED_DEFINITE, wall_ms=1, exit_ref=None, verification_ref=None
        ),
        attempt.attempt_id,
    )
    with pytest.raises(ValidationError, match="result"):
        RuntimePayload(transitions=payload.transitions, provenance=(wrong,))
    reconciled = OutcomeProvenance.bind(
        OutcomeProvenanceFields(
            result=OutcomeKind.RECONCILED_COMPLETED,
            wall_ms=None,
            exit_ref=None,
            verification_ref=None,
        ),
        attempt.attempt_id,
    )
    with pytest.raises(ValidationError, match="result"):
        RuntimePayload(transitions=payload.transitions, provenance=(reconciled,))


# ---------------------------------------------------------------- evaluation runs


def _eval_run(sm: RuntimeStateMachine) -> Run:
    run = sm.start_run(
        w.task(sm),
        run_grant_ref=b.new_id(RunGrantId),
        provenance=RunProvenanceFields(experiment_id=EXPERIMENT, trial_id=TRIAL),
    )
    run = sm.set_run_status(run, RunStatus.QUEUED)
    return sm.set_run_status(run, RunStatus.RUNNING)


def _eval_step(sm: RuntimeStateMachine, run: Run, kind: StepKind = StepKind.TOOL_CALL) -> Step:
    step = sm.create_step(run, kind, provenance=_step_fields())
    return sm.set_step_status(step, StepStatus.IN_PROGRESS)


def _eval_dispatched(sm: RuntimeStateMachine, step: Step, call: AttemptProvenanceFields) -> Any:
    attempt = sm.create_attempt(step)
    for target in w.PATH_WITHOUT_APPROVAL:
        extra: dict[str, Any] = {"provenance": call} if target is A.CONTENT_FIXED else {}
        attempt = sm.advance_attempt(attempt, target, w.evidence_for(attempt, target), **extra)
    return attempt


def test_an_evaluation_run_records_each_stage() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    step = _eval_step(sm, run, StepKind.MODEL_CALL)
    attempt = _eval_dispatched(sm, step, _model())
    done = OutcomeProvenanceFields(
        result=OutcomeKind.COMPLETED, wall_ms=42, exit_ref=None, verification_ref=None
    )
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED), provenance=done)
    index = pv.build_index(_events(sm, journal))
    view = index.run(run.run_id)
    assert view.trial is not None
    assert view.trial.experiment_id == EXPERIMENT
    assert view.complete
    assert index.step(step.step_id) is not None
    attempt_view = index.attempt(attempt.attempt_id)
    assert attempt_view.content is not None
    assert attempt_view.content.call is CallKind.MODEL
    assert attempt_view.outcome is not None
    assert attempt_view.outcome.wall_ms == 42


@pytest.mark.parametrize("stage", ["step", "content", "outcome"])
def test_missing_provenance_in_an_evaluation_run_is_refused_and_nothing_is_written(
    stage: str,
) -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    if stage == "step":
        before = len(_events(sm, journal))
        with pytest.raises(ProvenanceRequiredError):
            sm.create_step(run, StepKind.TOOL_CALL)
        assert len(_events(sm, journal)) == before
        return
    step = _eval_step(sm, run)
    attempt = sm.create_attempt(step)
    attempt = sm.advance_attempt(attempt, A.VALIDATED, w.evidence_for(attempt, A.VALIDATED))
    if stage == "content":
        before = len(_events(sm, journal))
        with pytest.raises(ProvenanceRequiredError):
            sm.advance_attempt(attempt, A.CONTENT_FIXED, w.evidence_for(attempt, A.CONTENT_FIXED))
        assert len(_events(sm, journal)) == before
        return
    for target in w.PATH_WITHOUT_APPROVAL[1:]:
        extra: dict[str, Any] = {"provenance": _tool()} if target is A.CONTENT_FIXED else {}
        attempt = sm.advance_attempt(attempt, target, w.evidence_for(attempt, target), **extra)
    before = len(_events(sm, journal))
    with pytest.raises(ProvenanceRequiredError):
        sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    assert len(_events(sm, journal)) == before
    assert sm.state.attempts[attempt.attempt_id].state is A.DISPATCH_STARTED


def test_the_call_kind_must_match_the_step_kind() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    step = _eval_step(sm, _eval_run(sm), StepKind.TOOL_CALL)
    with pytest.raises(ProvenanceRequiredError, match="call"):
        _eval_dispatched(sm, step, _model())


def test_step_provenance_must_name_the_runs_trial() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    with pytest.raises(ProvenanceRequiredError, match="trial"):
        sm.create_step(run, StepKind.TOOL_CALL, provenance=_step_fields(trial_id=b.new_id(TrialId)))


def test_markers_without_values_are_added_for_abort_unknown_and_reconciled() -> None:
    """Results that carry no measured value get their record from the runtime,
    so Recovery and the gate's abort path work unchanged in evaluation runs."""
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    step = _eval_step(sm, run)
    unknown = _eval_dispatched(sm, step, _tool())
    unknown = sm.advance_attempt(
        unknown, A.OUTCOME_UNKNOWN, w.evidence_for(unknown, A.OUTCOME_UNKNOWN)
    )
    sm.advance_attempt(
        unknown,
        A.COMPLETED,
        ReconciliationEvidence(
            kind="reconciliation", evidence_ref=b.digest("rec"), outcome=ReconciledOutcome.COMPLETED
        ),
    )
    aborted = sm.create_attempt(_eval_step(sm, run))
    for target in (A.VALIDATED, A.CONTENT_FIXED):
        extra: dict[str, Any] = {"provenance": _tool()} if target is A.CONTENT_FIXED else {}
        aborted = sm.advance_attempt(aborted, target, w.evidence_for(aborted, target), **extra)
    sm.advance_attempt(
        aborted,
        A.ABORTED_BEFORE_DISPATCH,
        AbortEvidence(kind="abort", reason=AbortReason.CANCELLED, not_dispatched_ref=None),
    )
    index = pv.build_index(_events(sm, journal))
    kinds = [r.result for r in index.attempt(unknown.attempt_id).outcomes]
    assert kinds == [OutcomeKind.OUTCOME_UNKNOWN, OutcomeKind.RECONCILED_COMPLETED]
    marker = index.attempt(aborted.attempt_id).outcomes[-1]
    assert marker.result is OutcomeKind.ABORTED_BEFORE_DISPATCH
    assert (marker.wall_ms, marker.exit_ref, marker.verification_ref) == (None, None, None)
    assert index.run(run.run_id).complete


def test_non_evaluation_runs_need_no_provenance_and_keep_working() -> None:
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = w.running_run(sm)
    attempt = w.dispatched_attempt(sm, w.step_in_progress(sm, run))
    sm.advance_attempt(attempt, A.COMPLETED, w.evidence_for(attempt, A.COMPLETED))
    events = _events(sm, journal)
    assert {e.schema_version for e in events} == {4}
    index = pv.build_index(events)
    assert index.run(run.run_id).trial is None
    assert index.run(run.run_id).complete
    assert all(not (e.payload and e.payload.provenance) for e in events)


def test_a_journal_with_an_incomplete_evaluation_run_still_opens_and_is_flagged() -> None:
    """Reading never depends on provenance: a crafted evaluation run without its
    step record still rebuilds (Recovery, status) and the index marks it."""
    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    _eval_step(sm, run)
    events = _events(sm, journal)
    stripped = []
    for e in events:
        data = json.loads(dump_json(e))
        records = data["payload"].get("provenance", [])
        if records and all(r["stage"] != "run_created" for r in records):
            del data["payload"]["provenance"]  # drop the step record only
        stripped.append(Event.model_validate_json(json.dumps(data)))
    stripped = _rechain(stripped)
    state = smod.rebuild(stripped)
    assert iw.semantic(state) == iw.semantic(smod.rebuild(events))
    view = pv.build_index(stripped).run(run.run_id)
    assert view.trial is not None
    assert not view.complete
    assert view.missing


def test_provenance_survives_a_restart_of_the_runtime(tmp_path: Path) -> None:
    """After a restart, the runtime still knows which runs are evaluation runs."""
    st = lw.setup(tmp_path)
    run = _eval_run(st.sm)
    st2 = st.reopen()
    try:
        with pytest.raises(ProvenanceRequiredError):
            st2.sm.create_step(st2.sm.state.runs[run.run_id], StepKind.TOOL_CALL)
        st2.sm.create_step(
            st2.sm.state.runs[run.run_id], StepKind.TOOL_CALL, provenance=_step_fields()
        )
    finally:
        st2.journal.close()


def test_status_reads_provenance_or_says_none_was_recorded(tmp_path: Path) -> None:
    """Read-only status: present provenance is shown; earlier versions say so."""
    path = _restore("stop", tmp_path)
    st = _setup(path)
    run_ids = list(st.sm.state.runs)
    st.journal.close()
    conn_before = _rows(path)
    lines = pv.describe(pv.build_index(_read_all(path)), run_ids[0])
    assert lines == ("provenance: not recorded (earlier journal version)",)
    assert _rows(path) == conn_before

    journal = InMemoryJournal()
    sm = w.machine(journal)
    run = _eval_run(sm)
    _eval_step(sm, run)
    text = pv.describe(pv.build_index(_events(sm, journal)), run.run_id)
    assert any("experiment" in line for line in text)


def _read_all(path: Path) -> list[Event]:
    journal = lw.open_journal(path, lw.ManualClock(), _keys())
    try:
        return list(journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1_000))
    finally:
        journal.close()


def test_attempt_ids_in_records_are_typed() -> None:
    record = AttemptProvenance.bind(_tool(), b.new_id(AttemptId))
    assert record.stage == "content_fixed"
    with pytest.raises(ValidationError):
        AttemptProvenance.model_validate({**record.model_dump(), "attempt_id": "x"})
