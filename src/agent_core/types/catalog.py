"""Task Catalog records (WP2-11; D 5, D 6, contract 8, D19).

Only DEV tasks live in this repository. Every task is synthetic: a small
synthetic repository (the cluster, D 8.1) and a start patch give its start
worktree; its goal and done criteria are plain text with objective criteria;
its grading is a reference (method, grader id and version, and the digest of
hidden tests that are written when the set is fixed). ``TaskSpec`` (WP2-01)
carries only references to these.

A set of tasks is a ``CatalogManifest``: a CANDIDATE until the owner fixes it
(a FROZEN manifest with the owner's MAC). Nothing here runs a task.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.evaluation import TaskSource, TaskSpec
from agent_core.types.serialization import dump_json
from agent_core.types.values import Digest, Identifier, SemVer

_DOMAIN = b"agent-core/task-catalog/v1\x00"
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2_000)]
Content = Annotated[str, StringConstraints(strict=True, max_length=16_384)]


def text_digest(kind: str, text: str) -> str:
    return "sha256:" + hashlib.sha256(_DOMAIN + kind.encode() + b"\x00" + text.encode()).hexdigest()


def _hash(kind: str, record: FrozenModel) -> str:
    return (
        "sha256:"
        + hashlib.sha256(_DOMAIN + kind.encode() + b"\x00" + dump_json(record)).hexdigest()
    )


class TaskKind(StrEnum):
    BUG_FIX = "BUG_FIX"
    SMALL_FEATURE = "SMALL_FEATURE"
    REFACTOR = "REFACTOR"
    MULTI_FILE = "MULTI_FILE"
    ADD_TESTS = "ADD_TESTS"
    SPEC_UNDERSTANDING = "SPEC_UNDERSTANDING"
    ERROR_RECOVERY = "ERROR_RECOVERY"
    CONTEXT_MANAGEMENT = "CONTEXT_MANAGEMENT"
    PLANNING = "PLANNING"
    SAFETY_CONDITIONS = "SAFETY_CONDITIONS"


class Difficulty(StrEnum):
    """D 5.1: S, M, L by files touched, lines changed, steps and expert time."""

    S = "S"
    M = "M"
    L = "L"


class GradingMethod(StrEnum):
    HIDDEN_TESTS = "HIDDEN_TESTS"  # tests the agent never sees
    FILE_STATE = "FILE_STATE"  # the files' final contents meet stated predicates
    ANSWER_MATCH = "ANSWER_MATCH"  # an answer compared with a fixed expected one
    TRACE_CHECK = "TRACE_CHECK"  # the journal shows (or never shows) stated actions


class CatalogStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    FROZEN = "FROZEN"


class WorktreeFile(FrozenModel):
    path: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
    content: Content

    @model_validator(mode="after")
    def _safe(self) -> Self:
        p = PurePosixPath(self.path)
        if p.is_absolute() or ".." in p.parts or "\\" in self.path or self.path != str(p):
            raise ValueError("a worktree path is relative, normalized and inside the tree")
        return self


def _sorted_files(files: tuple[WorktreeFile, ...]) -> None:
    paths = [f.path for f in files]
    if paths != sorted(set(paths)):
        raise ValueError("worktree files are sorted and unique by path")


class SyntheticRepo(FrozenModel):
    """A small synthetic repository: one cluster of tasks (D 8.1)."""

    repo_key: Identifier
    version: SemVer
    description: Text
    files: Annotated[tuple[WorktreeFile, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_files(self.files)
        return self

    def repo_hash(self) -> str:
        return _hash("repo", self)


class Grading(FrozenModel):
    method: GradingMethod
    grader_id: Identifier
    grader_version: SemVer
    # Digest of the dev hidden tests; None while a candidate (written at fixing).
    hidden_tests_ref: Digest | None = None


class Provenance(FrozenModel):
    origin: Literal["SYNTHETIC"]
    author: Identifier
    created: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]


class DevTask(FrozenModel):
    task_key: Identifier
    version: SemVer
    source: Literal[TaskSource.DEV]
    synthetic: Literal[True]
    kind: TaskKind
    difficulty: Difficulty
    capabilities: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    goal: Text
    done_criteria: Annotated[tuple[Text, ...], Field(min_length=1)]
    grading: Grading
    repo_key: Identifier
    start_patch: tuple[WorktreeFile, ...]
    provenance: Provenance
    contamination_notes: Text

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        if list(self.capabilities) != sorted(set(self.capabilities)):
            raise ValueError("capabilities are sorted and unique")
        if len(set(self.done_criteria)) != len(self.done_criteria):
            raise ValueError("done criteria are unique")
        _sorted_files(self.start_patch)
        return self

    @property
    def stratum(self) -> str:
        return f"{self.kind.value.lower()}_{self.difficulty.value.lower()}"

    def grader_ref(self) -> str:
        return _hash("grading", self.grading)

    def to_spec(self) -> TaskSpec:
        return TaskSpec(
            task_key=self.task_key,
            version=self.version,
            source=TaskSource.DEV,
            goal_ref=text_digest("goal", self.goal),
            done_criteria=tuple(sorted(text_digest("criterion", c) for c in self.done_criteria)),
            grader_ref=self.grader_ref(),
            repo_cluster=self.repo_key,
            stratum=self.stratum,
        )

    def definition_hash(self) -> str:
        """The whole definition, text included (what fixing protects)."""
        return _hash("task-definition", self)


class ManifestEntry(FrozenModel):
    task_key: Identifier
    task_ref: Digest  # TaskSpec.task_ref()
    definition_hash: Digest
    worktree_hash: Digest


class CatalogManifest(FrozenModel):
    split: Literal[TaskSource.DEV]
    version: SemVer
    status: CatalogStatus
    repos: tuple[tuple[Identifier, Digest], ...]
    tasks: Annotated[tuple[ManifestEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        keys = [t.task_key for t in self.tasks]
        if keys != sorted(set(keys)):
            raise ValueError("manifest tasks are sorted and unique by key")
        repo_keys = [r[0] for r in self.repos]
        if repo_keys != sorted(set(repo_keys)):
            raise ValueError("manifest repos are sorted and unique")
        return self

    def catalog_hash(self) -> str:
        return _hash("catalog", self)


class FrozenCatalog(FrozenModel):
    manifest: CatalogManifest
    owner_mac: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
