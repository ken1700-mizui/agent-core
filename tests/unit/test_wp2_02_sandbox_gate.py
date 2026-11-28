"""WP2-02: the Phase 1 gate path (Permit, Receipt, report) through P2 (fake sandbox).

``run_sandbox_operation`` (P1):

1. ``prepare`` before the claim. An unavailable or refusing broker ends the
   attempt ABORTED_BEFORE_DISPATCH (SANDBOX_UNAVAILABLE) through the live gate's
   not-dispatched report; the hold is released; nothing ran.
2. The gate claims and starts (the Receipt is used once), then the callback asks
   P2 to start with that Receipt, waits for a state in which nothing runs and
   collects the result. A lost broker, a refused or duplicate start, or a stop
   that is not confirmed is an unknown outcome: the hold is kept, nothing is
   retried.
3. The result is recorded with the gate's signed report; for evaluation Runs
   with P2's measured ``wall_ms`` (Journal v4 outcome provenance).

Approvals, holds and unknown outcomes are handled exactly as in Phase 1.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from agent_core.runtime import recovery as rc
from agent_core.runtime.sandbox_execution import SandboxProgram, run_sandbox_operation
from agent_core.types.cost import ReservationState
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.ids import RequestId
from agent_core.types.provenance import OutcomeKind
from agent_core.types.records import AttemptState as A
from agent_core.types.safety import ReceiptState
from agent_core.types.transitions import AbortReason
from agent_core_broker.fake import program_ref
from support import broker_world as bw
from support import builders as b
from support import identity_world as iw
from support import ledger_world as lw
from support import runtime_world as w


def _program(name: str = "exit0", deadline_ms: int = 60_000) -> SandboxProgram:
    return SandboxProgram(
        sandbox_profile_hash=bw.PROFILE,
        image_digest=b.digest("image"),
        workspace_ref=b.digest("workspace"),
        program_ref=program_ref(name),
        deadline_ms=deadline_ms,
        grace_ms=100,
    )


def _run(st: lw.Setup, attempt: Any, client: Any, program: SandboxProgram, **kw: Any) -> Any:
    return run_sandbox_operation(
        gate=lw.gate(st),
        sandbox=client,
        attempt=attempt,
        program=program,
        new_request_id=lambda: b.new_id(RequestId),
        max_polls=kw.pop("max_polls", 3),
        **lw.actual(st, attempt),
        **kw,
    )


def _consumed(st: lw.Setup) -> Any:
    return lw.consume(st, lw.issued(st, lw.step_in(st)))


def test_the_gate_path_runs_through_p2(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    attempt = _consumed(st)
    done = _run(st, attempt, bw.make_client(broker, epoch=st.journal.owner_epoch), _program())
    assert done.attempt.state is A.COMPLETED
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.USED
    assert broker.sandbox.runs[attempt.receipt_id] == 1
    assert done.reap_error is None
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is not ReservationState.UNKNOWN


def test_a_failing_program_is_a_definite_failure(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    done = _run(
        st, _consumed(st), bw.make_client(broker, epoch=st.journal.owner_epoch), _program("exit1")
    )
    assert done.attempt.state is A.FAILED_DEFINITE


def test_an_unavailable_broker_aborts_before_dispatch_and_releases_the_hold(
    tmp_path: Path,
) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)

    def unreachable() -> socket.socket:
        raise ConnectionRefusedError("no broker")

    attempt = _consumed(st)
    done = _run(st, attempt, bw.make_client(broker, connector=unreachable), _program())
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    events = st.journal.read_run(
        st.sm.state.steps[attempt.step_id].run_id, after_run_seq=0, limit=1_000
    )
    last = events[-1].payload.transitions[-1]  # type: ignore[union-attr]
    assert last.evidence.reason is AbortReason.SANDBOX_UNAVAILABLE  # type: ignore[union-attr]
    assert st.journal.receipt_state(attempt.receipt_id) is ReceiptState.NOT_DISPATCHED
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is ReservationState.RELEASED
    assert attempt.receipt_id not in broker.sandbox.runs


def test_a_broker_lost_after_the_start_leaves_an_unknown_outcome_with_its_hold(
    tmp_path: Path,
) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    calls = {"n": 0}
    good = bw.pair_connector(broker)

    def flaky() -> socket.socket:
        calls["n"] += 1
        if calls["n"] > 2:  # prepare and start reach P2; the status does not
            raise ConnectionResetError("broker gone")
        return good()

    attempt = _consumed(st)
    with pytest.raises(SandboxUnavailableError):
        _run(
            st,
            attempt,
            bw.make_client(broker, epoch=st.journal.owner_epoch, connector=flaky),
            _program(),
        )
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN
    reservation = st.journal.reservation(lw.rsv(attempt))
    assert reservation is not None
    assert reservation.state is ReservationState.UNKNOWN
    assert broker.sandbox.runs[attempt.receipt_id] == 1


def test_a_stop_that_is_not_confirmed_is_an_unknown_outcome(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    attempt = _consumed(st)
    with pytest.raises(SandboxUnavailableError, match="stop"):
        _run(
            st,
            attempt,
            bw.make_client(broker, epoch=st.journal.owner_epoch),
            _program("stubborn"),
            max_polls=2,
        )
    assert st.sm.state.attempts[attempt.attempt_id].state is A.OUTCOME_UNKNOWN


def test_a_confirmed_stop_after_the_wait_is_a_definite_failure(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    done = _run(
        st,
        _consumed(st),
        bw.make_client(broker, epoch=st.journal.owner_epoch),
        _program("hang"),
        max_polls=2,
    )
    assert done.attempt.state is A.FAILED_DEFINITE


def test_a_stale_owner_epoch_never_runs(tmp_path: Path) -> None:
    """P2 already saw a newer epoch: the older runtime is refused at prepare,
    before the claim, so the attempt ends before dispatch and nothing runs."""
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    newer = st.journal.owner_epoch + 5
    bw.make_client(broker, epoch=newer).start(bw.start_request(epoch=newer))
    attempt = _consumed(st)
    done = _run(st, attempt, bw.make_client(broker, epoch=st.journal.owner_epoch), _program())
    assert done.attempt.state is A.ABORTED_BEFORE_DISPATCH
    assert attempt.receipt_id not in broker.sandbox.runs


def test_recovery_after_an_unknown_sandbox_outcome_keeps_the_hold(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    broker = bw.make_broker(tmp_path)
    calls = {"n": 0}
    good = bw.pair_connector(broker)

    def flaky() -> socket.socket:
        calls["n"] += 1
        if calls["n"] > 2:
            raise ConnectionResetError("broker gone")
        return good()

    attempt = _consumed(st)
    with pytest.raises(SandboxUnavailableError):
        _run(
            st,
            attempt,
            bw.make_client(broker, epoch=st.journal.owner_epoch, connector=flaky),
            _program(),
        )
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


def test_an_evaluation_run_records_p2s_measured_time(tmp_path: Path) -> None:
    from agent_core.runtime.provenance import build_index
    from agent_core.types.ids import ExperimentId, RunGrantId, TrialId
    from agent_core.types.provenance import (
        AttemptProvenanceFields,
        CallKind,
        RunProvenanceFields,
        StepProvenanceFields,
        ToolRef,
    )
    from agent_core.types.records import RunStatus, StepKind, StepStatus

    st = lw.setup(tmp_path)
    experiment, trial = b.new_id(ExperimentId), b.new_id(TrialId)
    task = w.task(st.sm)
    run = st.sm.start_run(
        task,
        run_grant_ref=b.new_id(RunGrantId),
        provenance=RunProvenanceFields(experiment_id=experiment, trial_id=trial),
    )
    run = st.sm.set_run_status(st.sm.set_run_status(run, RunStatus.QUEUED), RunStatus.RUNNING)
    step = st.sm.create_step(
        run,
        StepKind.TOOL_CALL,
        provenance=StepProvenanceFields(
            config_hash=b.digest("config"),
            task_ref=b.digest("task"),
            eval_set_version="1.0.0",
            grader_version="1.0.0",
            experiment_id=experiment,
            trial_id=trial,
            plan_ref=None,
            strategy_refs=(),
        ),
    )
    step = st.sm.set_step_status(step, StepStatus.IN_PROGRESS)
    content = AttemptProvenanceFields(
        call=CallKind.TOOL,
        model_profile_ref=None,
        context_hash=None,
        prompt_template_ref=None,
        tool_ref=ToolRef(tool_id="sandbox.run", version="1.0.0", manifest_hash=b.digest("m")),
        sandbox_profile_hash=bw.PROFILE,
        image_digest=b.digest("image"),
    )
    attempt = lw.consume(st, lw.issued(st, step, content_provenance=content))
    broker = bw.make_broker(tmp_path)
    done = _run(st, attempt, bw.make_client(broker, epoch=st.journal.owner_epoch), _program())
    assert done.attempt.state is A.COMPLETED
    events = list(st.journal.read_tenant(b.TENANT, after_tenant_seq=0, limit=1_000))
    outcome = build_index(events).attempt(attempt.attempt_id).outcome
    assert outcome is not None
    assert outcome.result is OutcomeKind.COMPLETED
    assert outcome.wall_ms == broker.sandbox.program_wall_ms("exit0")
    assert outcome.exit_ref is not None
    assert build_index(events).run(run.run_id).complete
