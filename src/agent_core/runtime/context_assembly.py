"""The runtime's trusted assembly of the Context Engine's inputs (WP14-R01).

The engine is pure and its input types prove nothing by themselves. This is the
one path that builds ``ContextInputs`` for the agent loop, from trusted sources
only:

- the policy: the tenant's installed policy in the Journal;
- the approved project rules: the trusted rule catalog (owner-provided);
- the tools: the registered tools that both the Run grant and the policy pin;
- the ProtectedFacts: derived again from the current Journal state;
- the turns: what the runtime itself recorded during the Run. A turn names only
  its kind. The channel and provenance come from the kind, fixed here: a model
  response or a model tool call is D4, a tool result D1, a document D2, a
  runtime observation I4, a user input I3. A model cannot choose them.

Task, Run, tenant and grant are checked against one another, and the Run must be
RUNNING. Human promotion and Memory are not available (later work).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_core.context.facts import facts_from_journal
from agent_core.ports.context import RuntimeView
from agent_core.ports.ledger import LedgerPort
from agent_core.safety.descriptor import pin_of
from agent_core.types.base import FrozenModel
from agent_core.types.context import (
    ApprovedRule,
    Channel,
    ContextBuildResult,
    ContextInput,
    ContextInputs,
    InputKind,
    PairId,
    Priority,
    Provenance,
    ProvenanceClass,
    SourceRef,
    SyntheticTexts,
)
from agent_core.types.errors import ContextInputError, ContextVerificationError
from agent_core.types.ids import RunId, TaskId, TenantId
from agent_core.types.model import ToolSpec
from agent_core.types.records import RunStatus
from agent_core.types.safety import ActionDescriptor, ModelCostBasis, RelPath
from agent_core.types.tools import ToolRegistration
from agent_core.types.values import require_sorted_unique, sha256_digest


class TurnKind(StrEnum):
    USER_INPUT = "USER_INPUT"
    MODEL_RESPONSE = "MODEL_RESPONSE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    OBSERVATION = "OBSERVATION"
    DOCUMENT = "DOCUMENT"


# Fixed here, never chosen by whoever produced the text.
_PATH: Final = {
    TurnKind.USER_INPUT: (Channel.USER_MESSAGE, ProvenanceClass.USER_INPUT),
    TurnKind.MODEL_RESPONSE: (Channel.MODEL_OUTPUT, ProvenanceClass.MODEL_PLAN),
    TurnKind.TOOL_CALL: (Channel.MODEL_OUTPUT, ProvenanceClass.MODEL_PLAN),
    TurnKind.TOOL_RESULT: (Channel.TOOL_OUTPUT, ProvenanceClass.TOOL_RESULT),
    TurnKind.OBSERVATION: (Channel.RUNTIME_OBSERVATION, ProvenanceClass.RUNTIME_OBSERVED),
    TurnKind.DOCUMENT: (Channel.EXTERNAL_DOCUMENT, ProvenanceClass.EXTERNAL_EXTRACTED),
}
_HISTORY: Final = frozenset(
    {TurnKind.USER_INPUT, TurnKind.MODEL_RESPONSE, TurnKind.TOOL_CALL, TurnKind.TOOL_RESULT}
)
RuleText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200_000)]


class TrustedRule(FrozenModel):
    path: RelPath
    text: RuleText


class RuleCatalog(FrozenModel):
    """The owner-approved project rules (path and exact text)."""

    rules: tuple[TrustedRule, ...]

    @field_validator("rules")
    @classmethod
    def _sorted(cls, value: tuple[TrustedRule, ...]) -> tuple[TrustedRule, ...]:
        require_sorted_unique(tuple(r.path for r in value), "rules")
        return value


class Turn(FrozenModel):
    """One record of the Run as the runtime made it: its kind, text and order."""

    kind: TurnKind
    text: str
    seq: Annotated[int, Field(strict=True, ge=1, le=2**62)]
    ref: SourceRef
    pair_id: PairId | None = None
    latest: bool = False

    @model_validator(mode="after")
    def _shape(self) -> Self:
        paired = self.kind in (TurnKind.TOOL_CALL, TurnKind.TOOL_RESULT)
        if paired != (self.pair_id is not None):
            raise ValueError("tool calls and results carry their pair id; nothing else does")
        if self.latest and self.kind is not TurnKind.USER_INPUT:
            raise ValueError("only a user input is the latest input")
        return self


def _rule_input(rule: TrustedRule) -> ContextInput:
    return ContextInput(
        item_id="rule." + rule.path.replace("/", ":").lower(),
        kind=InputKind.CONTENT,
        provenance=Provenance(
            channel=Channel.PROJECT_RULES,
            provenance_class=ProvenanceClass.USER_INPUT,
            source_ref=rule.path,
            derived_from=(),
        ),
        text=rule.text,
        priority=Priority.MUST,
        seq=0,
        pair_id=None,
        pair_role=None,
    )


def _turn_input(turn: Turn) -> ContextInput:
    channel, provenance = _PATH[turn.kind]
    if turn.latest:
        kind = InputKind.LATEST
    elif turn.kind in _HISTORY:
        kind = InputKind.HISTORY
    else:
        kind = InputKind.CONTENT
    role = {TurnKind.TOOL_CALL: "CALL", TurnKind.TOOL_RESULT: "RESULT"}.get(turn.kind)
    return ContextInput(
        item_id=f"turn.{turn.seq}",
        kind=kind,
        provenance=Provenance(
            channel=channel, provenance_class=provenance, source_ref=turn.ref, derived_from=()
        ),
        text=turn.text,
        priority=Priority.MUST if turn.latest else Priority.SHOULD,
        seq=turn.seq,
        pair_id=turn.pair_id,
        pair_role=role,  # type: ignore[arg-type]
    )


def assemble_context_inputs(
    *,
    view: RuntimeView,
    ledger: LedgerPort,
    tenant_id: TenantId,
    task_id: TaskId,
    run_id: RunId,
    tools: tuple[ToolRegistration, ...],
    rules: RuleCatalog,
    texts: SyntheticTexts,
    turns: tuple[Turn, ...],
) -> ContextInputs:
    policy = ledger.policy(str(tenant_id))
    if policy is None or policy.tenant_id != tenant_id:
        raise ContextInputError("POLICY_MISSING")
    task = view.tasks.get(task_id)
    run = view.runs.get(run_id)
    if task is None or run is None or run.task_id != task_id:
        raise ContextInputError("RUN_TASK_MISMATCH")
    if run.status is not RunStatus.RUNNING:
        raise ContextInputError("RUN_NOT_RUNNING")
    grant = ledger.run_grant(str(run_id))
    if grant is None or grant.tenant_id != tenant_id or grant.task_id != task_id:
        raise ContextInputError("GRANT_MISSING")
    allowed = set(grant.tools) & set(policy.tools)
    specs = tuple(
        ToolSpec(name=r.manifest.tool_id, input_schema_ref=r.manifest.manifest_hash)
        for r in tools
        if pin_of(r) in allowed
    )
    seqs = [t.seq for t in turns]
    if len(set(seqs)) != len(seqs):
        raise ContextInputError("TURN_ORDER_INVALID")
    return ContextInputs(
        policy=policy,
        approved_rules=tuple(
            ApprovedRule(path=r.path, digest=sha256_digest(r.text.encode("utf-8")))
            for r in rules.rules
        ),
        tools=specs,
        journal_facts=facts_from_journal(view, task_id=task_id, run_id=run_id, texts=texts),
        items=tuple(_rule_input(r) for r in rules.rules) + tuple(_turn_input(t) for t in turns),
    )


def bind_model_descriptor(result: ContextBuildResult, descriptor: ActionDescriptor) -> None:
    """WP14-R04: the WP-09 model descriptor (and so the reservation) is for exactly
    this request: its request hash, profile, and ceilings derived from the rendered
    bytes and the requested output."""
    basis = descriptor.cost_basis
    if (
        descriptor.request_hash != result.request.request_hash
        or descriptor.model is None
        or descriptor.model.profile != result.request.profile
        or not isinstance(basis, ModelCostBasis)
        or basis.max_input_tokens != len(result.rendered_blob)
        or basis.max_output_tokens != result.request.params.max_output_tokens
    ):
        raise ContextVerificationError("MODEL_DESCRIPTOR_MISMATCH")
