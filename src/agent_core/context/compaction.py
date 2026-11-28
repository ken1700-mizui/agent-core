"""Context Engine v1: compaction, selection, summaries, handoff (WP2-05; B1 5.1-5.2).

Pure functions over ``ContextInput`` tuples, run before the WP-14 builder
(``build_context``), which still places, checks and hashes the result.

- ``compact``: by the usage of the token budget (synthetic estimator), B1.ST1
  (old tool results replaced by a stub naming the pair, size, first line and a
  handle) from ``st1_at_ppm``, B1.ST2 (head and tail kept, error lines first,
  the middle replaced by a marker with a handle) from ``st2_at_ppm``, then a
  selection that fits the budget: MUST items always, the rest by priority and
  recency, a tool call and its result together or not at all.
- Only data reaching the model as observations or retrieved text is compacted
  (tool output, external documents, memory, model output). Never instructions
  (task, project rules, policy), user messages, ProtectedFacts or the latest
  input. A compacted item keeps its provenance exactly: it gains no authority.
- ST6: every replaced text is kept in the ``HandleStore``; ``get`` returns the
  original. ST4: ``StructuredSummary`` (from a scripted or fake provider; no
  model here) is checked by ``verify_summary`` and placed by ``summary_item`` as
  model output derived from its sources: the builder then makes it data (D4).
  ST5: ``make_handoff`` keeps the authoritative facts (by id, from the Journal)
  apart from the summary; ``reconstruct`` builds the next context from them.
- ``CompactionRecord`` and ``compaction_hash`` are deterministic.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import Field, StringConstraints

from agent_core.context.estimator import estimate_text
from agent_core.types.base import FrozenModel
from agent_core.types.context import (
    Channel,
    ContextInput,
    ContextItemId,
    FactCategory,
    FactId,
    FactStatus,
    InputKind,
    Priority,
    ProtectedFact,
    Provenance,
    ProvenanceClass,
)
from agent_core.types.errors import AgentCoreError
from agent_core.types.values import SemVer

__all__ = ["FactCategory"]

PPM = 1_000_000
_COMPACTABLE = frozenset(
    {Channel.TOOL_OUTPUT, Channel.EXTERNAL_DOCUMENT, Channel.MEMORY, Channel.MODEL_OUTPUT}
)
# Text that tries to speak as a higher layer is refused in summaries (B1 3.1).
_INSTRUCTION_MARKERS = ("system:", "developer:", "policy:", "ignore the", "<instruction")
ClaimText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2_000)]


class SummaryRejectedError(AgentCoreError):
    code = "SUMMARY_REJECTED"


class CompactionPolicy(FrozenModel):
    """``CompactionPolicy@version`` (B1 5.1). Thresholds are provisional."""

    policy_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    version: SemVer
    st1_at_ppm: Annotated[int, Field(strict=True, ge=0, le=PPM)]
    st2_at_ppm: Annotated[int, Field(strict=True, ge=0, le=PPM)]
    keep_recent_results: Annotated[int, Field(strict=True, ge=0, le=1_000)]
    truncate_over_chars: Annotated[int, Field(strict=True, ge=64, le=10_000_000)]
    truncate_keep_chars: Annotated[int, Field(strict=True, ge=16, le=10_000)]


class CompactionRecord(FrozenModel):
    strategy: str
    policy: str
    item_ids: tuple[ContextItemId, ...]
    handles: tuple[str, ...]


@dataclass
class HandleStore:
    """B1.ST6 ``context.get_stored``: the originals of replaced texts."""

    texts: dict[str, str] = field(default_factory=dict)

    def put(self, text: str) -> str:
        handle = "h:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        self.texts[handle] = text
        return handle

    def get(self, handle: str) -> str:
        if handle not in self.texts:
            raise KeyError("no such handle")
        return self.texts[handle]


@dataclass(frozen=True)
class CompactionResult:
    items: tuple[ContextInput, ...]
    records: tuple[CompactionRecord, ...]
    store: HandleStore
    compaction_hash: str


def _tokens(items: Iterable[ContextInput]) -> int:
    return sum(estimate_text(i.text) for i in items)


def _compactable(item: ContextInput) -> bool:
    return (
        item.kind is not InputKind.LATEST
        and item.priority is not Priority.MUST
        and item.provenance.channel in _COMPACTABLE
    )


def _with_text(item: ContextInput, text: str) -> ContextInput:
    return item.model_copy(update={"text": text})


def _st1(
    items: list[ContextInput], policy: CompactionPolicy, store: HandleStore
) -> CompactionRecord | None:
    results = [
        i
        for i in items
        if i.pair_role == "RESULT" and _compactable(i) and not i.text.startswith("[")
    ]
    old = sorted(results, key=lambda i: i.seq)[: max(0, len(results) - policy.keep_recent_results)]
    if not old:
        return None
    ids, handles = [], []
    for victim in old:
        handle = store.put(victim.text)
        first = victim.text.splitlines()[0][:120] if victim.text else ""
        stub = (
            f"[tool result cleared: pair {victim.pair_id}, {len(victim.text)} chars,"
            f" first line: {first!r}, handle {handle}]"
        )
        items[items.index(victim)] = _with_text(victim, stub)
        ids.append(victim.item_id)
        handles.append(handle)
    return CompactionRecord(
        strategy="B1.ST1",
        policy=f"{policy.policy_id}@{policy.version}",
        item_ids=tuple(ids),
        handles=tuple(handles),
    )


def _st2(
    items: list[ContextInput], policy: CompactionPolicy, store: HandleStore
) -> CompactionRecord | None:
    ids, handles = [], []
    for item in list(items):
        if not _compactable(item) or len(item.text) <= policy.truncate_over_chars:
            continue
        if item.text.startswith("[tool result cleared"):
            continue
        handle = store.put(item.text)
        keep = policy.truncate_keep_chars
        errors = [
            line
            for line in item.text.splitlines()
            if any(w in line.lower() for w in ("error", "exception", "traceback", "fail"))
        ][:10]
        omitted = len(item.text) - 2 * keep
        parts = [
            *(f"[error line] {e}" for e in errors),
            item.text[:keep],
            f"[{omitted} chars omitted, handle {handle}]",
            item.text[-keep:],
        ]
        items[items.index(item)] = _with_text(item, "\n".join(parts))
        ids.append(item.item_id)
        handles.append(handle)
    if not ids:
        return None
    return CompactionRecord(
        strategy="B1.ST2",
        policy=f"{policy.policy_id}@{policy.version}",
        item_ids=tuple(ids),
        handles=tuple(handles),
    )


def _select(items: list[ContextInput], budget: int) -> list[ContextInput]:
    """Kept always: everything that is not compactable data. Then by priority and
    recency within the budget. A tool call and its result are one unit."""
    units: dict[str, list[ContextInput]] = {}
    for item in items:
        units.setdefault(item.pair_id or f"item:{item.item_id}", []).append(item)
    rank = {Priority.MUST: 0, Priority.SHOULD: 1, Priority.MAY: 2}
    keep: set[str] = set()
    used = 0

    def order(key: str) -> tuple[int, int]:
        unit = units[key]
        # Instructions, user messages, MUST items and the latest input are never
        # dropped: a context that cannot hold them fails in the builder instead.
        must = any(not _compactable(i) for i in unit)
        return (0 if must else 1 + min(rank[i.priority] for i in unit), -max(i.seq for i in unit))

    for key in sorted(units, key=order):
        unit = units[key]
        cost = _tokens(unit)
        must = order(key)[0] == 0
        if must or used + cost <= budget:
            keep.add(key)
            used += cost
    return [i for i in items if (i.pair_id or f"item:{i.item_id}") in keep]


def compact(
    items: tuple[ContextInput, ...], *, budget_tokens: int, policy: CompactionPolicy
) -> CompactionResult:
    store = HandleStore()
    working = list(items)
    records: list[CompactionRecord] = []
    if budget_tokens > 0 and _tokens(working) * PPM >= policy.st1_at_ppm * budget_tokens:
        found = _st1(working, policy, store)
        if found is not None:
            records.append(found)
    if budget_tokens > 0 and _tokens(working) * PPM >= policy.st2_at_ppm * budget_tokens:
        found = _st2(working, policy, store)
        if found is not None:
            records.append(found)
    if _tokens(working) > budget_tokens:
        working = _select(working, budget_tokens)
    body = json.dumps(
        {
            "policy": policy.model_dump(mode="json"),
            "records": [r.model_dump(mode="json") for r in records],
            "items": [[i.item_id, hashlib.sha256(i.text.encode()).hexdigest()] for i in working],
        },
        sort_keys=True,
    ).encode()
    digest = "sha256:" + hashlib.sha256(b"agent-core/compaction/v1\x00" + body).hexdigest()
    return CompactionResult(tuple(working), tuple(records), store, digest)


# ---------------------------------------------------------------- ST4 summaries


class Claim(FrozenModel):
    text: ClaimText
    sources: Annotated[tuple[ContextItemId, ...], Field(min_length=1)]
    # Facts the claim says are resolved: never accepted from a summary (B1 5.2).
    resolves: tuple[FactId, ...] = ()


class StructuredSummary(FrozenModel):
    """B1.ST4 format: goal, decisions, changed files, open issues, next steps."""

    summary_id: ContextItemId
    goal: Claim
    decisions: tuple[Claim, ...]
    changed_files: tuple[Claim, ...]
    open_issues: tuple[Claim, ...]
    next_steps: tuple[Claim, ...]

    def claims(self) -> tuple[Claim, ...]:
        return (
            self.goal,
            *self.decisions,
            *self.changed_files,
            *self.open_issues,
            *self.next_steps,
        )

    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({s for c in self.claims() for s in c.sources}))

    def text(self) -> str:
        def section(name: str, claims: tuple[Claim, ...]) -> str:
            lines = [f"- {c.text} [from {', '.join(c.sources)}]" for c in claims] or ["- (none)"]
            return f"{name}:\n" + "\n".join(lines)

        return "\n".join(
            [
                section("Goal", (self.goal,)),
                section("Decisions", self.decisions),
                section("Changed files", self.changed_files),
                section("Open issues", self.open_issues),
                section("Next steps", self.next_steps),
            ]
        )


def verify_summary(
    summary: StructuredSummary,
    items: Iterable[ContextInput],
    facts: Iterable[ProtectedFact],
) -> None:
    """Refuses a summary whose claims name unknown sources, close a ProtectedFact,
    or speak as an instruction. Its acceptance gives it no authority (it is D4)."""
    known = {i.item_id for i in items}
    open_facts = {f.fact_id for f in facts if f.status is FactStatus.OPEN}
    all_facts = {f.fact_id for f in facts}
    for claim in summary.claims():
        if any(s not in known for s in claim.sources):
            raise SummaryRejectedError("a claim names a source that is not in the context")
        if claim.resolves and (set(claim.resolves) & (open_facts | all_facts)):
            raise SummaryRejectedError("a summary cannot change a ProtectedFact")
        lowered = claim.text.lower()
        if any(marker in lowered for marker in _INSTRUCTION_MARKERS):
            raise SummaryRejectedError("a summary cannot speak as an instruction")


def summary_item(summary: StructuredSummary, *, seq: int) -> ContextInput:
    return ContextInput(
        item_id=summary.summary_id,
        kind=InputKind.CONTENT,
        provenance=Provenance(
            channel=Channel.MODEL_OUTPUT,
            provenance_class=ProvenanceClass.MODEL_HYPOTHESIS,
            source_ref=f"summary:{summary.summary_id}",
            derived_from=summary.sources(),
        ),
        text=summary.text(),
        priority=Priority.SHOULD,
        seq=seq,
        pair_id=None,
        pair_role=None,
    )


# ---------------------------------------------------------------- ST5 handoff


@dataclass(frozen=True)
class Handoff:
    """B1.ST5: the authoritative part (fact ids from the Journal) and the summary
    (no authority) kept apart; the source items the summary needs come along."""

    authoritative: tuple[FactId, ...]
    summary: StructuredSummary
    sources: tuple[ContextInput, ...]


def make_handoff(
    *, facts: Iterable[ProtectedFact], summary: StructuredSummary, items: Iterable[ContextInput]
) -> Handoff:
    pool = tuple(items)
    fact_list = tuple(facts)
    verify_summary(summary, pool, fact_list)
    needed = set(summary.sources())
    return Handoff(
        authoritative=tuple(sorted(f.fact_id for f in fact_list)),
        summary=summary,
        sources=tuple(i for i in pool if i.item_id in needed),
    )


def reconstruct(handoff: Handoff, *, latest: ContextInput, seq: int) -> tuple[ContextInput, ...]:
    """The next context: the summary's sources, the summary (D4), the latest input.
    The ProtectedFacts are added by the builder from the Journal, as always."""
    return (*handoff.sources, summary_item(handoff.summary, seq=seq), latest)
