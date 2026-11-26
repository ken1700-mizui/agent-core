"""ProtectedFact derivation and state changes (WP-14; B1 2.2, 3.4, C3, C18).

Phase 1 derives facts only from the Journal (through ``RuntimeView``) and from
trusted synthetic texts whose digests the Journal records:

- ``GOAL`` and ``DONE_CRITERIA`` from the Task: the text must hash to the Task's
  ``goal_ref`` / ``done_criteria`` digest (authority: the task's owner input).
- ``OPEN_ERROR`` from attempts of the Run that ended FAILED_DEFINITE or
  OUTCOME_UNKNOWN, and ``APPROVAL_STATE`` from attempts waiting for approval
  (authority: the runtime, which observed them).

Every derived fact is OPEN and required in every context. Changes follow B1 3.4:
a model can never change a fact; the runtime may resolve an open error with
evidence (a new version; the old one stays as it was). Updating, expiring and
superseding need an owner path that does not exist yet, so they are refused.
"""

from __future__ import annotations

from agent_core.ports.context import RuntimeView
from agent_core.types.context import (
    FactAuthority,
    FactCategory,
    FactChange,
    FactSource,
    FactStatus,
    JournalFacts,
    ProtectedFact,
    SyntheticTexts,
)
from agent_core.types.errors import ProtectedFactError
from agent_core.types.ids import RunId, TaskId
from agent_core.types.records import AttemptState
from agent_core.types.values import sha256_digest

_ERROR_STATES = (AttemptState.FAILED_DEFINITE, AttemptState.OUTCOME_UNKNOWN)


def _fact(
    fact_id: str,
    category: FactCategory,
    text: str,
    authority: FactAuthority,
    source: FactSource,
) -> ProtectedFact:
    return ProtectedFact(
        fact_id=fact_id,
        version=1,
        category=category,
        canonical_text=text,
        authority=authority,
        source=source,
        status=FactStatus.OPEN,
        resolution_evidence=None,
        previous_version=None,
    )


def facts_from_journal(
    view: RuntimeView, *, task_id: TaskId, run_id: RunId, texts: SyntheticTexts
) -> JournalFacts:
    task = view.tasks.get(task_id)
    run = view.runs.get(run_id)
    if task is None or run is None or run.task_id != task_id:
        raise ProtectedFactError("FACT_SOURCE_UNKNOWN")
    facts: list[ProtectedFact] = []
    goal = texts.text_for(task.goal_ref)
    if goal is None:
        raise ProtectedFactError("FACT_TEXT_UNAVAILABLE")
    source = FactSource(kind="task", ref=str(task_id), evidence=task.goal_ref)
    facts.append(_fact(f"goal.{task_id}", FactCategory.GOAL, goal, FactAuthority.TASK_SPEC, source))
    for index, criterion in enumerate(task.done_criteria):
        text = texts.text_for(criterion)
        if text is None:
            raise ProtectedFactError("FACT_TEXT_UNAVAILABLE")
        source = FactSource(kind="task", ref=str(task_id), evidence=criterion)
        facts.append(
            _fact(
                f"done.{task_id}.{index}",
                FactCategory.DONE_CRITERIA,
                text,
                FactAuthority.TASK_SPEC,
                source,
            )
        )
    for step_id in view.run_steps.get(run_id, ()):
        for attempt_id in view.step_attempts.get(step_id, ()):
            attempt = view.attempts[attempt_id]
            state = attempt.state
            evidence = sha256_digest(f"{attempt_id}:{state.value}".encode())
            source = FactSource(kind="attempt", ref=str(attempt_id), evidence=evidence)
            if state in _ERROR_STATES:
                facts.append(
                    _fact(
                        f"error.{attempt_id}",
                        FactCategory.OPEN_ERROR,
                        f"Attempt {attempt_id} ended {state.value}.",
                        FactAuthority.RUNTIME,
                        source,
                    )
                )
            elif state is AttemptState.APPROVAL_PENDING:
                facts.append(
                    _fact(
                        f"approval.{attempt_id}",
                        FactCategory.APPROVAL_STATE,
                        f"Attempt {attempt_id} waits for approval.",
                        FactAuthority.RUNTIME,
                        source,
                    )
                )
    required = tuple(sorted(f.fact_id for f in facts if f.status is FactStatus.OPEN))
    return JournalFacts(facts=tuple(facts), required=required)


def apply_fact_change(fact: ProtectedFact, change: FactChange) -> ProtectedFact:
    """The next version of ``fact`` after ``change``, or a refusal (B1 3.4)."""
    if change.actor == "MODEL":
        raise ProtectedFactError("FACT_CHANGE_BY_MODEL")
    if not (
        change.kind == "RESOLVE"
        and change.actor == "RUNTIME"
        and fact.category is FactCategory.OPEN_ERROR
    ):
        raise ProtectedFactError("FACT_CHANGE_UNSUPPORTED")
    if fact.status is not FactStatus.OPEN:
        raise ProtectedFactError("FACT_NOT_OPEN")
    if change.evidence_ref is None:
        raise ProtectedFactError("FACT_EVIDENCE_REQUIRED")
    return ProtectedFact.model_validate(
        {
            **fact.model_dump(),
            "version": fact.version + 1,
            "previous_version": fact.version,
            "status": FactStatus.RESOLVED,
            "resolution_evidence": change.evidence_ref,
        }
    )
