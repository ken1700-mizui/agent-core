"""Only approved dependencies are declared, locked and installed.

Approved set: docs/decisions/PHASE_1_APPROVAL.md section 2.2 and
docs/adr/0006-toolchain.md.
"""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "agent-core"

APPROVED_RUNTIME = {"pydantic"}
APPROVED_DEV = {"pytest", "ruff", "mypy", "import-linter"}
NEVER_ALLOWED = {
    "anthropic",
    "openai",
    "langchain",
    "langchain-core",
    "litellm",
    "mcp",
    "fastapi",
    "requests",
    "httpx",
}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    assert match is not None, requirement
    return _normalize(match.group(0))


def _load_toml(name: str) -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / name).read_text(encoding="utf-8"))


def _locked_versions() -> dict[str, str]:
    packages = _load_toml("uv.lock")["package"]
    return {_normalize(p["name"]): p["version"] for p in packages if p["name"] != PROJECT_NAME}


def test_declared_runtime_dependencies_are_exactly_the_approved_set() -> None:
    declared = {
        _requirement_name(r) for r in _load_toml("pyproject.toml")["project"]["dependencies"]
    }
    assert declared == APPROVED_RUNTIME


def test_declared_dev_dependencies_are_exactly_the_approved_set() -> None:
    groups = _load_toml("pyproject.toml")["dependency-groups"]
    assert set(groups) == {"dev"}
    assert {_requirement_name(r) for r in groups["dev"]} == APPROVED_DEV


def test_lock_file_direct_dependencies_match_pyproject() -> None:
    packages = _load_toml("uv.lock")["package"]
    (project,) = [p for p in packages if p["name"] == PROJECT_NAME]
    runtime = {_normalize(d["name"]) for d in project.get("dependencies", [])}
    dev = {_normalize(d["name"]) for d in project["dev-dependencies"]["dev"]}
    assert runtime == APPROVED_RUNTIME
    assert dev == APPROVED_DEV


def test_lock_file_contains_no_prohibited_package() -> None:
    assert set(_locked_versions()) & NEVER_ALLOWED == set()


def test_every_installed_distribution_is_locked_at_the_locked_version() -> None:
    locked = _locked_versions()
    installed = {
        _normalize(dist.metadata["Name"]): dist.version for dist in metadata.distributions()
    }
    unlocked = sorted(set(installed) - set(locked))
    mismatched = sorted(
        f"{name} installed={version} locked={locked[name]}"
        for name, version in installed.items()
        if name in locked and version != locked[name]
    )
    assert unlocked == []
    assert mismatched == []


def test_approved_direct_dependencies_are_installed() -> None:
    locked = _locked_versions()
    for name in sorted(APPROVED_RUNTIME | APPROVED_DEV):
        assert metadata.version(name) == locked[name]
