"""What the CLI shows: fixed fields read from the trusted records only.

The approval screen is built from the descriptor the ledger recorded for the
waiting Attempt (the one the approval is bound to through its action hash):
the operation, the tool and its executable's version and hashes, the target
resources and their access, the change's before and after digests, the cost
cap and the deadline. Never the prompt, a file's content, a key or a message.
Costs are synthetic XTU (micro units); no real API is used in Phase 1.
"""

from __future__ import annotations

from typing import Any, Final

from agent_core.runtime.agent_loop import LoopOutcome
from agent_core.types.cost import BudgetScopeType, CounterKind, ReservationState
from agent_core.types.ids import RunId
from agent_core.types.records import Attempt
from agent_core_cli import render
from agent_core_cli.environment import Session

UNIT: Final = "XTU (synthetic)"
REAL_API_COST: Final = "none"
_SETTLED: Final = frozenset(
    {
        ReservationState.ID_ALLOCATED,
        ReservationState.USAGE_REPORTED,
        ReservationState.RECONCILED,
        ReservationState.RELEASED,
        ReservationState.VOID,
    }
)


def outcome(run_id: RunId, result: LoopOutcome) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "run_status": result.status.value,
        "reason": result.reason,
        "model_calls": result.model_calls,
        "tool_calls": result.tool_calls,
        "verification_reads": result.verification_reads,
        "repairs": result.repairs,
        "ledger_model_calls": result.ledger_model_calls,
        "ledger_tool_calls": result.ledger_tool_calls,
        "pending_action_hash": result.pending_action_hash,
        "error": result.error,
    }


def approval_screen(session: Session, attempt: Attempt) -> dict[str, Any]:
    descriptor = session.journal.recorded_descriptor(str(attempt.attempt_id))
    run_id = session.sm.state.steps[attempt.step_id].run_id
    if descriptor is None:  # never for a waiting Attempt; shown as unknown, not guessed
        return {
            "run_id": str(run_id),
            "attempt_id": str(attempt.attempt_id),
            "action_hash": attempt.action_hash,
            "operation": None,
            "resumable": False,
            "unit": UNIT,
        }
    change = descriptor.apply_change
    tool = descriptor.tool
    return {
        "run_id": str(run_id),
        "attempt_id": str(attempt.attempt_id),
        "operation": descriptor.kind.value,
        "tool_id": tool.tool_id if tool else None,
        "tool_version": tool.version if tool else None,
        "manifest_hash": tool.manifest_hash if tool else None,
        "artifact_hash": tool.artifact_hash if tool else None,
        "effect_class": descriptor.effect_class.value,
        "targets": [
            {"path": t.path, "access": t.access.value} for t in descriptor.target_resources
        ],
        "change": None
        if change is None
        else {
            "op": change.op.value,
            "path": change.path,
            "before": change.expected_pre,
            "after": change.content_digest,
            "size": change.content_size,
        },
        "action_hash": descriptor.action_hash(),
        "cost_cap_micro": descriptor.cost_cap.micro_units,
        "expires_at_ms": descriptor.expires_at_ms,
        "expires_at": render.iso(descriptor.expires_at_ms),
        "resumable": session.continues(run_id),
        "unit": UNIT,
    }


def pending(session: Session) -> dict[str, Any]:
    return {"items": [approval_screen(session, a) for a in session.pending()]}


def _count(session: Session, run_id: RunId, counter: CounterKind) -> int | None:
    view = session.journal.counter(BudgetScopeType.RUN, str(run_id), counter)
    return None if view is None else view.used


def status(session: Session) -> dict[str, Any]:
    state = session.sm.state
    tasks = []
    for task in state.tasks.values():
        runs = []
        for run in (r for r in state.runs.values() if r.task_id == task.task_id):
            usage = session.journal.scope_usage(BudgetScopeType.RUN, str(run.run_id))
            runs.append(
                {
                    "run_id": str(run.run_id),
                    "status": run.status.value,
                    "spent_micro": usage.spent_micro,
                    "hold_micro": usage.hold_micro,
                    "ledger_model_calls": _count(session, run.run_id, CounterKind.MODEL_CALLS),
                    "ledger_tool_calls": _count(session, run.run_id, CounterKind.TOOL_CALLS),
                    "resumable": session.continues(run.run_id),
                }
            )
        tasks.append({"task_id": str(task.task_id), "status": task.status.value, "runs": runs})
    return {"tasks": tasks, "unit": UNIT, "real_api_cost": REAL_API_COST}


def _attempts(session: Session, run_id: RunId) -> list[tuple[Any, Attempt]]:
    state = session.sm.state
    return [
        (state.steps[s], state.attempts[a])
        for s in state.run_steps.get(run_id, ())
        for a in state.step_attempts.get(s, ())
    ]


def _reservation(session: Session, attempt: Attempt) -> Any:
    if attempt.reservation_id is None:
        return None
    return session.journal.reservation(str(attempt.reservation_id))


def steps(session: Session, run_id: RunId) -> dict[str, Any]:
    state = session.sm.state
    out = []
    for index, step_id in enumerate(state.run_steps.get(run_id, ()), start=1):
        step = state.steps[step_id]
        attempts = []
        for attempt_id in state.step_attempts.get(step_id, ()):
            attempt = state.attempts[attempt_id]
            view = _reservation(session, attempt)
            attempts.append(
                {
                    "attempt_id": str(attempt.attempt_id),
                    "state": attempt.state.value,
                    "reservation_state": view.state.value if view else None,
                    "spent_micro": view.spent_micro if view else 0,
                    "hold_micro": view.hold_micro if view else 0,
                    "cost_final": view.spent_known if view else None,
                }
            )
        out.append(
            {
                "index": index,
                "kind": step.kind.value,
                "status": step.status.value,
                "attempts": attempts,
            }
        )
    return {"run_id": str(run_id), "steps": out}


def budget(session: Session, run_id: RunId) -> dict[str, Any]:
    run = session.sm.state.runs[run_id]
    scopes = []
    for kind, scope_id in (
        (BudgetScopeType.RUN, str(run_id)),
        (BudgetScopeType.TASK, str(run.task_id)),
    ):
        usage = session.journal.scope_usage(kind, scope_id)
        scopes.append(
            {
                "scope": kind.value,
                "id": scope_id,
                "cap_micro": usage.cap_micro,
                "spent_micro": usage.spent_micro,
                "hold_micro": usage.hold_micro,
            }
        )
    unsettled = []
    for step, attempt in _attempts(session, run_id):
        view = _reservation(session, attempt)
        if view is None:
            continue
        open_cost = view.state is ReservationState.USAGE_REPORTED and not view.spent_known
        if view.state not in _SETTLED or view.hold_micro != 0 or open_cost:
            unsettled.append(
                {
                    "attempt_id": str(attempt.attempt_id),
                    "step_kind": step.kind.value,
                    "attempt_state": attempt.state.value,
                    "reservation_state": view.state.value,
                    "hold_micro": view.hold_micro,
                    "spent_micro": view.spent_micro,
                    "cost_final": view.spent_known,
                }
            )
    return {
        "run_id": str(run_id),
        "scopes": scopes,
        "unsettled": unsettled,
        "unit": UNIT,
        "real_api_cost": REAL_API_COST,
    }


def stops(session: Session) -> dict[str, Any]:
    return {
        "items": [
            {
                "scope_type": e.scope_type.value,
                "scope_id": e.scope_id,
                "stop_seq": e.stop_seq,
                "status": e.status.value,
            }
            for e in session.journal.stop_entries()
        ]
    }
