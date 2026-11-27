"""Synthetic done criteria and their per-criterion checks (WP-15, WP15-R07).

A Task's ``done_criteria`` are digests: each is the SHA-256 of a criterion
specification's canonical JSON, so the specification cannot be swapped. The
only kind in Phase 1 is ``file_content``: the file at ``path`` in the managed
tree has exactly the bytes whose SHA-256 is ``expected_digest``.

A ``CriterionCheck`` is the result of one criterion: the specification's path
and expected digest, the digest the verification read observed, and that read's
Attempt. The completion transaction re-derives the criterion's digest from the
path and expected digest, and checks the observed digest against what the
live gate recorded for that read (WP15-R07).
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import AttemptId
from agent_core.types.safety import RelPath
from agent_core.types.serialization import dump_json
from agent_core.types.values import Digest, sha256_digest


class FileCriterion(FrozenModel):
    kind: Literal["file_content"]
    path: RelPath
    expected_digest: Digest

    @field_validator("path")
    @classmethod
    def _concrete(cls, value: str) -> str:
        if "*" in value or ".." in value.split("/"):
            raise ValueError("a criterion names one concrete file")
        return value

    def text(self) -> str:
        return dump_json(self).decode("utf-8")

    def ref(self) -> str:
        return sha256_digest(dump_json(self))


class CriterionCheck(FrozenModel):
    criterion_ref: Digest
    path: RelPath
    expected_digest: Digest
    observed_digest: Digest | None
    read_attempt_id: AttemptId | None

    def specification_ref(self) -> str:
        return FileCriterion(
            kind="file_content", path=self.path, expected_digest=self.expected_digest
        ).ref()
