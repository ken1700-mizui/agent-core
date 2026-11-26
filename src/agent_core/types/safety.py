"""Safety types (WP-09A; C1 4, 6, 7, 11; E 3, 5, 38; C2 4.1, 6.2).

Everything here is data. A typed object grants nothing by itself:

- ``Policy`` and ``RunGrant`` are owner-trusted inputs to the PDP. They are
  installed through the trusted owner path (WP-09C ledger), never taken from a
  model or a tool proposal.
- ``ActionDescriptor`` is built from the actual operation by the trusted
  builder (``agent_core.safety.descriptor``), which recomputes every hash. Its
  ``action_hash`` binds approvals, reservations and permits (C1 7.1, E 38).
- ``ExactApproval`` is valid only with the owner channel's MAC and only while
  the trusted ledger records it as unused and not revoked (WP-09C).

Hashes are unkeyed, domain-tagged SHA-256 over canonical JSON, for synthetic
data only (``HashScheme.SYNTHETIC_SHA256``). Confidential arguments need keyed
HMACs (C1 7.1, C2 6.2); that scheme is declared but refused in Phase 1.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.cost import PeriodId
from agent_core.types.ids import (
    ApprovalId,
    AttemptId,
    ExperimentId,
    PepInstanceId,
    PermitId,
    ReceiptId,
    ReservationId,
    RunGrantId,
    RunId,
    StepId,
    TaskId,
    TenantId,
    UserId,
)
from agent_core.types.model import ModelId, ProfileRef, RequestHash, Usage
from agent_core.types.serialization import dump_json
from agent_core.types.tools import ResourceAccess, ToolEffectClass, ToolId, ToolVersion
from agent_core.types.values import (
    Budget,
    Digest,
    Epoch,
    Identifier,
    PolicyVersion,
    SemVer,
    TimestampMs,
    require_unique,
)

ACTION_HASH_DOMAIN = b"agent-core/action-descriptor/v1\x00"
POLICY_HASH_DOMAIN = b"agent-core/policy/v1\x00"
GRANT_HASH_DOMAIN = b"agent-core/run-grant/v1\x00"
DECISION_HASH_DOMAIN = b"agent-core/policy-decision/v1\x00"
APPROVAL_MAC_DOMAIN = b"agent-core/exact-approval/v1\x00"

# Relative POSIX path or pattern ("workspace/a.txt", "workspace/**"). No "..",
# no absolute path, no backslash, no empty segment.
RelPath = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9._*-]+(/[A-Za-z0-9._*-]+)*$",
    ),
]


def _domain_hash(domain: bytes, model: FrozenModel) -> str:
    return "sha256:" + hashlib.sha256(domain + dump_json(model)).hexdigest()


def _no_dotdot(values: tuple[str, ...]) -> None:
    for value in values:
        if ".." in value.split("/"):
            raise ValueError("paths are relative and never contain '..'")


class SecretKey:
    """Key bytes that never appear in repr, str, logs or serialized records."""

    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        if type(value) is not bytes or len(value) < 32:
            raise ValueError("a key is at least 32 random bytes")
        self._value = value

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return "<SecretKey>"

    __str__ = __repr__

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("keys are not serializable")


class LedgerKeys:
    """The ledger's keys: the owner channel key (verifies owner approvals and owner
    operations) and the permit MAC key (P1 only; C2 6.2). Never serialized."""

    __slots__ = ("owner", "permit")

    def __init__(self, *, owner: SecretKey, permit: SecretKey) -> None:
        if type(owner) is not SecretKey or type(permit) is not SecretKey:
            raise TypeError("ledger keys are SecretKeys")
        if owner.reveal() == permit.reveal():
            raise ValueError("the owner key and the permit key are separate keys")
        self.owner = owner
        self.permit = permit

    def __repr__(self) -> str:
        return "<LedgerKeys>"

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("keys are not serializable")


class HashScheme(StrEnum):
    SYNTHETIC_SHA256 = "SYNTHETIC_SHA256"
    # Required for confidential data (C2 6.2, WP-14). Refused in Phase 1.
    KEYED_HMAC_SHA256 = "KEYED_HMAC_SHA256"


class ApproverRole(StrEnum):
    OWNER = "OWNER"
    USER = "USER"


class StopScopeType(StrEnum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    RUN = "RUN"


class StopStatus(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    RESUMING = "RESUMING"


class ModelProviderKind(StrEnum):
    """Phase 1 allows FAKE and REPLAY only. LIVE needs the Phase 3A gates."""

    FAKE = "FAKE"
    REPLAY = "REPLAY"
    LIVE = "LIVE"


class ActionKind(StrEnum):
    TOOL_CALL = "TOOL_CALL"
    MODEL_CALL = "MODEL_CALL"


class DenyReason(StrEnum):
    """Fixed reasons a PDP decision or a check refuses an action."""

    TENANT_MISMATCH = "TENANT_MISMATCH"
    GRANT_SCOPE_MISMATCH = "GRANT_SCOPE_MISMATCH"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    DESCRIPTOR_EXPIRED = "DESCRIPTOR_EXPIRED"
    DESCRIPTOR_OUTLIVES_GRANT = "DESCRIPTOR_OUTLIVES_GRANT"
    DESCRIPTOR_MISMATCH = "DESCRIPTOR_MISMATCH"
    TOOL_NOT_IN_POLICY = "TOOL_NOT_IN_POLICY"
    TOOL_NOT_GRANTED = "TOOL_NOT_GRANTED"
    TOOL_KIND_NOT_ALLOWED = "TOOL_KIND_NOT_ALLOWED"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    EFFECT_NOT_ALLOWED = "EFFECT_NOT_ALLOWED"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    SANDBOX_NOT_AVAILABLE = "SANDBOX_NOT_AVAILABLE"
    LIVE_PROVIDER_FORBIDDEN = "LIVE_PROVIDER_FORBIDDEN"
    MODEL_NOT_GRANTED = "MODEL_NOT_GRANTED"
    RESOURCE_OUTSIDE_TOOL = "RESOURCE_OUTSIDE_TOOL"
    RESOURCE_OUTSIDE_GRANT = "RESOURCE_OUTSIDE_GRANT"
    RESOURCE_DENIED = "RESOURCE_DENIED"
    UNTRUSTED_WORKSPACE = "UNTRUSTED_WORKSPACE"
    COST_CAP_EXCEEDS_GRANT = "COST_CAP_EXCEEDS_GRANT"
    STALE_POLICY = "STALE_POLICY"
    STALE_ACCESS_EPOCH = "STALE_ACCESS_EPOCH"
    STALE_OWNER = "STALE_OWNER"
    STOPPED = "STOPPED"
    STOP_STATE_INCONSISTENT = "STOP_STATE_INCONSISTENT"
    HASH_SCHEME_NOT_ALLOWED = "HASH_SCHEME_NOT_ALLOWED"
    COST_BASIS_UNTRUSTED = "COST_BASIS_UNTRUSTED"


class ApprovalReason(StrEnum):
    """Fixed reasons an ExactApproval is not valid for an action."""

    FORGED = "FORGED"
    WRONG_ACTION = "WRONG_ACTION"
    WRONG_TENANT = "WRONG_TENANT"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"
    TTL_TOO_LONG = "TTL_TOO_LONG"
    ROLE_INSUFFICIENT = "ROLE_INSUFFICIENT"
    USED = "USED"
    REVOKED = "REVOKED"
    NOT_RECORDED = "NOT_RECORDED"


class ToolPin(FrozenModel):
    """``tool_id@version@manifest_hash`` plus the executable's ``artifact_hash`` (C1 4.2)."""

    tool_id: ToolId
    version: ToolVersion
    manifest_hash: Digest
    artifact_hash: Digest


class PathScope(FrozenModel):
    """Relative patterns: an exact path, or ``dir/**`` for everything under ``dir/``."""

    read: tuple[RelPath, ...]
    write: tuple[RelPath, ...]

    @model_validator(mode="after")
    def _relative(self) -> Self:
        _no_dotdot(self.read + self.write)
        return self


class TargetResource(FrozenModel):
    path: RelPath
    access: ResourceAccess

    @model_validator(mode="after")
    def _concrete(self) -> Self:
        _no_dotdot((self.path,))
        if "*" in self.path:
            raise ValueError("a target resource is a concrete path, not a pattern")
        return self


class ModelTarget(FrozenModel):
    profile: ProfileRef
    provider: ModelProviderKind


class ToolCostBasis(FrozenModel):
    """WP09-R01: a tool call's required amount is the trusted manifest's estimate."""

    kind: Literal["tool"]
    estimate: Budget


class ModelCostBasis(FrozenModel):
    """WP09-R01: a model call's required amount comes from the owner's installed
    synthetic price table and ceilings verified from the request itself: the
    rendered input's UTF-8 bytes (a conservative token ceiling; real token
    counting is WP-14) and the request's ``max_output_tokens``."""

    kind: Literal["model"]
    price_table_id: Identifier
    price_table_version: SemVer
    model_id: ModelId
    max_input_tokens: Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=10_000_000)]


CostBasis = Annotated[ToolCostBasis | ModelCostBasis, Field(discriminator="kind")]


class ToolContent(FrozenModel):
    """The actual tool arguments (canonical JSON) the descriptor was built from."""

    kind: Literal["tool"]
    canonical_input: Annotated[str, StringConstraints(strict=True, max_length=1_048_576)]


class ModelContent(FrozenModel):
    """The exact request record and rendered input (UTF-8 JSON) of a model call."""

    kind: Literal["model"]
    request_record: Annotated[
        str, StringConstraints(strict=True, min_length=2, max_length=1_048_576)
    ]
    rendered_input: Annotated[
        str, StringConstraints(strict=True, min_length=2, max_length=1_048_576)
    ]


ActionContent = Annotated[ToolContent | ModelContent, Field(discriminator="kind")]


class Policy(FrozenModel):
    """The owner's static, versioned policy (C1 13: owner-edited file). No defaults:
    every limit is stated explicitly and is never an approved value by itself."""

    policy_version: PolicyVersion
    tenant_id: TenantId
    tools: tuple[ToolPin, ...]
    allowed_effects: tuple[ToolEffectClass, ...]
    approval_effects: tuple[ToolEffectClass, ...]
    approval_tools: tuple[ToolId, ...]
    model_providers: tuple[ModelProviderKind, ...]
    deny_resources: tuple[RelPath, ...]
    trusted_synthetic_workspaces: tuple[Digest, ...]
    # The owner's installed synthetic price table every model call is priced with.
    price_table_id: Identifier
    price_table_version: SemVer
    max_permit_ttl_ms: Annotated[int, Field(strict=True, ge=1, le=600_000)]
    max_approval_ttl_ms: Annotated[int, Field(strict=True, ge=1, le=86_400_000)]

    @model_validator(mode="after")
    def _phase_1(self) -> Self:
        require_unique([(t.tool_id, t.version) for t in self.tools], "tool pins")
        if ModelProviderKind.LIVE in self.model_providers:
            raise ValueError("live providers are not allowed in Phase 1")
        _no_dotdot(self.deny_resources)
        return self

    def policy_hash(self) -> str:
        return _domain_hash(POLICY_HASH_DOMAIN, self)


class RunGrant(FrozenModel):
    """What one Run may do (C1 4.2). Computed when the Run starts; never widened."""

    run_grant_id: RunGrantId
    tenant_id: TenantId
    # Budget scopes come from the grant, never from the caller (WP09-R06).
    user_id: UserId
    period_id: PeriodId
    experiment_id: ExperimentId | None
    task_id: TaskId
    run_id: RunId
    tools: tuple[ToolPin, ...]
    files: PathScope
    model_profiles: tuple[ProfileRef, ...]
    max_cost_per_action: Budget
    policy_version: PolicyVersion
    access_epoch_at_grant: Epoch
    issued_at_ms: TimestampMs
    expires_at_ms: TimestampMs

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("a grant expires after it is issued")
        return self

    def grant_hash(self) -> str:
        return _domain_hash(GRANT_HASH_DOMAIN, self)


class ActionDescriptor(FrozenModel):
    """The normalized content of one operation (C1 7.1, E 38.1)."""

    # Version 2 adds ``cost_basis`` (WP09-R01). Version 1 is not accepted.
    descriptor_version: Literal[2]
    hash_scheme: HashScheme
    kind: ActionKind
    tenant_id: TenantId
    task_id: TaskId
    run_id: RunId
    step_id: StepId
    attempt_id: AttemptId
    tool: ToolPin | None
    model: ModelTarget | None
    sandbox_profile_hash: Digest | None
    args_hash: Digest
    target_resources: tuple[TargetResource, ...]
    destinations: tuple[()]
    request_hash: RequestHash | None
    effect_class: ToolEffectClass
    workspace_ref: Digest | None
    policy_version: PolicyVersion
    access_epoch: Epoch
    cost_cap: Budget
    cost_basis: CostBasis
    reservation_id: ReservationId
    expires_at_ms: TimestampMs

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind is ActionKind.TOOL_CALL:
            if self.tool is None or self.model is not None or self.request_hash is not None:
                raise ValueError("a tool call names a tool and no model request")
            if self.effect_class is ToolEffectClass.MODEL_CALL:
                raise ValueError("a tool call is not a model call")
            if not isinstance(self.cost_basis, ToolCostBasis):
                raise ValueError("a tool call is priced by its manifest estimate")
        else:
            if self.model is None or self.tool is not None or self.request_hash is None:
                raise ValueError("a model call names a model and its request hash")
            if self.effect_class is not ToolEffectClass.MODEL_CALL or self.target_resources:
                raise ValueError("a model call has the MODEL_CALL effect and no resources")
            if (
                not isinstance(self.cost_basis, ModelCostBasis)
                or self.cost_basis.model_id != self.model.profile.model_id
            ):
                raise ValueError("a model call is priced by the price table for its model")
        require_unique([(r.path, r.access) for r in self.target_resources], "target resources")
        return self

    def action_hash(self) -> str:
        return _domain_hash(ACTION_HASH_DOMAIN, self)


class StopEntry(FrozenModel):
    scope_type: StopScopeType
    scope_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
    stop_seq: Epoch
    status: StopStatus


class SafetyContext(FrozenModel):
    """The current trusted state the PDP decides against (read from the ledger)."""

    now_ms: TimestampMs
    tenant_id: TenantId
    policy_version: PolicyVersion
    access_epoch: Epoch
    journal_owner_epoch: Epoch
    runtime_owner_epoch: Epoch
    stops: tuple[StopEntry, ...]
    denied_resources: tuple[RelPath, ...]


class PolicyDecision(FrozenModel):
    decision_version: Literal[1]
    allowed: bool
    reasons: tuple[DenyReason, ...]
    action_hash: Digest
    policy_hash: Digest
    grant_hash: Digest
    policy_version: PolicyVersion
    access_epoch: Epoch
    approval_required: bool
    decided_at_ms: TimestampMs

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.allowed != (not self.reasons):
            raise ValueError("a decision allows exactly when it has no deny reason")
        return self

    def decision_ref(self) -> str:
        return _domain_hash(DECISION_HASH_DOMAIN, self)


class ExactApproval(FrozenModel):
    """Approval of one action (C1 7.2, 7.6): bound to ``action_hash``, used once."""

    approval_version: Literal[1]
    approval_id: ApprovalId
    tenant_id: TenantId
    action_hash: Digest
    approver_id: UserId
    approver_role: ApproverRole
    granted_at_ms: TimestampMs
    expires_at_ms: TimestampMs
    max_uses: Literal[1]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.expires_at_ms <= self.granted_at_ms:
            raise ValueError("an approval expires after it is granted")
        return self


class SignedApproval(FrozenModel):
    """An approval with the owner channel's MAC. Still grants nothing until the
    trusted ledger records it and checks it at permit issue and consumption."""

    approval: ExactApproval
    owner_mac: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


# ---------------------------------------------------------------- permits and receipts (WP-09C)

PERMIT_MAC_DOMAIN = b"agent-core/permit/v1\x00"
AUDIT_HASH_DOMAIN = b"agent-core/mandatory-audit/v1\x00"


class Consumer(StrEnum):
    """The single PEP that consumes a permit (C1 7.5). Phase 1 has the broker only."""

    BROKER = "BROKER"
    GATEWAY = "GATEWAY"


class PermitState(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"


class ReceiptState(StrEnum):
    ISSUED = "ISSUED"
    CLAIMED = "CLAIMED"
    USED = "USED"
    REVOKED = "REVOKED"
    # A live gate reported that the operation was not started (WP09-R04).
    NOT_DISPATCHED = "NOT_DISPATCHED"


class PermitReason(StrEnum):
    """Fixed reasons a permit is not issued or not consumed, or a receipt not claimed."""

    KEY_MISSING = "KEY_MISSING"
    UNKNOWN_PERMIT = "UNKNOWN_PERMIT"
    MAC_INVALID = "MAC_INVALID"
    PERMIT_DIFFERS = "PERMIT_DIFFERS"
    NOT_ISSUED = "NOT_ISSUED"
    ATTEMPT_MISMATCH = "ATTEMPT_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    CONSUMER_MISMATCH = "CONSUMER_MISMATCH"
    ACTION_MISMATCH = "ACTION_MISMATCH"
    EXPIRED = "EXPIRED"
    TTL_TOO_LONG = "TTL_TOO_LONG"
    STALE_ACCESS_EPOCH = "STALE_ACCESS_EPOCH"
    STALE_OWNER = "STALE_OWNER"
    STALE_REVOCATION = "STALE_REVOCATION"
    STOPPED = "STOPPED"
    RESOURCE_DENIED = "RESOURCE_DENIED"
    DECISION_MISSING = "DECISION_MISSING"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    RESERVATION_INVALID = "RESERVATION_INVALID"
    INTENT_MISSING = "INTENT_MISSING"
    UNKNOWN_RECEIPT = "UNKNOWN_RECEIPT"
    RECEIPT_DIFFERS = "RECEIPT_DIFFERS"
    RECEIPT_NOT_ISSUED = "RECEIPT_NOT_ISSUED"
    RECEIPT_NOT_CLAIMED = "RECEIPT_NOT_CLAIMED"
    PEP_MISMATCH = "PEP_MISMATCH"
    REPORT_MAC_INVALID = "REPORT_MAC_INVALID"
    REPORT_MISMATCH = "REPORT_MISMATCH"
    REPORT_USED = "REPORT_USED"
    ALREADY_STARTED = "ALREADY_STARTED"
    USAGE_COST_MISMATCH = "USAGE_COST_MISMATCH"
    GATE_BUSY = "GATE_BUSY"
    RUN_CANCELLED = "RUN_CANCELLED"
    ELAPSED_MS = "ELAPSED_MS"
    GRANT_EXPIRED = "GRANT_EXPIRED"


class PermitBody(FrozenModel):
    """C1 7.4 plus the bindings of E 38.2: action, reservation, consumer, epochs."""

    permit_version: Literal[1]
    permit_id: PermitId
    tenant_id: TenantId
    run_id: RunId
    attempt_id: AttemptId
    action_hash: Digest
    reservation_id: ReservationId
    consumer: Consumer
    access_epoch: Epoch
    owner_epoch: Epoch
    revocation_gen: Epoch
    policy_version: PolicyVersion
    issued_at_ms: TimestampMs
    expires_at_ms: TimestampMs


class Permit(FrozenModel):
    """A permit as handed to its consumer. The MAC is checked only by P1, inside the
    consumption transaction (C1 7.4 item 1, C2 6.2). Holding one grants nothing."""

    body: PermitBody
    mac: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class Receipt(FrozenModel):
    """Issued by the consumption transaction (E 3.3); used once by its PEP."""

    receipt_version: Literal[1]
    receipt_id: ReceiptId
    permit_id: PermitId
    tenant_id: TenantId
    attempt_id: AttemptId
    reservation_id: ReservationId
    consumer: Consumer
    pep_instance_id: PepInstanceId
    # The owner epoch of the consumption: a receipt never outlives a restart (WP09-R03).
    owner_epoch: Epoch
    revocation_gen: Epoch
    expires_at_ms: TimestampMs


class DispatchAuthorization(FrozenModel):
    """What the gate's callback receives after the start was committed (WP09-R03).
    It is information, not authority: nothing accepts it back, and starting again
    needs the receipt, which is already used."""

    receipt: Receipt
    action_hash: Digest
    descriptor: ActionDescriptor


class AuditView(FrozenModel):
    """One mandatory audit record as read back (IDs and codes only)."""

    audit_seq: Annotated[int, Field(strict=True, ge=1)]
    action: str
    decision: str
    reason: str | None
    prev_hash: str | None
    record_hash: str


# ---------------------------------------------------------------- gate reports (WP09-R04)

NOT_DISPATCHED_DOMAIN = b"agent-core/pep-not-dispatched/v1\x00"
EXECUTION_REPORT_DOMAIN = b"agent-core/pep-execution-report/v1\x00"
MacHex = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class NotDispatchedReport(FrozenModel):
    """A live gate's statement that a consumed permit's operation was not started.
    MAC with the P1 key; bound to receipt, permit, attempt, reservation, owner
    epoch and gate instance; used once."""

    report_version: Literal[1]
    receipt_id: ReceiptId
    permit_id: PermitId
    attempt_id: AttemptId
    reservation_id: ReservationId
    owner_epoch: Epoch
    pep_instance_id: PepInstanceId
    reported_at_ms: TimestampMs
    mac: MacHex


class ExecutionReport(FrozenModel):
    """A live gate's result of a started operation: the outcome and this call's
    usage. The ledger derives the cost itself (synthetic billing) and refuses a
    stated cost that differs."""

    report_version: Literal[1]
    receipt_id: ReceiptId
    attempt_id: AttemptId
    reservation_id: ReservationId
    owner_epoch: Epoch
    pep_instance_id: PepInstanceId
    outcome: Literal["COMPLETED", "FAILED_DEFINITE"]
    usage: Usage
    final: bool
    mac: MacHex
