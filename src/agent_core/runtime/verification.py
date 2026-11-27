"""Synthetic done criteria for the WP-15 VERIFYING stage (E 7.3, 29.5).

A Task's ``done_criteria`` are digests. In Phase 1 each digest names a
deterministic, synthetic specification held in a trusted catalog: the digest is
the SHA-256 of the specification's canonical JSON, so a specification cannot be
swapped for another. Its canonical JSON is also the ProtectedFact text of the
criterion (WP-14).

The only kind is ``file_content``: the file at ``path`` in the managed tree has
exactly the bytes whose SHA-256 is ``expected_digest`` (which also means it
exists). The check reads the file through the authorization path and the broker
(the agent loop); nothing here reads a file. Verification inside the managed tree
is not the verification of a later apply to an external workspace (E 29.5), and
not the independent Evaluation of Phase 2.
"""

from __future__ import annotations

from agent_core.types.base import FrozenModel
from agent_core.types.criteria import FileCriterion

__all__ = ["CriteriaCatalog", "FileCriterion"]


class CriteriaCatalog(FrozenModel):
    criteria: tuple[FileCriterion, ...]

    def spec_for(self, ref: str) -> FileCriterion | None:
        for spec in self.criteria:
            if spec.ref() == ref:
                return spec
        return None
