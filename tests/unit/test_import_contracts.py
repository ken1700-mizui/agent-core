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

REPO_ROOT = Path(__file__).resolve().parents[2]
LINT_IMPORTS = str(Path(sys.executable).parent / "lint-imports")


def _lint_imports(config: Path, python_path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(python_path)
    return subprocess.run(
        [LINT_IMPORTS, "--config", str(config), "--no-cache"],
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


def test_direct_network_import_breaks_real_contract(tmp_path: Path) -> None:
    src = _copy_real_package(tmp_path)
    (src / "agent_core" / "leak.py").write_text('"""Planted."""\n\nimport socket\n')
    result = _lint_imports(REPO_ROOT / "pyproject.toml", src, tmp_path)
    assert result.returncode == 1
    assert "agent_core.leak -> socket" in result.stdout


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
