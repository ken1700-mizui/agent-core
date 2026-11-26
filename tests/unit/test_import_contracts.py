"""import-linter enforces this repository's contracts and detects violations.

Positive control: the real code satisfies the real contracts.
Negative controls: a copy of the real package with a planted violation fails the
real contract, and a synthetic layered package shows that a layers contract
catches an upward import and an import cycle between layers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LINT_IMPORTS = str(Path(sys.executable).parent / "lint-imports")


def _lint_imports(config: Path, python_path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(python_path)
    return subprocess.run(
        [LINT_IMPORTS, str(config), "--no-cache"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _copy_real_package(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    shutil.copytree(
        REPO_ROOT / "src" / "agent_core",
        src / "agent_core",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return src


def test_real_code_satisfies_real_contracts(tmp_path: Path) -> None:
    result = _lint_imports(REPO_ROOT / "pyproject.toml", REPO_ROOT / "src", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


@pytest.mark.parametrize(
    ("statement", "target"),
    [
        ("import socket", "socket"),
        ("import _socket", "_socket"),
        ("import http.client", "http"),
        ("from urllib import request", "urllib"),
        ("import asyncio", "asyncio"),
        ("import multiprocessing", "multiprocessing"),
        ("import ctypes", "ctypes"),
        ("import anthropic", "anthropic"),
    ],
)
def test_direct_forbidden_import_breaks_real_contract(
    tmp_path: Path, statement: str, target: str
) -> None:
    src = _copy_real_package(tmp_path)
    (src / "agent_core" / "leak.py").write_text(f'"""Planted."""\n\n{statement}\n')
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert f"agent_core.leak -> {target}" in result.stdout


def test_indirect_process_spawning_import_breaks_real_contract(tmp_path: Path) -> None:
    src = _copy_real_package(tmp_path)
    (src / "agent_core" / "outer.py").write_text('"""Planted."""\n\nfrom agent_core import inner\n')
    (src / "agent_core" / "inner.py").write_text('"""Planted."""\n\nimport subprocess\n')
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert "agent_core.inner -> subprocess" in result.stdout


def _write_layered_package(root: Path, imports: dict[str, str]) -> None:
    package = root / "layered"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for module in ("high", "mid", "low"):
        (package / f"{module}.py").write_text(imports.get(module, ""))
    (root / "layers.toml").write_text(
        "[tool.importlinter]\n"
        'root_package = "layered"\n\n'
        "[[tool.importlinter.contracts]]\n"
        'name = "synthetic layers"\n'
        'type = "layers"\n'
        'layers = ["layered.high", "layered.mid", "layered.low"]\n'
    )


def test_layers_contract_accepts_downward_imports(tmp_path: Path) -> None:
    _write_layered_package(
        tmp_path, {"high": "from layered import mid\n", "mid": "from layered import low\n"}
    )
    result = _lint_imports(tmp_path / "layers.toml", tmp_path, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_layers_contract_rejects_upward_import(tmp_path: Path) -> None:
    _write_layered_package(tmp_path, {"low": "from layered import high\n"})
    result = _lint_imports(tmp_path / "layers.toml", tmp_path, tmp_path)
    assert result.returncode == 1
    assert "layered.low -> layered.high" in result.stdout


def test_layers_contract_rejects_cycle_between_layers(tmp_path: Path) -> None:
    _write_layered_package(
        tmp_path, {"mid": "from layered import low\n", "low": "from layered import mid\n"}
    )
    result = _lint_imports(tmp_path / "layers.toml", tmp_path, tmp_path)
    assert result.returncode == 1
    assert "layered.low -> layered.mid" in result.stdout


# ---------------------------------------------------------------- WP-02 layer contract


def _plant(src: Path, module: str, statement: str) -> None:
    path = src / Path(*module.split(".")).with_suffix(".py")
    path.write_text(f'"""Planted."""\n\n{statement}\n')


def test_real_layer_contract_is_configured_and_kept(tmp_path: Path) -> None:
    result = _lint_imports(REPO_ROOT / "pyproject.toml", REPO_ROOT / "src", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ports & Adapters layers" in result.stdout
    assert "KEPT" in result.stdout


@pytest.mark.parametrize(
    ("module", "statement", "expected"),
    [
        (
            "agent_core.types.planted",
            "from agent_core.ports import clock",
            "agent_core.types.planted -> agent_core.ports.clock",
        ),
        (
            "agent_core.types.planted",
            "from agent_core.identity import generator",
            "agent_core.types.planted -> agent_core.identity.generator",
        ),
        (
            "agent_core.ports.planted",
            "from agent_core.adapters import system",
            "agent_core.ports.planted -> agent_core.adapters.system",
        ),
        (
            "agent_core.ports.planted",
            "from agent_core.identity import generator",
            "agent_core.ports.planted -> agent_core.identity.generator",
        ),
        (
            "agent_core.identity.planted",
            "from agent_core.adapters import system",
            "agent_core.identity.planted -> agent_core.adapters.system",
        ),
        (
            "agent_core.adapters.planted",
            "from agent_core.identity import generator",
            "agent_core.adapters.planted -> agent_core.identity.generator",
        ),
    ],
)
def test_forbidden_layer_direction_breaks_the_real_contract(
    tmp_path: Path, module: str, statement: str, expected: str
) -> None:
    src = _copy_real_package(tmp_path)
    _plant(src, module, statement)
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert expected in result.stdout


def test_cycle_between_layers_breaks_the_real_contract(tmp_path: Path) -> None:
    src = _copy_real_package(tmp_path)
    _plant(src, "agent_core.types.planted_low", "from agent_core.ports import planted_high")
    _plant(src, "agent_core.ports.planted_high", "from agent_core.types import planted_low")
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert "agent_core.types.planted_low -> agent_core.ports.planted_high" in result.stdout


# -------------------------------------------------- WP-03: core does not depend on SQLite


@pytest.mark.parametrize(
    ("module", "statement", "expected"),
    [
        ("agent_core.types.planted", "import sqlite3", "agent_core.types.planted -> sqlite3"),
        ("agent_core.ports.planted", "import sqlite3", "agent_core.ports.planted -> sqlite3"),
        ("agent_core.identity.planted", "import fcntl", "agent_core.identity.planted -> fcntl"),
        (
            "agent_core.ports.planted",
            "from agent_core.adapters import sqlite_journal",
            "agent_core.ports.planted -> agent_core.adapters.sqlite_journal",
        ),
    ],
)
def test_core_cannot_depend_on_sqlite_or_os_locks(
    tmp_path: Path, module: str, statement: str, expected: str
) -> None:
    src = _copy_real_package(tmp_path)
    _plant(src, module, statement)
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert expected in result.stdout
