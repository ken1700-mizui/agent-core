"""Transition tables (WP-04): every (current, target) pair is checked.

Allowed edges are listed here independently of the implementation, from
A 6 (Run), E 3.1 with E 38 (Attempt) and the WP-04 decisions (Task, Step).
"""

from __future__ import annotations

import itertools
from enum import StrEnum
from typing import Any

import pytest

from agent_core.runtime.state_machine import (
    ATTEMPT_TRANSITIONS,
    FINAL_ATTEMPT_STATES,
    RUN_TRANSITIONS,
    STEP_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_STEP_STATUSES,
    TERMINAL_TASK_STATUSES,
    check_edge,
)
from agent_core.types.errors import IllegalTransitionError
from agent_core.types.records import (
    TERMINAL_RUN_STATUSES,
    AttemptState,
    RunStatus,
    StepStatus,
    TaskStatus,
)

A, R, S, T = AttemptState, RunStatus, StepStatus, TaskStatus
_ABORT = A.ABORTED_BEFORE_DISPATCH
_STOP = {R.FAILED, R.ABORTED_SAFETY}

EXPECTED_RUN: dict[RunStatus, set[RunStatus]] = {
    R.CREATED: {R.QUEUED} | _STOP,
    R.QUEUED: {R.RUNNING, R.RECOVERING} | _STOP,
    R.RUNNING: {
        R.WAITING_TOOL,
        R.WAITING_APPROVAL,
        R.PAUSED_BUDGET,
        R.VERIFYING,
        R.CANCELLING,
        R.RECONCILIATION_REQUIRED,
        R.RECOVERING,
    }
    | _STOP,
    R.WAITING_TOOL: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
    R.WAITING_APPROVAL: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
    R.PAUSED_BUDGET: {R.RUNNING, R.CANCELLING, R.RECOVERING} | _STOP,
    R.VERIFYING: {R.COMPLETED, R.RUNNING, R.RECOVERING} | _STOP,
    R.RECOVERING: {R.QUEUED, R.RECONCILIATION_REQUIRED} | _STOP,
    R.CANCELLING: {R.CANCELLED, R.RECONCILIATION_REQUIRED} | _STOP,
    R.RECONCILIATION_REQUIRED: {R.RUNNING, R.CANCELLED} | _STOP,
    R.COMPLETED: set(),
    R.CANCELLED: set(),
    R.FAILED: set(),
    R.ABORTED_SAFETY: set(),
}

EXPECTED_ATTEMPT: dict[AttemptState, set[AttemptState]] = {
    A.PROPOSED: {A.VALIDATED, _ABORT},
    A.VALIDATED: {A.CONTENT_FIXED, _ABORT},
    A.CONTENT_FIXED: {A.POLICY_ALLOWED, _ABORT},
    A.POLICY_ALLOWED: {A.APPROVAL_PENDING, A.BUDGET_RESERVED, _ABORT},
    A.APPROVAL_PENDING: {A.APPROVED, _ABORT},
    A.APPROVED: {A.BUDGET_RESERVED, _ABORT},
    A.BUDGET_RESERVED: {A.INTENT_RECORDED, _ABORT},
    A.INTENT_RECORDED: {A.PERMIT_ISSUED, _ABORT},
    A.PERMIT_ISSUED: {A.PERMIT_CONSUMED, _ABORT},
    A.PERMIT_CONSUMED: {A.DISPATCH_AUTHORIZED, A.OUTCOME_UNKNOWN, _ABORT},
    A.DISPATCH_AUTHORIZED: {A.DISPATCH_STARTED, A.OUTCOME_UNKNOWN, _ABORT},
    A.DISPATCH_STARTED: {A.COMPLETED, A.FAILED_DEFINITE, A.OUTCOME_UNKNOWN},
    A.OUTCOME_UNKNOWN: {A.COMPLETED, A.FAILED_DEFINITE},
    A.COMPLETED: set(),
    A.FAILED_DEFINITE: set(),
    _ABORT: set(),
}

EXPECTED_STEP: dict[StepStatus, set[StepStatus]] = {
    S.PLANNED: {S.IN_PROGRESS, S.CANCELLED},
    S.IN_PROGRESS: {S.COMMITTED, S.FAILED, S.RECONCILIATION_REQUIRED, S.CANCELLED},
    S.RECONCILIATION_REQUIRED: {S.COMMITTED, S.FAILED, S.CANCELLED},
    S.COMMITTED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}

EXPECTED_TASK: dict[TaskStatus, set[TaskStatus]] = {
    T.CREATED: {T.ACTIVE, T.CANCELLED, T.FAILED},
    T.ACTIVE: {T.PAUSED, T.COMPLETED, T.FAILED, T.CANCELLED},
    T.PAUSED: {T.ACTIVE, T.FAILED, T.CANCELLED},
    T.COMPLETED: set(),
    T.FAILED: set(),
    T.CANCELLED: set(),
}

TABLES: list[tuple[str, type[StrEnum], Any, dict[Any, set[Any]]]] = [
    ("run", RunStatus, RUN_TRANSITIONS, EXPECTED_RUN),
    ("attempt", AttemptState, ATTEMPT_TRANSITIONS, EXPECTED_ATTEMPT),
    ("step", StepStatus, STEP_TRANSITIONS, EXPECTED_STEP),
    ("task", TaskStatus, TASK_TRANSITIONS, EXPECTED_TASK),
]


@pytest.mark.parametrize(("name", "enum", "table", "expected"), TABLES, ids=[t[0] for t in TABLES])
def test_table_matches_the_specification(
    name: str, enum: type[StrEnum], table: Any, expected: dict[Any, set[Any]]
) -> None:
    assert set(table) == set(enum)
    assert {k: set(v) for k, v in table.items()} == expected


def _all_pairs() -> list[tuple[str, Any, Any, bool]]:
    pairs: list[tuple[str, Any, Any, bool]] = []
    for name, enum, _, expected in TABLES:
        members: list[Any] = list(enum)
        for current, target in itertools.product(members, members):
            pairs.append((name, current, target, target in expected[current]))
    return pairs


@pytest.mark.parametrize(("name", "current", "target", "allowed"), _all_pairs())
def test_every_pair_is_allowed_or_rejected_by_the_edge_check(
    name: str, current: Any, target: Any, allowed: bool
) -> None:
    table = dict((t[0], t[2]) for t in TABLES)[name]
    if allowed:
        check_edge(table, current, target, name)
    else:
        with pytest.raises(IllegalTransitionError):
            check_edge(table, current, target, name)


def test_terminal_states_have_no_outgoing_edges() -> None:
    for run_status in TERMINAL_RUN_STATUSES:
        assert RUN_TRANSITIONS[run_status] == frozenset()
    for step_status in TERMINAL_STEP_STATUSES:
        assert STEP_TRANSITIONS[step_status] == frozenset()
    for task_status in TERMINAL_TASK_STATUSES:
        assert TASK_TRANSITIONS[task_status] == frozenset()
    for state in FINAL_ATTEMPT_STATES:
        assert ATTEMPT_TRANSITIONS[state] == frozenset()
    assert {A.COMPLETED, A.FAILED_DEFINITE, _ABORT} == FINAL_ATTEMPT_STATES


def test_completed_is_reachable_only_from_verifying() -> None:
    sources = {s for s, targets in RUN_TRANSITIONS.items() if R.COMPLETED in targets}
    assert sources == {R.VERIFYING}


def test_permit_consumed_is_reachable_only_from_permit_issued() -> None:
    sources = {s for s, targets in ATTEMPT_TRANSITIONS.items() if A.PERMIT_CONSUMED in targets}
    assert sources == {A.PERMIT_ISSUED}
