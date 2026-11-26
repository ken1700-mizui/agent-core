"""Common records: Task, Run, Step, Attempt, Event, EngineRequest (E_INTEGRATION 8)."""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.types.errors import ImmutableFieldError
from agent_core.types.events import Event
from agent_core.types.ids import AttemptId, PermitId, ReceiptId, ReservationId, RunId, StepId
from agent_core.types.records import (
    TERMINAL_RUN_STATUSES,
    AttemptState,
    RunStatus,
    StepStatus,
    TaskStatus,
)
from agent_core.types.values import MAX_INT64, Budget, Currency, KeyedHash
from support import builders as b


def _error_types(excinfo: pytest.ExceptionInfo[ValidationError]) -> set[str]:
    return {str(e["type"]) for e in excinfo.value.errors()}


# ---------------------------------------------------------------- valid records


def test_valid_records_can_be_built() -> None:
    assert b.task().status is TaskStatus.CREATED
    assert b.run().status is RunStatus.CREATED
    assert b.step().run_seq == 1
    assert b.attempt().state is AttemptState.PROPOSED
    assert b.consumed_attempt().receipt_id is not None
    assert b.event().tenant_seq == 1
    assert b.engine_request().run_id is None


def test_run_terminal_statuses_follow_design_a_section_6() -> None:
    assert {
        RunStatus.COMPLETED,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.ABORTED_SAFETY,
    } == TERMINAL_RUN_STATUSES


def test_attempt_states_match_e_integration_3_1() -> None:
    assert [s.value for s in AttemptState] == [
        "PROPOSED",
        "VALIDATED",
        "CONTENT_FIXED",
        "POLICY_ALLOWED",
        "APPROVAL_PENDING",
        "APPROVED",
        "BUDGET_RESERVED",
        "INTENT_RECORDED",
        "PERMIT_ISSUED",
        "PERMIT_CONSUMED",
        "DISPATCH_AUTHORIZED",
        "DISPATCH_STARTED",
        "COMPLETED",
        "FAILED_DEFINITE",
        "OUTCOME_UNKNOWN",
        "ABORTED_BEFORE_DISPATCH",
    ]


# ---------------------------------------------------------------- rejection: types and fields


@pytest.mark.parametrize(
    ("builder", "field"),
    [
        (b.task, "goal_ref"),
        (b.run, "run_grant_ref"),
        (b.step, "kind"),
        (b.attempt, "state"),
        (b.event, "tenant_seq"),
        (b.engine_request, "payload_ref"),
    ],
)
def test_missing_required_field_is_rejected(builder: Any, field: str) -> None:
    record = builder()
    data = {k: getattr(record, k) for k in type(record).model_fields if k != field}
    with pytest.raises(ValidationError) as excinfo:
        type(record).model_validate(data)
    assert "missing" in _error_types(excinfo)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        b.task(unexpected="value")
    assert "extra_forbidden" in _error_types(excinfo)


@pytest.mark.parametrize(
    ("builder", "field", "value"),
    [
        (b.task, "status", "CREATED"),
        (b.task, "status", "DONE"),
        (b.run, "status", "RUNNING"),
        (b.step, "status", "COMMITTED"),
        (b.attempt, "state", "PROPOSED"),
    ],
)
def test_status_must_be_a_known_enum_member_not_a_string(
    builder: Any, field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        builder(**{field: value})


@pytest.mark.parametrize(
    ("builder", "field", "value"),
    [
        (b.step, "run_seq", 0),
        (b.step, "run_seq", -1),
        (b.step, "run_seq", MAX_INT64 + 1),
        (b.step, "run_seq", 1.0),
        (b.step, "run_seq", "1"),
        (b.step, "run_seq", True),
        (b.event, "tenant_seq", 0),
        (b.run, "owner_epoch", -1),
        (b.event, "access_epoch", -1),
        (b.event, "revocation_gen", math.nan),
        (b.event, "recorded_at_ms", math.inf),
    ],
)
def test_invalid_numbers_are_rejected_without_coercion(
    builder: Any, field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        builder(**{field: value})


@pytest.mark.parametrize("micro_units", [-1, MAX_INT64 + 1, 1.5, math.nan, math.inf, "100"])
def test_invalid_budget_amounts_are_rejected(micro_units: object) -> None:
    with pytest.raises(ValidationError):
        Budget(currency=Currency.XTU, micro_units=micro_units)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["model_calls", "tool_calls", "wall_clock_ms", "retries"])
def test_negative_caps_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        b.caps(**{field: -1})


def test_wrong_id_kind_in_a_record_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        b.run(task_id=b.new_id(RunId))
    assert "id_kind_mismatch" in _error_types(excinfo)


@pytest.mark.parametrize(
    ("builder", "field", "value"),
    [
        (b.task, "goal_ref", "sha256:" + "0" * 63),
        (b.task, "goal_ref", "md5:" + "0" * 32),
        (b.task, "goal_ref", "sha256:" + "A" * 64),
        (b.event, "policy_version", ""),
        (b.event, "type", "Runtime Task Created"),
        (b.event, "labels", ("Synthetic",)),
    ],
)
def test_malformed_strings_are_rejected(builder: Any, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        builder(**{field: value})


def test_lists_are_not_coerced_to_tuples() -> None:
    with pytest.raises(ValidationError):
        b.task(done_criteria=[b.digest("criterion")])


def test_done_criteria_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        b.task(done_criteria=())


@pytest.mark.parametrize(
    ("builder", "field", "make"),
    [
        (b.task, "runs", lambda: (lambda r: (r, r))(b.new_id(RunId))),
        (b.event, "labels", lambda: ("synthetic", "synthetic")),
        (b.event, "labels", lambda: ("zeta", "alpha")),
        (b.event, "refs", lambda: (b.digest("b"), b.digest("b"))),
    ],
)
def test_collections_must_be_unique_and_canonical(builder: Any, field: str, make: Any) -> None:
    with pytest.raises(ValidationError):
        builder(**{field: make()})


def test_keyed_hash_names_must_be_unique_and_sorted() -> None:
    first = KeyedHash(name="args", value="hmac-sha256:" + "1" * 64)
    second = KeyedHash(name="target", value="hmac-sha256:" + "2" * 64)
    assert b.event(keyed_hashes=(first, second)).keyed_hashes == (first, second)
    with pytest.raises(ValidationError):
        b.event(keyed_hashes=(second, first))
    with pytest.raises(ValidationError):
        b.event(keyed_hashes=(first, first))


# ---------------------------------------------------------------- record-level consistency


def test_run_cannot_be_its_own_parent() -> None:
    run_id = b.new_id(RunId)
    with pytest.raises(ValidationError):
        b.run(run_id=run_id, parent_run_id=run_id)


@pytest.mark.parametrize(
    "state", [AttemptState.CONTENT_FIXED, AttemptState.POLICY_ALLOWED, AttemptState.APPROVED]
)
def test_attempt_needs_action_hash_once_content_is_fixed(state: AttemptState) -> None:
    approval = True if state is AttemptState.APPROVED else None
    with pytest.raises(ValidationError):
        b.attempt(state=state, action_hash=None, approval_required=approval)
    fixed = b.attempt(state=state, action_hash=b.digest("action"), approval_required=approval)
    assert fixed.state is state


@pytest.mark.parametrize("state", [AttemptState.APPROVAL_PENDING, AttemptState.APPROVED])
def test_approval_states_require_the_approval_path(state: AttemptState) -> None:
    for flag in (None, False):
        with pytest.raises(ValidationError):
            b.attempt(state=state, action_hash=b.digest("a"), approval_required=flag)


def test_attempt_needs_permit_from_permit_issued() -> None:
    with pytest.raises(ValidationError):
        b.attempt(state=AttemptState.PERMIT_ISSUED, action_hash=b.digest("a"))


def test_attempt_cannot_hold_a_permit_before_it_is_issued() -> None:
    with pytest.raises(ValidationError):
        b.attempt(
            state=AttemptState.BUDGET_RESERVED,
            action_hash=b.digest("a"),
            permit_id=b.new_id(PermitId),
        )


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.PERMIT_CONSUMED,
        AttemptState.DISPATCH_STARTED,
        AttemptState.COMPLETED,
        AttemptState.OUTCOME_UNKNOWN,
    ],
)
def test_attempt_needs_receipt_after_consumption(state: AttemptState) -> None:
    with pytest.raises(ValidationError):
        b.consumed_attempt(state=state, receipt_id=None)


def test_receipt_without_permit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        b.attempt(
            state=AttemptState.ABORTED_BEFORE_DISPATCH,
            receipt_id=b.new_id(ReceiptId),
        )


def test_reservation_id_may_be_allocated_before_approval() -> None:
    # AUDIT-01: the ID is allocated before the ActionDescriptor is fixed.
    # Holding the ID grants nothing; activation is WP-09.
    record = b.attempt(
        state=AttemptState.CONTENT_FIXED,
        action_hash=b.digest("a"),
        reservation_id=b.new_id(ReservationId),
    )
    assert record.reservation_id is not None


def test_event_run_id_and_run_seq_come_together() -> None:
    with pytest.raises(ValidationError):
        b.event(run_id=b.new_id(RunId), run_seq=None)
    with pytest.raises(ValidationError):
        b.event(run_id=None, run_seq=3)


def test_event_hash_chain_start_is_explicit() -> None:
    with pytest.raises(ValidationError):
        b.event(tenant_seq=1, prev_hash=b.digest("x"))
    with pytest.raises(ValidationError):
        b.event(tenant_seq=2, prev_hash=None)
    assert b.event(tenant_seq=2, prev_hash=b.digest("x")).tenant_seq == 2


@pytest.mark.parametrize("version", [0, 2, 4, 99, "1", 1.0, True])
def test_unknown_schema_version_is_rejected(version: object) -> None:
    with pytest.raises(ValidationError):
        b.event(schema_version=version)


def test_event_from_draft_assigns_positions() -> None:
    run_id = b.new_id(RunId)
    draft = b.event_draft(run_id=run_id)
    stored = Event.from_draft(draft, tenant_seq=5, run_seq=2, prev_hash=b.digest("prev"))
    assert (stored.event_id, stored.tenant_seq, stored.run_seq) == (draft.event_id, 5, 2)
    with pytest.raises(ValidationError):
        Event.from_draft(draft, tenant_seq=5, run_seq=None, prev_hash=b.digest("prev"))


def test_engine_request_ids_must_form_a_chain() -> None:
    with pytest.raises(ValidationError):
        b.engine_request(attempt_id=b.new_id(AttemptId))
    with pytest.raises(ValidationError):
        b.engine_request(step_id=b.new_id(StepId))
    chained = b.engine_request(
        run_id=b.new_id(RunId), step_id=b.new_id(StepId), attempt_id=b.new_id(AttemptId)
    )
    assert chained.attempt_id is not None


def test_engine_request_cost_estimate_must_be_a_budget() -> None:
    with pytest.raises(ValidationError):
        b.engine_request(estimated_cost=100)


# ---------------------------------------------------------------- immutability and evolve


def test_records_are_frozen() -> None:
    record = b.task()
    with pytest.raises(ValidationError):
        record.status = TaskStatus.ACTIVE  # type: ignore[misc]  # runtime check of frozen
    stored = b.event()
    with pytest.raises(ValidationError):
        stored.tenant_seq = 9  # type: ignore[misc]  # runtime check of frozen


def test_event_and_request_records_have_no_update_method() -> None:
    assert not hasattr(b.event(), "evolve")
    assert not hasattr(b.engine_request(), "evolve")


def test_evolve_returns_a_new_validated_snapshot() -> None:
    original = b.step()
    moved = original.evolve(status=StepStatus.IN_PROGRESS)
    assert moved.status is StepStatus.IN_PROGRESS
    assert original.status is StepStatus.PLANNED
    with pytest.raises(ValidationError):
        original.evolve(status="IN_PROGRESS")


def test_evolve_cannot_change_identity_fields() -> None:
    with pytest.raises(ImmutableFieldError):
        b.step().evolve(run_id=b.new_id(RunId))
    with pytest.raises(ImmutableFieldError):
        b.run().evolve(task_id=b.task().task_id)


def test_evolve_cannot_rewrite_write_once_fields() -> None:
    fixed = b.attempt(state=AttemptState.CONTENT_FIXED, action_hash=b.digest("a"))
    with pytest.raises(ImmutableFieldError):
        fixed.evolve(action_hash=b.digest("other"))
    allocated = fixed.evolve(reservation_id=b.new_id(ReservationId))
    with pytest.raises(ImmutableFieldError):
        allocated.evolve(reservation_id=b.new_id(ReservationId))
    assert allocated.evolve(state=AttemptState.POLICY_ALLOWED).reservation_id == (
        allocated.reservation_id
    )


def test_evolve_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        b.task().evolve(unexpected=1)
