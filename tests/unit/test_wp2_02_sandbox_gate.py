"""WP2-02: the Phase 1 gate path (Permit, Receipt, report) through P2 (fake sandbox).

Since B1A-04 the program run is read from the approved ``sandbox_run`` content
(``tests/support/sandbox_world.py``); since B1A-03 a definite result needs P2's
containment evidence. ``run_sandbox_operation`` (P1):

1. ``prepare`` before the claim. An unavailable or refusing broker ends the
   attempt ABORTED_BEFORE_DISPATCH (SANDBOX_UNAVAILABLE) through the live gate's
   not-dispatched report; the hold is released; nothing ran.
2. The gate claims and starts (the Receipt is used once), then the callback asks
   P2 to start with that Receipt, waits for a state in which nothing runs and
   collects the result. A lost broker, a duplicate start, or a stop that is not
   confirmed is an unknown outcome: the hold is kept, nothing is retried.
3. The result is recorded with the gate's signed report; for evaluation Runs
   with P2's measured ``wall_ms`` (Journal v4 outcome provenance).

Approvals, holds and unknown outcomes are handled exactly as in Phase 1.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from agent_core.runtime import recovery as rc
from agent_core.runtime.provenance import build_index
from agent_core.types.cost import ReservationState
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import ExperimentId, RunGrantId, TrialId
from agent_core.types.provenance import (
    AttemptProvenanceFields,
    CallKind,
    OutcomeKind,
    RunProvenanceFields,
    StepProvenanceFields,
    ToolRef,
)
from agent_core.types.records import AttemptState as A
from agent_core.types.records import RunStatus, StepKind, StepStatus
from agent_core.types.safety import ReceiptState
from agent_core.types.transitions import AbortReason
from support import broker_world as bw
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w
from support import sandbox_world as sw


def _flaky(broker: object, after: int) -> object:
    """Connects for the first ``after`` requests (prepare, start), then fails."""
    calls = {"n": 0}
    good = bw.pair_connector(broker)  # type: ignore[arg-type]

    def connect() -> socket.socket:
        calls["n"] += 1
        if calls["n"] > after:
            raise ConnectionResetError("broker gone")
        return good()

    return connect


def test_the_gate_path_runs_through_p2(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st)
    done = sw.run(st, broker, attempt)
    assert done.attempt.state is A.COMPLETED
    assert st.journal.receipt_state(str(attempt.receipt_id)) is ReceiptState.USED
    assert broker.sandbox.runs[attempt.receipt_id] == 1
    assert done.reap_error is None
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is not ReservationState.UNKNOWN
    broker.close()


def test_a_failing_program_is_a_definite_failure(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    done = sw.run(st, broker, sw.consumed(st, program="exit1"))
    assert done.attempt.state is A.FAILED_DEFINITE
    broker.close()


def test_an_unavailable_broker_aborts_before_dispatch_and_releases_the_hold(
    tmp_path: Path,
) -> None:
    st, broker = sw.setup(tmp_path)

    def unreachable() -> socket.socket:
        raise ConnectionRefusedError("no broker")

    attempt = sw.consumed(st)
    done = sw.run(st, broker, attempt, client=bw.make_client(broker, connector=unreachable))
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    run_id = st.sm.state.steps[attempt.step_id].run_id
    last = st.journal.read_run(run_id, after_run_seq=0, limit=1_000)[-1]
    transition = last.payload.transitions[-1]  # type: ignore[union-attr]
    assert transition.evidence.reason is AbortReason.SANDBOX_UNAVAILABLE  # type: ignore[union-attr]
    assert st.journal.receipt_state(str(attempt.receipt_id)) is ReceiptState.NOT_DISPATCHED
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is ReservationState.RELEASED
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


def test_a_broker_lost_after_the_start_leaves_an_unknown_outcome_with_its_hold(
    tmp_path: Path,
) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="hang")
    client = bw.make_client(broker, epoch=st.journal.owner_epoch, connector=_flaky(broker, 2))  # type: ignore[arg-type]
    with pytest.raises(SandboxUnavailableError):
        sw.run(st, broker, attempt, client=client)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is ReservationState.UNKNOWN
    assert broker.sandbox.runs[attempt.receipt_id] == 1
    broker.close()


def test_a_stop_that_is_not_confirmed_is_an_unknown_outcome(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="stubborn")
    with pytest.raises(SandboxUnavailableError, match="stop"):
        sw.run(st, broker, attempt, max_polls=2)
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    broker.close()


def test_a_confirmed_stop_after_the_wait_is_a_definite_failure(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    done = sw.run(st, broker, sw.consumed(st, program="hang"), max_polls=2)
    assert done.attempt.state is A.FAILED_DEFINITE  # contained (B1A-03)
    broker.close()


def test_a_stale_owner_epoch_never_runs(tmp_path: Path) -> None:
    """P2 already saw a newer epoch: the older runtime is refused at prepare,
    before the claim, so the attempt ends before dispatch and nothing runs."""
    st, broker = sw.setup(tmp_path)
    newer = st.journal.owner_epoch + 5
    bw.make_client(broker, epoch=newer).start(bw.start_request(epoch=newer))
    attempt = sw.consumed(st)
    done = sw.run(st, broker, attempt)
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert attempt.receipt_id not in broker.sandbox.runs
    broker.close()


def test_recovery_after_an_unknown_sandbox_outcome_keeps_the_hold(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    attempt = sw.consumed(st, program="hang")
    client = bw.make_client(broker, epoch=st.journal.owner_epoch, connector=_flaky(broker, 2))  # type: ignore[arg-type]
    with pytest.raises(SandboxUnavailableError):
        sw.run(st, broker, attempt, client=client)
    st2 = st.reopen()
    try:
        rc.recover(iw.deps(st2), deadlines=iw.DEADLINES, run_ids=list(st2.sm.state.runs))
        assert st2.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
        reservation = st2.journal.reservation(lw.rsv(attempt))
        assert reservation is not None
        assert reservation.state is ReservationState.UNKNOWN
    finally:
        st2.journal.close()
    assert broker.sandbox.runs[attempt.receipt_id] == 1
    broker.close()


def test_an_evaluation_run_records_p2s_measured_time(tmp_path: Path) -> None:
    st, broker = sw.setup(tmp_path)
    experiment, trial = b.new_id(ExperimentId), b.new_id(TrialId)
    run = st.sm.start_run(
        w.task(st.sm),
        run_grant_ref=b.new_id(RunGrantId),
        provenance=RunProvenanceFields(experiment_id=experiment, trial_id=trial),
    )
    run = st.sm.set_run_status(st.sm.set_run_status(run, RunStatus.QUEUED), RunStatus.RUNNING)
    fields = StepProvenanceFields(
        config_hash=b.digest("config"),
        task_ref=b.digest("task"),
        eval_set_version="1.0.0",
        grader_version="1.0.0",
        experiment_id=experiment,
        trial_id=trial,
        plan_ref=None,
        strategy_refs=(),
    )
    step = st.sm.set_step_status(
        st.sm.create_step(run, StepKind.TOOL_CALL, provenance=fields), StepStatus.IN_PROGRESS
    )
    content = AttemptProvenanceFields(
        call=CallKind.TOOL,
        model_profile_ref=None,
        context_hash=None,
        prompt_template_ref=None,
        tool_ref=ToolRef(tool_id="sandbox_run", version="1.0.0", manifest_hash=b.digest("m")),
        sandbox_profile_hash=bw.PROFILE,
        image_digest=b.digest("image"),
    )
    issued = lw.issued(
        st,
        step,
        registration=sw.SANDBOX_TOOL,
        canonical_input=sw.program_input(sw.program()),
        content_provenance=content,
    )
    attempt = lw.consume(st, issued)
    done = sw.run(st, broker, attempt)
    assert done.attempt.state is A.COMPLETED
    events = list(st.journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1_000))
    outcome = build_index(events).attempt(attempt.attempt_id).outcome
    assert outcome is not None
    assert outcome.result is OutcomeKind.COMPLETED
    assert outcome.wall_ms == broker.sandbox.program_wall_ms("exit0")
    assert outcome.exit_ref is not None
    assert build_index(events).run(run.run_id).complete
    broker.close()
