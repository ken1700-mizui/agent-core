"""WP2-11: the dev Task Catalog (D 5, D 6.1, D 6.3, D.V8, D 16.2; D19).

The candidate set (``evaluation/dev/0.1.0``) is data, not code: 20 to 50
synthetic DEV tasks over small synthetic repositories (the clusters). It is a
CANDIDATE: fixing it is a separate owner approval. Nothing here runs a task,
connects to validation or holdout, or executes untrusted code.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_core import catalog as cat
from agent_core.adapters.task_catalog_files import CatalogSplitError, TaskCatalog
from agent_core.types.catalog import (
    CatalogStatus,
    Difficulty,
    TaskKind,
    WorktreeFile,
)
from agent_core.types.evaluation import TaskSource

ROOT = Path(__file__).resolve().parents[2] / "evaluation"
KEY = b"wp2-11 synthetic owner key, test only"


@pytest.fixture(scope="module")
def loaded() -> cat.LoadedCatalog:
    return TaskCatalog.load(ROOT, TaskSource.DEV, "0.1.0")


# ---------------------------------------------------------------- the candidate set


def test_the_candidate_set_has_20_to_50_synthetic_dev_tasks(loaded: cat.LoadedCatalog) -> None:
    assert loaded.status is CatalogStatus.CANDIDATE
    assert 20 <= len(loaded.tasks) <= 50
    assert all(t.source is TaskSource.DEV for t in loaded.tasks)
    assert all(t.provenance.origin == "SYNTHETIC" for t in loaded.tasks)


def test_every_kind_and_difficulty_is_present(loaded: cat.LoadedCatalog) -> None:
    kinds = Counter(t.kind for t in loaded.tasks)
    assert set(kinds) == set(TaskKind)
    assert {t.difficulty for t in loaded.tasks} == set(Difficulty)


def test_every_task_records_criteria_grading_and_capabilities(loaded: cat.LoadedCatalog) -> None:
    for t in loaded.tasks:
        assert t.done_criteria
        assert t.capabilities
        assert t.contamination_notes
        assert t.grading.grader_version
        assert t.repo_key in {r.repo_key for r in loaded.repos}


def test_the_candidates_do_not_duplicate_each_other(loaded: cat.LoadedCatalog) -> None:
    prints = [cat.fingerprint(t) for t in loaded.tasks]
    assert cat.duplicates(prints) == ()


def test_each_task_has_a_start_worktree(loaded: cat.LoadedCatalog) -> None:
    repos = {r.repo_key: r for r in loaded.repos}
    hashes = set()
    for t in loaded.tasks:
        tree = cat.start_worktree(t, repos[t.repo_key])
        assert tree
        hashes.add(cat.worktree_hash(tree))
    assert len(hashes) > len(repos)  # tasks change their repository's start state


def test_the_repository_holds_no_validation_or_holdout_tasks() -> None:
    assert sorted(p.name for p in ROOT.iterdir() if p.is_dir()) == ["dev"]
    for split in (TaskSource.VALIDATION, TaskSource.HOLDOUT):
        with pytest.raises(CatalogSplitError):
            TaskCatalog.load(ROOT, split, "0.1.0")


# ---------------------------------------------------------------- versions and references


def test_the_task_reference_is_fixed_by_its_content(loaded: cat.LoadedCatalog) -> None:
    t = loaded.tasks[0]
    assert t.to_spec().task_ref() == t.to_spec().task_ref()
    changed = t.model_copy(update={"goal": t.goal + " (changed)"})
    assert changed.to_spec().task_ref() != t.to_spec().task_ref()
    bumped = t.model_copy(update={"version": "0.2.0"})
    assert bumped.to_spec().task_ref() != t.to_spec().task_ref()


def test_a_worktree_path_cannot_leave_the_tree() -> None:
    for path in ("../x.py", "/etc/passwd", "a/../../b", "a\\b.py", "./a.py"):
        with pytest.raises(ValidationError):
            WorktreeFile(path=path, content="")


# ---------------------------------------------------------------- split overlap (D 6.3)


def test_the_overlap_check_finds_same_goal_similar_text_and_shared_repositories(
    loaded: cat.LoadedCatalog,
) -> None:
    dev = [cat.fingerprint(t) for t in loaded.tasks]
    a = loaded.tasks[0]
    same_goal = cat.fingerprint(a.model_copy(update={"task_key": "other.copy", "version": "9.0.0"}))
    similar = cat.fingerprint(
        a.model_copy(
            update={
                "task_key": "other.near",
                "repo_key": "other_repo",
                "goal": a.goal + " Also log it.",
            }
        )
    )
    shared_repo = cat.fingerprint(
        loaded.tasks[1].model_copy(
            update={"task_key": "other.repo", "goal": "Unrelated words entirely here for a check."}
        )
    )
    found = cat.check_overlap({"dev": dev, "elsewhere": [same_goal, similar, shared_repo]})
    reasons = {(o.right_key, o.reason) for o in found}
    assert ("other.copy", "SAME_GOAL") in reasons
    assert ("other.near", "SIMILAR") in reasons
    assert ("other.repo", "SAME_REPOSITORY") in reasons


def test_fingerprints_carry_no_task_text(loaded: cat.LoadedCatalog) -> None:
    fp = cat.fingerprint(loaded.tasks[0])
    words = set(loaded.tasks[0].goal.lower().split())
    assert not any(w in repr(fp) for w in words if len(w) > 6)


# ---------------------------------------------------------------- fixing (D.V8, D 16.2)


def test_a_frozen_set_refuses_any_rewrite(loaded: cat.LoadedCatalog) -> None:
    manifest = cat.build_manifest(loaded.repos, loaded.tasks, version="0.1.0")
    frozen = cat.freeze(manifest, KEY)
    assert frozen.manifest.status is CatalogStatus.FROZEN
    cat.verify(frozen, KEY, loaded.repos, loaded.tasks)
    t = loaded.tasks[3]
    for update in (
        {"goal": t.goal + "!"},
        {"done_criteria": (*t.done_criteria, "Also pass a new check.")},
        {"grading": t.grading.model_copy(update={"grader_version": "9.9.9"})},
    ):
        rewritten = tuple(x.model_copy(update=update) if x is t else x for x in loaded.tasks)
        with pytest.raises(cat.CatalogTamperedError):
            cat.verify(frozen, KEY, loaded.repos, rewritten)
    with pytest.raises(cat.CatalogTamperedError):
        cat.verify(frozen, b"another key that is long enough", loaded.repos, loaded.tasks)


def test_a_candidate_set_gives_no_experiment_task_refs(loaded: cat.LoadedCatalog) -> None:
    with pytest.raises(cat.CatalogNotFrozenError):
        cat.experiment_task_refs(loaded)
    manifest = cat.build_manifest(loaded.repos, loaded.tasks, version="0.1.0")
    frozen = cat.freeze(manifest, KEY)
    refs = cat.experiment_task_refs(
        cat.LoadedCatalog(CatalogStatus.FROZEN, loaded.repos, loaded.tasks, frozen), key=KEY
    )
    assert refs == tuple(sorted(t.to_spec().task_ref() for t in loaded.tasks))


def test_a_frozen_catalog_on_disk_is_verified_when_loaded(
    tmp_path: Path, loaded: cat.LoadedCatalog
) -> None:
    import shutil

    shutil.copytree(ROOT, tmp_path / "evaluation")
    root = tmp_path / "evaluation"
    frozen = cat.freeze(cat.build_manifest(loaded.repos, loaded.tasks, version="0.1.0"), KEY)
    TaskCatalog.write_frozen(root, frozen)
    assert (
        TaskCatalog.load(root, TaskSource.DEV, "0.1.0", owner_key=KEY).status
        is CatalogStatus.FROZEN
    )
    with pytest.raises(cat.CatalogTamperedError):
        TaskCatalog.load(root, TaskSource.DEV, "0.1.0")  # frozen, but no key to check it
    tasks = root / "dev" / "0.1.0" / "tasks.json"
    tasks.write_text(tasks.read_text().replace("Fix", "Fixx", 1))
    with pytest.raises(cat.CatalogTamperedError):
        TaskCatalog.load(root, TaskSource.DEV, "0.1.0", owner_key=KEY)
