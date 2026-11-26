"""ruff and mypy, run with this repository's configuration, reject bad code.

Each check has a negative control (bad code is rejected) and a positive control
(good code is accepted), so a check that silently does nothing would fail.
Source files are generated in a temporary directory; no fixture is committed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = str(REPO_ROOT / "pyproject.toml")

GOOD_SOURCE = '''"""Synthetic module."""


def add(a: int, b: int) -> int:
    return a + b
'''


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "synthetic_module.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_ruff_check_accepts_clean_code(tmp_path: Path) -> None:
    path = _write(tmp_path, GOOD_SOURCE)
    result = _run("ruff", "check", "--config", CONFIG, "--no-cache", str(path), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_check_reports_unused_import(tmp_path: Path) -> None:
    path = _write(tmp_path, '"""Synthetic module."""\n\nimport os\n')
    result = _run("ruff", "check", "--config", CONFIG, "--no-cache", str(path), cwd=tmp_path)
    assert result.returncode == 1
    assert "F401" in result.stdout


def test_ruff_check_reports_blind_except(tmp_path: Path) -> None:
    source = (
        '"""Synthetic module."""\n\n\ndef f() -> None:\n'
        "    try:\n        pass\n    except Exception:\n        pass\n"
    )
    path = _write(tmp_path, source)
    result = _run("ruff", "check", "--config", CONFIG, "--no-cache", str(path), cwd=tmp_path)
    assert result.returncode == 1
    assert "BLE001" in result.stdout


def test_ruff_format_check_rejects_unformatted_code(tmp_path: Path) -> None:
    good = _write(tmp_path, GOOD_SOURCE)
    ok = _run("ruff", "format", "--config", CONFIG, "--check", str(good), cwd=tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    bad = _write(tmp_path, "def add( a:int,b:int )->int :\n    return a+b\n")
    result = _run("ruff", "format", "--config", CONFIG, "--check", str(bad), cwd=tmp_path)
    assert result.returncode == 1


def _mypy(tmp_path: Path, path: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        "mypy",
        "--config-file",
        CONFIG,
        "--cache-dir",
        str(tmp_path / ".mypy_cache"),
        str(path),
        cwd=tmp_path,
    )


def test_mypy_accepts_well_typed_code(tmp_path: Path) -> None:
    result = _mypy(tmp_path, _write(tmp_path, GOOD_SOURCE))
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_reports_type_error(tmp_path: Path) -> None:
    source = GOOD_SOURCE + '\n\nRESULT: int = add(1, "2")\n'
    result = _mypy(tmp_path, _write(tmp_path, source))
    assert result.returncode == 1
    assert "[arg-type]" in result.stdout


def test_mypy_strict_mode_rejects_untyped_function(tmp_path: Path) -> None:
    source = '"""Synthetic module."""\n\n\ndef add(a, b):\n    return a + b\n'
    result = _mypy(tmp_path, _write(tmp_path, source))
    assert result.returncode == 1
    assert "[no-untyped-def]" in result.stdout


def test_mypy_rejects_type_ignore_without_error_code(tmp_path: Path) -> None:
    source = GOOD_SOURCE + '\n\nRESULT: int = add(1, "2")  # type: ignore\n'
    result = _mypy(tmp_path, _write(tmp_path, source))
    assert result.returncode == 1
    assert "[ignore-without-code]" in result.stdout
