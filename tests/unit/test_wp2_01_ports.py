"""WP2-01: the new ports and the sandbox request types (no sandbox runs here).

- ``EvaluationStorePort`` is implemented by the SQLite results store.
- ``SandboxPort`` (prepare, start, status, stop, collect, reap) is typed so a
  stop request is never a confirmed stop, and an exit exists only when finished.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.adapters.sqlite_results import SqliteResultsStore
from agent_core.ports.evaluation import EvaluationStorePort, GraderPort, PlannerPort
from agent_core.ports.sandbox import SandboxPort
from agent_core.types import sandbox as sx
from agent_core.types.evaluation import GradingRequest, PlanProposal
from agent_core.types.ids import AttemptId, ExperimentId, PermitId, ReceiptId, RequestId, TrialId
from agent_core.types.records import StepKind
from support import builders as b


def test_the_results_store_is_an_evaluation_store(tmp_path: Path) -> None:
    store = SqliteResultsStore.open(tmp_path / "r.db")
    try:
        assert isinstance(store, EvaluationStorePort)
    finally:
        store.close()


def test_ports_are_protocols() -> None:
    for port in (SandboxPort, GraderPort, PlannerPort, EvaluationStorePort):
        assert getattr(port, "_is_protocol", False)
    assert {"prepare", "start", "status", "stop", "collect", "reap"} <= set(dir(SandboxPort))


def _start(**overrides: Any) -> sx.StartRequest:
    values: dict[str, Any] = {
        "request_id": b.new_id(RequestId),
        "attempt_id": b.new_id(AttemptId),
        "permit_id": b.new_id(PermitId),
        "receipt_id": b.new_id(ReceiptId),
        "owner_epoch": 3,
        "sandbox_profile_hash": b.digest("profile"),
        "program_ref": b.digest("fixed diagnostic"),
        "deadline_ms": 300_000,
        "grace_ms": 10_000,
    }
    values.update(overrides)
    return sx.StartRequest(**values)


def test_start_needs_its_receipt_and_a_bounded_deadline() -> None:
    _start()
    with pytest.raises(ValidationError):
        _start(receipt_id=None)
    with pytest.raises(ValidationError):
        _start(deadline_ms=0)
    with pytest.raises(ValidationError):
        _start(deadline_ms=sx.MAX_DEADLINE_MS + 1)


def test_a_stop_request_is_not_a_confirmed_stop() -> None:
    sx.StopResult(
        request_id=b.new_id(RequestId),
        attempt_id=b.new_id(AttemptId),
        state=sx.SandboxState.RUNNING,
        stop_confirmed=False,
        containment=sx.Containment.CONTAINED,
    )
    with pytest.raises(ValidationError):
        sx.StopResult(
            request_id=b.new_id(RequestId),
            attempt_id=b.new_id(AttemptId),
            state=sx.SandboxState.RUNNING,
            stop_confirmed=True,
            containment=sx.Containment.CONTAINED,
        )


def test_an_exit_exists_only_when_finished() -> None:
    def status(state: sx.SandboxState, exit_code: int | None) -> sx.StatusResult:
        return sx.StatusResult(
            request_id=b.new_id(RequestId),
            attempt_id=b.new_id(AttemptId),
            receipt_id=b.new_id(ReceiptId),
            state=state,
            exit_code=exit_code,
            containment=sx.Containment.UNKNOWN,
        )

    status(sx.SandboxState.FINISHED, 0)
    status(sx.SandboxState.RUNNING, None)
    with pytest.raises(ValidationError):
        status(sx.SandboxState.RUNNING, 0)
    with pytest.raises(ValidationError):
        status(sx.SandboxState.FINISHED, None)


def test_grading_and_planning_requests() -> None:
    GradingRequest(
        trial_id=b.new_id(TrialId),
        experiment_id=b.new_id(ExperimentId),
        task_ref=b.digest("task"),
        artifact_hash=b.digest("artifact"),
        grader_version="1.0.0",
    )
    with pytest.raises(ValidationError):
        PlanProposal(plan_ref=b.digest("plan"), step_kinds=())
    PlanProposal(plan_ref=b.digest("plan"), step_kinds=(StepKind.MODEL_CALL, StepKind.TOOL_CALL))
