"""The runtime matches the approved stack (docs/decisions/PHASE_1_APPROVAL.md)."""

from __future__ import annotations

import sqlite3
import sys
import tomllib
from pathlib import Path

import pydantic

import agent_core

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_is_3_12() -> None:
    assert sys.version_info[:2] == (3, 12)


def test_package_is_importable_and_version_matches_pyproject() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert agent_core.__version__ == project["project"]["version"]


def test_package_is_loaded_from_the_src_layout() -> None:
    assert Path(agent_core.__file__).resolve().is_relative_to(REPO_ROOT / "src" / "agent_core")


def test_pydantic_is_major_version_2() -> None:
    assert pydantic.VERSION.split(".")[0] == "2"


def test_sqlite_supports_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic.db"
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.execute("CREATE TABLE synthetic (n INTEGER NOT NULL)")
        conn.execute("INSERT INTO synthetic (n) VALUES (1)")
        conn.commit()
        assert mode == "wal"
        assert (tmp_path / "synthetic.db-wal").exists()
    finally:
        conn.close()
