"""Static check: agent_core never executes commands or code dynamically.

Phase 1 has no arbitrary command execution (ROADMAP 0.1, AGENTS.md 4.12).
import-linter forbids modules such as ``subprocess``; this check covers what
import-linter cannot see: calls through ``os``/``posix`` (``os.system``,
``os.exec*``, ``os.spawn*``, ``os.fork`` ...), dynamic imports that would
bypass import-linter (``__import__``, ``importlib.import_module``), and
``eval``/``exec``/``compile``.

Limits: this is an AST check. It does not see code generated at run time; the
ban on ``eval``/``exec``/dynamic imports is what keeps that out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "src" / "agent_core"
CLI_DIR = REPO_ROOT / "src" / "agent_core_cli"

OS_MODULES = frozenset({"os", "posix", "nt"})
FORBIDDEN_OS_ATTRIBUTES = frozenset(
    {
        "system",
        "popen",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "startfile",
        *(f"exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
        *(f"spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
    }
)
DYNAMIC_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})
IMPORTLIB_FORBIDDEN = frozenset({"import_module", "__import__"})


def find_violations(source: str, filename: str = "<synthetic>") -> list[str]:
    """Return ``"line: description"`` for each forbidden construct in ``source``."""
    tree = ast.parse(source, filename=filename)
    os_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    builtins_aliases: set[str] = set()
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name in OS_MODULES:
                    os_aliases.add(bound)
                elif alias.name == "importlib":
                    importlib_aliases.add(bound)
                elif alias.name == "builtins":
                    builtins_aliases.add(bound)
        elif isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if node.module in OS_MODULES and names & (FORBIDDEN_OS_ATTRIBUTES | {"*"}):
                violations.append(f"{node.lineno}: from {node.module} import {sorted(names)}")
            if node.module == "importlib" and names & (IMPORTLIB_FORBIDDEN | {"*"}):
                violations.append(f"{node.lineno}: from importlib import {sorted(names)}")
            if node.module == "builtins" and names & (DYNAMIC_BUILTINS | {"*"}):
                violations.append(f"{node.lineno}: from builtins import {sorted(names)}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner, attribute = node.value.id, node.attr
            if owner in os_aliases and attribute in FORBIDDEN_OS_ATTRIBUTES:
                violations.append(f"{node.lineno}: {owner}.{attribute}")
            if owner in importlib_aliases and attribute in IMPORTLIB_FORBIDDEN:
                violations.append(f"{node.lineno}: {owner}.{attribute}")
            if owner in builtins_aliases and attribute in DYNAMIC_BUILTINS:
                violations.append(f"{node.lineno}: {owner}.{attribute}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in DYNAMIC_BUILTINS:
                violations.append(f"{node.lineno}: {name}()")
            if (
                name == "getattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in os_aliases | importlib_aliases | builtins_aliases
            ):
                violations.append(f"{node.lineno}: getattr({node.args[0].id}, ...)")
    return sorted(set(violations))


def test_agent_core_has_no_process_execution_or_dynamic_code() -> None:
    # WP-10: the CLI (the composition root, outside agent_core) is checked too.
    files = sorted(PACKAGE_DIR.rglob("*.py")) + sorted(CLI_DIR.rglob("*.py"))
    assert files, "the check must inspect at least one file"
    report = {
        str(path.relative_to(REPO_ROOT)): found
        for path in files
        if (found := find_violations(path.read_text(encoding="utf-8"), str(path)))
    }
    assert report == {}


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.system('true')\n",
        "import os as o\no.execv('/bin/true', ['true'])\n",
        "import os\nos.spawnlp(os.P_WAIT, 'true', 'true')\n",
        "import os\nos.posix_spawn('/bin/true', ['true'], {})\n",
        "import os\nos.fork()\n",
        "import os\nos.popen('true')\n",
        "import posix\nposix.system('true')\n",
        "from os import system\n",
        "from os import *\n",
        "import os\ngetattr(os, 'sys' + 'tem')('true')\n",
        "__import__('subprocess')\n",
        "import importlib\nimportlib.import_module('subprocess')\n",
        "from importlib import import_module\n",
        "eval('1 + 1')\n",
        "exec('x = 1')\n",
        "compile('1', '<s>', 'eval')\n",
        "import builtins\nbuiltins.exec('x = 1')\n",
    ],
)
def test_forbidden_constructs_are_detected(source: str) -> None:
    assert find_violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "import os\npath = os.path.join('a', 'b')\n",
        "import os\nvalue = os.environ.get('X')\n",
        "def system() -> None:\n    pass\n\nsystem()\n",
        "import importlib.metadata\nv = importlib.metadata.version('pydantic')\n",
    ],
)
def test_ordinary_code_is_not_reported(source: str) -> None:
    assert find_violations(source) == []
