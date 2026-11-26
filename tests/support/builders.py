"""Valid synthetic records for tests. Every value here is synthetic."""

from __future__ import annotations

from itertools import count
from typing import Any

from agent_core.types.events import Event, EventDraft
from agent_core.types.ids import (
    AttemptId,
    EntityId,
    EventId,
    PermitId,
    ReceiptId,
    RequestId,
    ReservationId,
    RunGrantId,
    RunId,
    SiteId,
    StepId,
    TaskId,
    TenantId,
)
from agent_core.types.records import (
    Attempt,
    AttemptState,
    EngineRequest,
    Run,
    RunStatus,
    Step,
    StepKind,
    StepStatus,
    Task,
    TaskStatus,
)
from agent_core.types.values import (
    Budget,
    Currency,
    EngineName,
    EngineVersion,
    NonMonetaryCaps,
    sha256_digest,
)

SITE = SiteId("dev01")
TS = 1_780_000_000_000
_counter = count(1)


def new_id[T: EntityId](id_type: type[T]) -> T:
    return id_type.compose(SITE, TS, next(_counter))


def digest(label: str) -> str:
    return sha256_digest(label.encode())


ENGINE = EngineVersion(engine=EngineName.RUNTIME, version="0.1.0")
TENANT = TenantId.compose(SITE, TS, 0)


def caps(**overrides: int) -> NonMonetaryCaps:
    values: dict[str, int] = {
        "model_calls": 200,
        "tool_calls": 300,
        "context_rebuilds": 50,
        "replans": 10,
        "memory_operations": 20,
        "retries": 30,
        "total_operations": 600,
        "wall_clock_ms": 7_200_000,
    }
    values.update(overrides)
    return NonMonetaryCaps(**values)


def task(**overrides: Any) -> Task:
    values: dict[str, Any] = {
        "task_id": new_id(TaskId),
        "goal_ref": digest("goal"),
        "done_criteria": (digest("criterion"),),
        "budget_total": Budget(currency=Currency.XTU, micro_units=5_000_000),
        "caps_total": caps(),
        "status": TaskStatus.CREATED,
        "runs": (),
    }
    values.update(overrides)
    return Task(**values)


def run(**overrides: Any) -> Run:
    values: dict[str, Any] = {
        "run_id": new_id(RunId),
        "task_id": new_id(TaskId),
        "run_grant_ref": new_id(RunGrantId),
        "status": RunStatus.CREATED,
        "owner_epoch": 1,
        "parent_run_id": None,
        "cancel_requested": False,
    }
    values.update(overrides)
    return Run(**values)


def step(**overrides: Any) -> Step:
    values: dict[str, Any] = {
        "step_id": new_id(StepId),
        "run_id": new_id(RunId),
        "run_seq": 1,
        "kind": StepKind.MODEL_CALL,
        "status": StepStatus.PLANNED,
    }
    values.update(overrides)
    return Step(**values)


def attempt(**overrides: Any) -> Attempt:
    values: dict[str, Any] = {
        "attempt_id": new_id(AttemptId),
        "step_id": new_id(StepId),
        "state": AttemptState.PROPOSED,
        "action_hash": None,
        "permit_id": None,
        "receipt_id": None,
        "reservation_id": None,
        "approval_required": None,
    }
    values.update(overrides)
    return Attempt(**values)


def consumed_attempt(**overrides: Any) -> Attempt:
    values: dict[str, Any] = {
        "state": AttemptState.PERMIT_CONSUMED,
        "action_hash": digest("action"),
        "permit_id": new_id(PermitId),
        "receipt_id": new_id(ReceiptId),
        "reservation_id": new_id(ReservationId),
    }
    values.update(overrides)
    return attempt(**values)


def event_draft(**overrides: Any) -> EventDraft:
    values: dict[str, Any] = {
        "event_id": new_id(EventId),
        "type": "runtime.task_created",
        "schema_version": 1,
        "tenant_id": TENANT,
        "run_id": None,
        "engine": ENGINE,
        "policy_version": "policy-1",
        "access_epoch": 0,
        "owner_epoch": 1,
        "revocation_gen": 0,
        "labels": ("synthetic",),
        "refs": (),
        "keyed_hashes": (),
        "recorded_at_ms": TS,
    }
    values.update(overrides)
    return EventDraft(**values)


def event(**overrides: Any) -> Event:
    values: dict[str, Any] = {
        "event_id": new_id(EventId),
        "type": "runtime.task_created",
        "schema_version": 1,
        "tenant_id": TENANT,
        "run_id": None,
        "run_seq": None,
        "tenant_seq": 1,
        "engine": ENGINE,
        "policy_version": "policy-1",
        "access_epoch": 0,
        "owner_epoch": 1,
        "revocation_gen": 0,
        "labels": ("synthetic",),
        "refs": (),
        "keyed_hashes": (),
        "prev_hash": None,
        "recorded_at_ms": TS,
    }
    values.update(overrides)
    return Event(**values)


def engine_request(**overrides: Any) -> EngineRequest:
    values: dict[str, Any] = {
        "request_id": new_id(RequestId),
        "kind": "model_gateway.complete",
        "engine": ENGINE,
        "run_id": None,
        "step_id": None,
        "attempt_id": None,
        "tenant_id": TENANT,
        "payload_ref": digest("payload"),
        "labels": ("synthetic",),
        "estimated_cost": None,
        "policy_version": "policy-1",
        "access_epoch": 0,
    }
    values.update(overrides)
    return EngineRequest(**values)
