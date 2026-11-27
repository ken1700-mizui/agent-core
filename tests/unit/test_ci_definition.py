"""WP-01: the CI definition (written first; nothing here runs GitHub Actions).

- every action is pinned to a full 40-hex commit SHA (not a tag);
- the only trigger is ``workflow_dispatch`` (no automatic runs);
- uv is installed in pip's hash-checking mode, from a requirements file that
  pins the same version as ``UV_VERSION``;
- the ordinary tests run as the runner's user and the privileged ones as root.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
UV_REQUIREMENTS = ROOT / "tools/ci/uv-requirements.txt"


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", CI, flags=re.MULTILINE)
    assert uses, "the workflow uses actions"
    for ref in uses:
        _name, _, version = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", version), ref


def test_the_only_trigger_is_a_manual_dispatch() -> None:
    block = re.search(r"^on:\n((?:[ \t]+.*\n)+)", CI, flags=re.MULTILINE)
    assert block is not None
    triggers = re.findall(r"^  (\w+):", block.group(1), flags=re.MULTILINE)
    assert triggers == ["workflow_dispatch"]


def test_uv_is_installed_by_hash_at_the_pinned_version() -> None:
    version = re.search(r'UV_VERSION:\s*"([^"]+)"', CI)
    assert version is not None
    text = UV_REQUIREMENTS.read_text(encoding="utf-8")
    assert f"uv=={version.group(1)} " in text
    assert re.search(r"--hash=sha256:[0-9a-f]{64}", text)
    install = [line for line in CI.splitlines() if "pip install" in line]
    assert install, "uv is installed with pip"
    for line in install:
        assert "--require-hashes" in line
        assert "--only-binary :all:" in line
        assert "tools/ci/uv-requirements.txt" in line


def test_ordinary_and_privileged_tests_are_separate_steps() -> None:
    assert 'uv run --locked --offline pytest -m "not privileged"' in CI
    assert re.search(r"sudo .*pytest .*-m privileged", CI)
