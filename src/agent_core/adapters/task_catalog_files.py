"""``TaskCatalog.load(split, version)`` over the repository's data files (WP2-11).

Layout: ``evaluation/dev/<version>/repos.json`` and ``tasks.json`` (the
definitions), and, once the owner fixes the set, ``frozen.json`` (the manifest
with the owner's MAC). Only DEV is in this repository: VALIDATION and HOLDOUT
come through the evaluation side's own source (contract 8), never from here.
A frozen set is always verified with the owner's key; without the key it is
refused rather than read as a candidate.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from agent_core.catalog import CatalogError, CatalogTamperedError, LoadedCatalog, verify
from agent_core.types.catalog import CatalogStatus, DevTask, FrozenCatalog, SyntheticRepo
from agent_core.types.evaluation import TaskSource
from agent_core.types.serialization import dump_json

_REPOS = TypeAdapter(tuple[SyntheticRepo, ...])
_TASKS = TypeAdapter(tuple[DevTask, ...])


class CatalogSplitError(CatalogError):
    """Only DEV tasks are in this repository."""

    code = "TASK_CATALOG_SPLIT"


class TaskCatalog:
    @staticmethod
    def _dir(root: Path, split: TaskSource, version: str) -> Path:
        if split is not TaskSource.DEV:
            raise CatalogSplitError("validation and holdout tasks are not in this repository")
        return root / "dev" / version

    @classmethod
    def load(
        cls, root: Path, split: TaskSource, version: str, *, owner_key: bytes | None = None
    ) -> LoadedCatalog:
        folder = cls._dir(root, split, version)
        try:
            repos = _REPOS.validate_json((folder / "repos.json").read_bytes())
            tasks = _TASKS.validate_json((folder / "tasks.json").read_bytes())
        except (OSError, ValidationError) as exc:
            raise CatalogError(f"the catalog {version} cannot be read") from exc
        frozen_path = folder / "frozen.json"
        if not frozen_path.exists():
            return LoadedCatalog(CatalogStatus.CANDIDATE, repos, tasks)
        if owner_key is None:
            raise CatalogTamperedError("a frozen set is read only with the owner's key")
        try:
            frozen = FrozenCatalog.model_validate_json(frozen_path.read_bytes())
        except ValidationError as exc:
            raise CatalogTamperedError("the frozen manifest does not parse") from exc
        try:
            verify(frozen, owner_key, repos, tasks)
        except CatalogError as exc:
            if isinstance(exc, CatalogTamperedError):
                raise
            raise CatalogTamperedError(str(exc)) from exc
        return LoadedCatalog(CatalogStatus.FROZEN, repos, tasks, frozen)

    @classmethod
    def write_frozen(cls, root: Path, frozen: FrozenCatalog) -> None:
        """Write the owner's frozen manifest beside the definitions."""
        folder = cls._dir(root, frozen.manifest.split, frozen.manifest.version)
        (folder / "frozen.json").write_bytes(dump_json(frozen))


def dump_definitions(
    repos: tuple[SyntheticRepo, ...], tasks: tuple[DevTask, ...]
) -> tuple[str, str]:
    """Canonical, readable JSON for the data files."""
    return (
        json.dumps(json.loads(_REPOS.dump_json(repos)), indent=1, ensure_ascii=False) + "\n",
        json.dumps(json.loads(_TASKS.dump_json(tasks)), indent=1, ensure_ascii=False) + "\n",
    )
