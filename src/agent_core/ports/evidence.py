"""Evidence verifier port (WP-04).

The runtime asks this port to confirm every piece of evidence before it records
a transition. A typed evidence object proves nothing by itself. Phase 1 has no
production implementation: real confirmation of approvals, reservations and
permits arrives with WP-09, so until then no code path outside tests can move an
attempt to an authorized state.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.records import Attempt, Run
from agent_core.types.transitions import AttemptEvidence, RunEvidence


@runtime_checkable
class EvidenceVerifier(Protocol):
    def verify(self, evidence: AttemptEvidence | RunEvidence, *, subject: Attempt | Run) -> None:
        """Return if the evidence is genuine for ``subject``; raise
        ``EvidenceRejectedError`` otherwise."""
        ...
