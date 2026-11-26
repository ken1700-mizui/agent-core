"""Context IR types (WP-14; B1 2-4, 6; B1.C1-C3, C6, C7, C10, C16, C18).

Everything here is data:

- ``ContextInput`` is what the runtime hands the engine. It has no tier field:
  the tier is derived from the trusted input path (``Channel``), the
  ``ProvenanceClass`` and what the item was derived from, never from the text.
- ``ContextItem`` is one placed entry of the IR (no plaintext: a content hash).
- ``ContextIR`` is the normalized IR that ``context_hash`` covers;
  ``ContextManifest`` records what was included, excluded and checked.
- ``ProtectedFact`` holds goals, done criteria, open errors and pending
  approvals derived from the Journal, apart from the conversation.

Hashes are unkeyed, domain-free SHA-256 over canonical JSON for synthetic data
only (``SYNTHETIC_SHA256``). Real data needs the separate keyed HMACs of C2 6.2
(context, rendering and request keys); that is not implemented.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, NamedTuple, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.model import (
    ContextHash,
    GenerationParams,
    ModelProfile,
    ModelRequest,
    ProfileRef,
    RenderingHash,
    RequestHash,
    TokenizerMethod,
    ToolSpec,
)
from agent_core.types.safety import Policy, RelPath
from agent_core.types.values import (
    Digest,
    PolicyVersion,
    SemVer,
    require_sorted_unique,
    sha256_digest,
)

CONTEXT_IR_VERSION: Final = 1
CONTEXT_MANIFEST_VERSION: Final = 1

ContextItemId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_.:-]{0,127}$")
]
FactId = ContextItemId
PairId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9_.:-]{0,63}$")]
# A reference to where an item came from (Journal seq, event, attempt, file path,
# URI). A closed alphabet: it is rendered next to data and can never hold a delimiter.
SourceRef = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.:@/+-]{1,200}$")]
TokenValue = Annotated[int, Field(strict=True, ge=0, le=100_000_000)]


class Tier(StrEnum):
    """B1 3.1. I0-I3 can instruct; I4 is observed fact; D1-D4 are data only."""

    I0 = "I0"
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"


# Higher rank = less authority. A derived item gets the highest rank of its parts.
TIER_RANK: Final = {tier: rank for rank, tier in enumerate(Tier)}
INSTRUCTION_TIERS: Final = frozenset({Tier.I0, Tier.I1, Tier.I2, Tier.I3})
DATA_TIERS: Final = frozenset({Tier.D1, Tier.D2, Tier.D3, Tier.D4})


class Channel(StrEnum):
    """The trusted path an input arrived through (B1 3.1, 3.4)."""

    SAFETY_POLICY = "SAFETY_POLICY"  # reserved: I1 is built from the Policy itself
    PROJECT_RULES = "PROJECT_RULES"  # I2 only when listed as approved with its digest
    TASK_SPEC = "TASK_SPEC"
    USER_MESSAGE = "USER_MESSAGE"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"  # promotion: not available in Phase 1
    RUNTIME_OBSERVATION = "RUNTIME_OBSERVATION"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    EXTERNAL_DOCUMENT = "EXTERNAL_DOCUMENT"
    MEMORY = "MEMORY"
    MODEL_OUTPUT = "MODEL_OUTPUT"


class ProvenanceClass(StrEnum):
    """How the information came to be (B1 2.1, 3.4)."""

    SYSTEM_STATIC = "SYSTEM_STATIC"  # reserved: I0 is the engine's own text
    USER_INPUT = "USER_INPUT"
    HUMAN_APPROVED = "HUMAN_APPROVED"  # promotion: not available in Phase 1
    RUNTIME_OBSERVED = "RUNTIME_OBSERVED"
    MODEL_PLAN = "MODEL_PLAN"
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"
    EXTERNAL_EXTRACTED = "EXTERNAL_EXTRACTED"
    TOOL_RESULT = "TOOL_RESULT"
    MEMORY_RECALL = "MEMORY_RECALL"


class Priority(StrEnum):
    MUST = "MUST"
    SHOULD = "SHOULD"
    MAY = "MAY"


class InputKind(StrEnum):
    CONTENT = "CONTENT"  # placed by its tier
    HISTORY = "HISTORY"  # a conversation turn
    LATEST = "LATEST"  # the latest input (exactly one, from the user)


class ContextSection(StrEnum):
    """B1 3.3: fixed layer, semi-fixed layer, variable layer."""

    SYSTEM = "SYSTEM"  # I0
    TOOLS = "TOOLS"  # tool definitions (in the request's tools)
    POLICY = "POLICY"  # I1
    PROJECT = "PROJECT"  # I2
    TASK = "TASK"  # I3
    PROTECTED = "PROTECTED"  # ProtectedFact
    OBSERVED = "OBSERVED"  # I4
    PLAN = "PLAN"  # D4
    MEMORY = "MEMORY"  # D3
    RETRIEVED = "RETRIEVED"  # D2 / D1
    HISTORY = "HISTORY"
    LATEST = "LATEST"


SECTION_ORDER: Final = tuple(ContextSection)


class Provenance(FrozenModel):
    channel: Channel
    provenance_class: ProvenanceClass
    source_ref: SourceRef
    derived_from: tuple[ContextItemId, ...]

    @field_validator("derived_from")
    @classmethod
    def _sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_sorted_unique(value, "derived_from")
        return value


class ContextInput(FrozenModel):
    """One candidate item, as the runtime derived it. The text is checked by the
    engine (typed errors that never echo it)."""

    item_id: ContextItemId
    kind: InputKind
    provenance: Provenance
    text: str
    priority: Priority
    seq: Annotated[int, Field(strict=True, ge=0, le=2**62)]
    pair_id: PairId | None


class ApprovedRule(FrozenModel):
    """An owner-approved project rule file: only this path with this digest is I2."""

    path: RelPath
    digest: Digest


class EstimatorRef(FrozenModel):
    estimator_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    version: SemVer


class TokenEstimate(FrozenModel):
    """A token count from a named estimator: never an exact measurement."""

    value: TokenValue
    estimator: EstimatorRef
    exact: Literal[False]


class ContextProfile(FrozenModel):
    """B1 2.4 additions to the model profile that the budget needs. Synthetic."""

    model: ModelProfile
    per_message_overhead: TokenValue
    tool_def_overhead: TokenValue
    # B1 4.1: 15% is a provisional design candidate, not a measured error rate.
    safety_margin_percent: Annotated[int, Field(strict=True, ge=1, le=100)]
    safety_margin_min: TokenValue
    estimator: EstimatorRef


class TokenCheck(FrozenModel):
    """B1 4.4: the final check of the rendered request (an estimate)."""

    method: TokenizerMethod
    exact: Literal[False]
    estimator: EstimatorRef
    context_window: TokenValue
    reserved_output: TokenValue
    limit: TokenValue
    per_message_overhead: TokenValue
    tool_def_overhead: TokenValue
    input_tokens: TokenValue
    margin: TokenValue
    margin_percent: Annotated[int, Field(strict=True, ge=1, le=100)]
    passed: bool


# ---------------------------------------------------------------- ProtectedFact


class FactCategory(StrEnum):
    GOAL = "GOAL"
    PROHIBITION = "PROHIBITION"
    SAFETY_CONSTRAINT = "SAFETY_CONSTRAINT"
    DONE_CRITERIA = "DONE_CRITERIA"
    OPEN_ERROR = "OPEN_ERROR"
    APPROVAL_STATE = "APPROVAL_STATE"
    KEY_DECISION = "KEY_DECISION"
    PENDING_TOOL_PAIR = "PENDING_TOOL_PAIR"


class FactStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class FactAuthority(StrEnum):
    """Who created the fact (B1 3.4): the task's owner input, or the runtime."""

    TASK_SPEC = "TASK_SPEC"
    RUNTIME = "RUNTIME"


class FactSource(FrozenModel):
    kind: Literal["task", "attempt"]
    ref: SourceRef
    evidence: Digest


class ProtectedFact(FrozenModel):
    fact_id: FactId
    version: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    category: FactCategory
    canonical_text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=20_000)]
    authority: FactAuthority
    source: FactSource
    status: FactStatus
    resolution_evidence: SourceRef | None
    previous_version: Annotated[int, Field(strict=True, ge=1)] | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if "\x00" in self.canonical_text:
            raise ValueError("fact text must not contain NUL")
        if (self.status is FactStatus.RESOLVED) != (self.resolution_evidence is not None):
            raise ValueError("a resolved fact names its evidence; an open one has none")
        if (self.previous_version is None) != (self.version == 1):
            raise ValueError("a later version names the version it follows")
        if self.previous_version is not None and self.previous_version != self.version - 1:
            raise ValueError("versions follow one another")
        return self


class JournalFacts(FrozenModel):
    """The facts derived from the Journal and the ids that must be in every context."""

    facts: tuple[ProtectedFact, ...]
    required: tuple[FactId, ...]

    @field_validator("required")
    @classmethod
    def _sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        require_sorted_unique(value, "required")
        return value


class FactChange(FrozenModel):
    kind: Literal["UPDATE", "RESOLVE", "EXPIRE", "SUPERSEDE"]
    actor: Literal["RUNTIME", "OWNER", "MODEL"]
    evidence_ref: SourceRef | None


class SyntheticTexts(FrozenModel):
    """Trusted synthetic texts of the digests the Journal records (Phase 1). Each
    entry's text must hash to its digest."""

    entries: tuple[
        tuple[
            Digest, Annotated[str, StringConstraints(strict=True, min_length=1, max_length=20_000)]
        ],
        ...,
    ]

    @model_validator(mode="after")
    def _bound(self) -> Self:
        for digest, text in self.entries:
            if sha256_digest(text.encode("utf-8")) != digest:
                raise ValueError("a catalog text must hash to its digest")
        require_sorted_unique(tuple(d for d, _ in self.entries), "entries")
        return self

    @classmethod
    def of(cls, *texts: str) -> SyntheticTexts:
        pairs = {sha256_digest(t.encode("utf-8")): t for t in texts}
        return cls(entries=tuple(sorted(pairs.items())))

    def text_for(self, digest: str) -> str | None:
        for key, text in self.entries:
            if key == digest:
                return text
        return None


# ---------------------------------------------------------------- inputs and IR


class ContextInputs(FrozenModel):
    policy: Policy
    approved_rules: tuple[ApprovedRule, ...]
    tools: tuple[ToolSpec, ...]
    journal_facts: JournalFacts
    items: tuple[ContextInput, ...]


class ContextItem(FrozenModel):
    """One placed entry of the IR. No plaintext: the content hash and the estimate."""

    item_id: ContextItemId
    section: ContextSection
    tier: Tier
    channel: Channel | None  # None for the engine's own entries (I0, I1)
    provenance_class: ProvenanceClass
    source_ref: SourceRef
    derived_from: tuple[ContextItemId, ...]
    priority: Priority
    content_hash: Digest
    tokens: TokenEstimate
    protected_fact: FactId | None
    fact_version: Annotated[int, Field(strict=True, ge=1)] | None
    pair_id: PairId | None


class ContextIR(FrozenModel):
    """The normalized IR that ``context_hash`` covers (``ctxhash-v1``): the placed
    entries in order, the tool definitions, the versions and the generation
    settings. Not covered: times, build ids, the rendered text."""

    ir_version: Literal[1]
    hash_algo: Literal["ctxhash-v1"]
    hash_scheme: Literal["SYNTHETIC_SHA256"]
    engine_version: SemVer
    profile: ProfileRef
    estimator: EstimatorRef
    policy_version: PolicyVersion
    params: GenerationParams
    reserved_output: TokenValue
    reasoning_budget: Literal[0]
    tools: tuple[ToolSpec, ...]
    entries: tuple[ContextItem, ...]


class ManifestEntry(FrozenModel):
    item_id: ContextItemId
    content_hash: Digest
    tokens: TokenValue
    position: Annotated[int, Field(strict=True, ge=0)]
    section: ContextSection
    tier: Tier
    provenance_class: ProvenanceClass


class ExcludedEntry(FrozenModel):
    item_id: ContextItemId
    reason: Literal["BUDGET"]


class ContextManifest(FrozenModel):
    """B1 2.5 (Phase 1 subset): what was included, excluded and checked."""

    manifest_version: Literal[1]
    engine_version: SemVer
    adapter_version: Literal["synthetic-adapter@1"]
    policy_version: PolicyVersion
    profile: ProfileRef
    included: tuple[ManifestEntry, ...]
    excluded: tuple[ExcludedEntry, ...]
    protected_coverage: tuple[FactId, ...]
    token_check: TokenCheck
    context_hash: ContextHash
    rendering_hash: RenderingHash
    request_hash: RequestHash
    hash_algos: tuple[Literal["ctxhash-v1"], Literal["renderhash-v1"], Literal["reqhash-v1"]]
    hash_scheme: Literal["SYNTHETIC_SHA256"]
    key_version: None


class ContextBuildResult(NamedTuple):
    """A built and verified context: the IR, the manifest, the model request, and
    the three blobs each hash is computed from (for storage and Replay)."""

    ir: ContextIR
    manifest: ContextManifest
    request: ModelRequest
    ir_blob: bytes
    rendered_blob: bytes
    request_record_blob: bytes
