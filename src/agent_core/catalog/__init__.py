"""Task Catalog logic (WP2-11; D 5, D 6.3, D.V8, D 16.2). Pure: no I/O.

- ``start_worktree``: the repository's files with the task's start patch.
- ``build_manifest`` / ``freeze`` / ``verify``: a set is fixed only by the
  owner's MAC over its manifest; ``verify`` rebuilds the manifest from the
  definitions given and refuses any difference (a rewritten goal, criterion,
  grader version or file).
- ``fingerprint`` / ``check_overlap`` / ``duplicates``: D 6.3 checks on hashes
  only (task reference, goal digest, repository, hashed word 3-grams), so the
  evaluation side can compare splits without sending task text.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_core.types.catalog import (
    CatalogManifest,
    CatalogStatus,
    DevTask,
    FrozenCatalog,
    ManifestEntry,
    SyntheticRepo,
    WorktreeFile,
    text_digest,
)
from agent_core.types.errors import AgentCoreError
from agent_core.types.evaluation import TaskSource

_MAC_DOMAIN = b"agent-core/task-catalog-freeze/v1\x00"
SIMILARITY_THRESHOLD = 0.6  # PROVISIONAL (D 6.3 names no value)


class CatalogError(AgentCoreError):
    code = "TASK_CATALOG"


class CatalogTamperedError(CatalogError):
    """The definitions differ from the owner's frozen manifest, or its MAC fails."""

    code = "TASK_CATALOG_TAMPERED"
    fatal = True


class CatalogNotFrozenError(CatalogError):
    """A candidate set is never used as an experiment's task set."""

    code = "TASK_CATALOG_NOT_FROZEN"


@dataclass(frozen=True)
class LoadedCatalog:
    status: CatalogStatus
    repos: tuple[SyntheticRepo, ...]
    tasks: tuple[DevTask, ...]
    frozen: FrozenCatalog | None = None


def start_worktree(task: DevTask, repo: SyntheticRepo) -> tuple[WorktreeFile, ...]:
    if task.repo_key != repo.repo_key:
        raise CatalogError("the task names another repository")
    files = {f.path: f for f in repo.files}
    files.update({f.path: f for f in task.start_patch})
    return tuple(files[p] for p in sorted(files))


def worktree_hash(files: Sequence[WorktreeFile]) -> str:
    h = hashlib.sha256(b"agent-core/task-catalog-worktree/v1\x00")
    for f in files:
        for part in (f.path, f.content):
            data = part.encode()
            h.update(len(data).to_bytes(8, "big") + data)
    return "sha256:" + h.hexdigest()


def build_manifest(
    repos: Sequence[SyntheticRepo],
    tasks: Sequence[DevTask],
    *,
    version: str,
    status: CatalogStatus = CatalogStatus.CANDIDATE,
) -> CatalogManifest:
    by_key = {r.repo_key: r for r in repos}
    if len(by_key) != len(repos):
        raise CatalogError("repository keys are unique")
    entries = []
    for t in tasks:
        repo = by_key.get(t.repo_key)
        if repo is None:
            raise CatalogError(f"task {t.task_key} names a missing repository")
        entries.append(
            ManifestEntry(
                task_key=t.task_key,
                task_ref=t.to_spec().task_ref(),
                definition_hash=t.definition_hash(),
                worktree_hash=worktree_hash(start_worktree(t, repo)),
            )
        )
    return CatalogManifest(
        split=TaskSource.DEV,
        version=version,
        status=status,
        repos=tuple(sorted((r.repo_key, r.repo_hash()) for r in repos)),
        tasks=tuple(sorted(entries, key=lambda e: e.task_key)),
    )


def _mac(key: bytes, manifest: CatalogManifest) -> str:
    if len(key) < 16:
        raise ValueError("the owner key is at least 16 bytes")
    return hmac.new(key, _MAC_DOMAIN + manifest.catalog_hash().encode(), hashlib.sha256).hexdigest()


def freeze(manifest: CatalogManifest, key: bytes) -> FrozenCatalog:
    """The owner's act of fixing a set (a separate approval; never the agent's)."""
    fixed = manifest.model_copy(update={"status": CatalogStatus.FROZEN})
    return FrozenCatalog(manifest=fixed, owner_mac=_mac(key, fixed))


def verify(
    frozen: FrozenCatalog,
    key: bytes,
    repos: Sequence[SyntheticRepo],
    tasks: Sequence[DevTask],
) -> CatalogManifest:
    if frozen.manifest.status is not CatalogStatus.FROZEN:
        raise CatalogTamperedError("the manifest is not frozen")
    if not hmac.compare_digest(_mac(key, frozen.manifest), frozen.owner_mac):
        raise CatalogTamperedError("the owner's MAC does not match")
    rebuilt = build_manifest(
        repos, tasks, version=frozen.manifest.version, status=CatalogStatus.FROZEN
    )
    if rebuilt != frozen.manifest:
        raise CatalogTamperedError("the definitions differ from the frozen manifest")
    return rebuilt


def experiment_task_refs(loaded: LoadedCatalog, *, key: bytes | None = None) -> tuple[str, ...]:
    """The task references an ExperimentSpec may use: only from a frozen set."""
    if loaded.status is not CatalogStatus.FROZEN or loaded.frozen is None or key is None:
        raise CatalogNotFrozenError("only a frozen, verified set gives experiment tasks")
    manifest = verify(loaded.frozen, key, loaded.repos, loaded.tasks)
    return tuple(sorted(e.task_ref for e in manifest.tasks))


# ---------------------------------------------------------------- D 6.3 overlap


@dataclass(frozen=True)
class Fingerprint:
    task_key: str
    task_ref: str
    goal_ref: str
    repo_cluster: str
    shingles: frozenset[str]  # hashed word 3-grams of the goal and criteria


def _shingles(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    grams = [" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2))]
    return frozenset(hashlib.sha256(g.encode()).hexdigest()[:16] for g in grams)


def fingerprint(task: DevTask) -> Fingerprint:
    spec = task.to_spec()
    return Fingerprint(
        task_key=task.task_key,
        task_ref=spec.task_ref(),
        goal_ref=text_digest("goal", task.goal.strip().lower()),
        repo_cluster=task.repo_key,
        shingles=_shingles(" ".join((task.goal, *task.done_criteria))),
    )


def _similarity(a: Fingerprint, b: Fingerprint) -> float:
    union = a.shingles | b.shingles
    return len(a.shingles & b.shingles) / len(union) if union else 0.0


@dataclass(frozen=True)
class Overlap:
    left_split: str
    left_key: str
    right_split: str
    right_key: str
    reason: str  # SAME_TASK, SAME_GOAL, SIMILAR, SAME_REPOSITORY


def _reasons(a: Fingerprint, b: Fingerprint, threshold: float) -> list[str]:
    reasons = []
    if a.task_ref == b.task_ref:
        reasons.append("SAME_TASK")
    if a.goal_ref == b.goal_ref:
        reasons.append("SAME_GOAL")
    elif _similarity(a, b) >= threshold:
        reasons.append("SIMILAR")
    if a.repo_cluster == b.repo_cluster:
        reasons.append("SAME_REPOSITORY")
    return reasons


def check_overlap(
    splits: Mapping[str, Sequence[Fingerprint]], *, threshold: float = SIMILARITY_THRESHOLD
) -> tuple[Overlap, ...]:
    """Pairs across different splits that must not be split apart (D 6.3:
    similar tasks and tasks of one repository belong to one split)."""
    names = sorted(splits)
    found: list[Overlap] = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            for a in splits[left]:
                for b in splits[right]:
                    found += [
                        Overlap(left, a.task_key, right, b.task_key, r)
                        for r in _reasons(a, b, threshold)
                    ]
    return tuple(found)


def duplicates(
    prints: Sequence[Fingerprint], *, threshold: float = SIMILARITY_THRESHOLD
) -> tuple[tuple[str, str], ...]:
    """Pairs inside one split with the same goal or similar text."""
    found = []
    for i, a in enumerate(prints):
        for b in prints[i + 1 :]:
            if a.goal_ref == b.goal_ref or _similarity(a, b) >= threshold:
                found.append((a.task_key, b.task_key))
    return tuple(found)
