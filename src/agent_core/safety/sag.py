"""Minimal Phase 1 storage admission for synthetic data (WP-09A; C2 4.1, 12).

Phase 1 handles synthetic data only (PHASE_1 plan section 2). Data counts as
trusted synthetic data only when its origin is an owner-pinned synthetic
source (a fixture or workspace digest in the policy). A ``synthetic`` label,
from a model or anywhere else, is not trust by itself.

Not implemented here: credential scanning, encryption, lineage records and the
full SAG (C2 4.1). Signed synthetic workspace trees and checks of the actual
executable are enforced by later work packages through the same pinned list.
"""

from __future__ import annotations

from agent_core.types.errors import StorageAdmissionError
from agent_core.types.safety import Policy

ALLOWED_LABELS = frozenset({"synthetic"})


def admit_synthetic(policy: Policy, *, origin_ref: str | None, labels: tuple[str, ...]) -> None:
    """Raise ``StorageAdmissionError`` unless the data is from a pinned synthetic source."""
    if type(policy) is not Policy:
        raise StorageAdmissionError("POLICY_REQUIRED")
    if origin_ref is None or origin_ref not in policy.trusted_synthetic_workspaces:
        raise StorageAdmissionError("ORIGIN_NOT_TRUSTED")
    if "synthetic" not in labels or not set(labels) <= ALLOWED_LABELS:
        raise StorageAdmissionError("LABELS_NOT_ALLOWED")
