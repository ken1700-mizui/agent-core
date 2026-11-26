"""ExactApproval (WP-09A; C1 7.2, 7.6; E 38; C1.S6, S27).

- Only the owner channel (``OwnerApprovalChannel``) can produce a valid
  approval: it holds the owner approval key and MACs the canonical approval.
  The model, tool proposals and the runtime never hold the channel, so no code
  path from them can issue an approval.
- A ``SignedApproval`` object still grants nothing: ``check_approval`` needs the
  key, the action it is checked against, the current time and the ledger's use
  and revocation state. Permit issue and consumption check it again (WP-09C).
- Bound to ``action_hash``. Arguments, executable, resources, cost cap,
  reservation and Attempt are all inside that hash, so a change voids it.
- Phase 1 has one owner: only ``ApproverRole.OWNER`` approvals are accepted.
  ScopedApproval and SpendApproval are not implemented.
"""

from __future__ import annotations

import hashlib
import hmac

from agent_core.types.errors import ApprovalRejectedError
from agent_core.types.ledger import LedgerBatch
from agent_core.types.safety import (
    APPROVAL_MAC_DOMAIN,
    ApprovalReason,
    ApproverRole,
    ExactApproval,
    SecretKey,
    SignedApproval,
)
from agent_core.types.serialization import dump_json

OWNER_LEDGER_DOMAIN = b"agent-core/owner-ledger-batch/v1\x00"


def _mac(key: SecretKey, approval: ExactApproval) -> str:
    return hmac.new(
        key.reveal(), APPROVAL_MAC_DOMAIN + dump_json(approval), hashlib.sha256
    ).hexdigest()


class OwnerApprovalChannel:
    """The trusted owner operation path for approvals (Phase 1: in-process)."""

    __slots__ = ("_key",)

    def __init__(self, key: SecretKey) -> None:
        if type(key) is not SecretKey:
            raise TypeError("the owner channel needs a SecretKey")
        self._key = key

    def approve(self, approval: ExactApproval) -> SignedApproval:
        if type(approval) is not ExactApproval:
            raise TypeError("approve takes an ExactApproval")
        return SignedApproval(approval=approval, owner_mac=_mac(self._key, approval))

    def sign_ledger(self, batch: LedgerBatch) -> str:
        """MAC of owner ledger operations (caps, counters, run windows, stop/resume)."""
        if type(batch) is not LedgerBatch:
            raise TypeError("sign_ledger takes a LedgerBatch")
        return ledger_mac(self._key, batch)


def ledger_mac(key: SecretKey, batch: LedgerBatch) -> str:
    return hmac.new(
        key.reveal(), OWNER_LEDGER_DOMAIN + dump_json(batch), hashlib.sha256
    ).hexdigest()


def verify_ledger_mac(key: SecretKey, batch: LedgerBatch, mac: str) -> bool:
    return type(mac) is str and hmac.compare_digest(ledger_mac(key, batch), mac)


def verify_signature(key: SecretKey, signed: SignedApproval) -> ExactApproval:
    if type(signed) is not SignedApproval or type(signed.approval) is not ExactApproval:
        raise ApprovalRejectedError(ApprovalReason.FORGED)
    expected = _mac(key, signed.approval)
    if not hmac.compare_digest(expected, signed.owner_mac):
        raise ApprovalRejectedError(ApprovalReason.FORGED)
    return signed.approval


def check_approval(
    signed: SignedApproval,
    *,
    key: SecretKey,
    action_hash: str,
    tenant_id: str,
    now_ms: int,
    max_ttl_ms: int,
    uses: int,
    revoked: bool,
) -> ExactApproval:
    """Raise ``ApprovalRejectedError`` unless the approval is valid for this action now."""
    approval = verify_signature(key, signed)
    reasons: list[ApprovalReason] = []
    if approval.action_hash != action_hash:
        reasons.append(ApprovalReason.WRONG_ACTION)
    if approval.tenant_id != tenant_id:
        reasons.append(ApprovalReason.WRONG_TENANT)
    if now_ms < approval.granted_at_ms:
        reasons.append(ApprovalReason.NOT_YET_VALID)
    if now_ms >= approval.expires_at_ms:
        reasons.append(ApprovalReason.EXPIRED)
    if approval.expires_at_ms - approval.granted_at_ms > max_ttl_ms:
        reasons.append(ApprovalReason.TTL_TOO_LONG)
    if approval.approver_role is not ApproverRole.OWNER:
        reasons.append(ApprovalReason.ROLE_INSUFFICIENT)
    if uses >= approval.max_uses:
        reasons.append(ApprovalReason.USED)
    if revoked:
        reasons.append(ApprovalReason.REVOKED)
    if reasons:
        raise ApprovalRejectedError(*reasons)
    return approval
