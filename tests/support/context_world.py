"""Synthetic inputs for WP-14 Context Engine tests.

A small Journal-derived view (one Task and one Run, built from the common
builders), the trusted synthetic text catalog the facts are checked against, a
synthetic profile and the typical items of each channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_core.context.engine import build_context
from agent_core.context.estimator import SYNTHETIC_ESTIMATOR
from agent_core.context.facts import facts_from_journal
from agent_core.types.context import (
    ApprovedRule,
    Channel,
    ContextInput,
    ContextInputs,
    ContextProfile,
    InputKind,
    JournalFacts,
    Priority,
    Provenance,
    ProvenanceClass,
    SyntheticTexts,
)
from agent_core.types.ids import AttemptId, ModelRequestId
from agent_core.types.model import GenerationParams, ToolSpec
from agent_core.types.values import sha256_digest
from support import builders as b
from support import model_world as m
from support import safety_world as s

GOAL = "Write a short synthetic summary of report A."
CRITERION = "The summary names report A and stays under 100 words."
RULE_PATH = "rules/style.md"
RULE_TEXT = "Use plain sentences."
PARAMS = GenerationParams(max_output_tokens=200, temperature_milli=0, timeout_ms=30_000)
# Deliberately not in name order: the engine sorts them.
TOOLS = (
    ToolSpec(name="write_file", input_schema_ref=b.digest("write-schema")),
    ToolSpec(name="read_file", input_schema_ref=b.digest("read-schema")),
)


def profile(**model: Any) -> ContextProfile:
    values: dict[str, Any] = {
        "model": m.profile(**model),
        "per_message_overhead": 4,
        "tool_def_overhead": 16,
        "safety_margin_percent": 15,  # B1 4.1 provisional candidate, not a measured rate
        "safety_margin_min": 32,
        "estimator": SYNTHETIC_ESTIMATOR,
    }
    return ContextProfile(**values)


@dataclass
class View:
    """The read-only runtime view the fact derivation needs (RuntimeView port)."""

    tasks: dict[Any, Any] = field(default_factory=dict)
    runs: dict[Any, Any] = field(default_factory=dict)
    steps: dict[Any, Any] = field(default_factory=dict)
    attempts: dict[Any, Any] = field(default_factory=dict)
    run_steps: dict[Any, Any] = field(default_factory=dict)
    step_attempts: dict[Any, Any] = field(default_factory=dict)


# One fixed Task and Run, so every build of the same inputs gets the same facts.
_TASK = b.task(
    goal_ref=sha256_digest(GOAL.encode()), done_criteria=(sha256_digest(CRITERION.encode()),)
)
_RUN = b.run(task_id=_TASK.task_id)


def view() -> tuple[View, Any, Any]:
    v = View(tasks={_TASK.task_id: _TASK}, runs={_RUN.run_id: _RUN}, run_steps={_RUN.run_id: ()})
    return v, _TASK, _RUN


def texts(*extra: str) -> SyntheticTexts:
    return SyntheticTexts.of(GOAL, CRITERION, *extra)


def journal_facts() -> JournalFacts:
    v, task, run = view()
    return facts_from_journal(v, task_id=task.task_id, run_id=run.run_id, texts=texts())


def item(
    item_id: str,
    text: str,
    channel: Channel,
    provenance: ProvenanceClass,
    *,
    kind: InputKind = InputKind.CONTENT,
    priority: Priority = Priority.SHOULD,
    seq: int = 1,
    source_ref: str | None = None,
    derived_from: tuple[str, ...] = (),
    pair_id: str | None = None,
    pair_role: Any = None,
) -> ContextInput:
    return ContextInput(
        item_id=item_id,
        kind=kind,
        provenance=Provenance(
            channel=channel,
            provenance_class=provenance,
            source_ref=source_ref if source_ref is not None else f"journal:{seq}",
            derived_from=derived_from,
        ),
        text=text,
        priority=priority,
        seq=seq,
        pair_id=pair_id,
        pair_role=pair_role,
    )


def latest(text: str = "Please write the summary now.", seq: int = 100) -> ContextInput:
    return item(
        "latest",
        text,
        Channel.USER_MESSAGE,
        ProvenanceClass.USER_INPUT,
        kind=InputKind.LATEST,
        priority=Priority.MUST,
        seq=seq,
    )


def standard_items() -> tuple[ContextInput, ...]:
    """One item of each channel, plus history and the latest input."""
    return (
        item(
            "rule.style",
            RULE_TEXT,
            Channel.PROJECT_RULES,
            ProvenanceClass.USER_INPUT,
            source_ref=RULE_PATH,
            seq=1,
        ),
        item(
            "task.note",
            "Prefer the 2026 figures.",
            Channel.USER_MESSAGE,
            ProvenanceClass.USER_INPUT,
            seq=2,
        ),
        item(
            "obs.test",
            "Exit code 0 for the synthetic check.",
            Channel.RUNTIME_OBSERVATION,
            ProvenanceClass.RUNTIME_OBSERVED,
            seq=3,
        ),
        item(
            "plan.1",
            "Plan: read A, then write.",
            Channel.MODEL_OUTPUT,
            ProvenanceClass.MODEL_PLAN,
            seq=4,
        ),
        item(
            "mem.1",
            "Earlier summaries were short.",
            Channel.MEMORY,
            ProvenanceClass.MEMORY_RECALL,
            seq=5,
            priority=Priority.MAY,
        ),
        item(
            "doc.a",
            "Report A: synthetic figures only.",
            Channel.EXTERNAL_DOCUMENT,
            ProvenanceClass.EXTERNAL_EXTRACTED,
            seq=6,
        ),
        item(
            "tool.out",
            "read_file returned 12 lines.",
            Channel.TOOL_OUTPUT,
            ProvenanceClass.TOOL_RESULT,
            seq=7,
        ),
        item(
            "hist.user",
            "Can you help with report A?",
            Channel.USER_MESSAGE,
            ProvenanceClass.USER_INPUT,
            kind=InputKind.HISTORY,
            seq=8,
        ),
        item(
            "hist.model",
            "Yes. I will read it first.",
            Channel.MODEL_OUTPUT,
            ProvenanceClass.MODEL_PLAN,
            kind=InputKind.HISTORY,
            seq=9,
        ),
        latest(),
    )


def inputs(*items: ContextInput, **overrides: Any) -> ContextInputs:
    values: dict[str, Any] = {
        "policy": s.policy(),
        "approved_rules": (ApprovedRule(path=RULE_PATH, digest=sha256_digest(RULE_TEXT.encode())),),
        "tools": TOOLS,
        "journal_facts": journal_facts(),
        "items": items if items else standard_items(),
    }
    values.update(overrides)
    return ContextInputs(**values)


REQUEST_ID = b.new_id(ModelRequestId)
ATTEMPT_ID = b.new_id(AttemptId)


def build(ctx: ContextInputs | None = None, prof: ContextProfile | None = None, **kw: Any) -> Any:
    return build_context(
        inputs=ctx if ctx is not None else inputs(),
        profile=prof if prof is not None else profile(),
        params=kw.pop("params", PARAMS),
        attempt_id=kw.pop("attempt_id", ATTEMPT_ID),
        model_request_id=kw.pop("model_request_id", REQUEST_ID),
    )


def verify(result: Any, ctx: Any = None, prof: Any = None, params: Any = None) -> None:
    """verify_build against the trusted inputs, profile and parameters of a build."""
    from agent_core.context.engine import verify_build

    verify_build(
        result,
        inputs=ctx if ctx is not None else inputs(),
        profile=prof if prof is not None else profile(),
        params=params if params is not None else PARAMS,
    )
