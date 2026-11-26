"""The repository secret scanner (tools/secret_scan.py).

Synthetic secret-shaped values are assembled at run time from fragments, so
this file itself never contains a secret-shaped literal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import secret_scan

REPO_ROOT = Path(__file__).resolve().parents[2]

SYNTHETIC_SAMPLES = {
    "private-key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
    "aws-access-key-id": "AK" + "IA" + "Z7Q3" * 4,
    "github-token": "gh" + "p_" + "aB3d" * 9,
    "anthropic-api-key": "sk-" + "ant-" + "api03-" + "Xy9_" * 10,
    "openai-api-key": "sk-" + "proj-" + "Qw7e" * 10,
    "slack-token": "xo" + "xb-" + "1234567890-abcdef",
    "google-api-key": "AI" + "za" + "Sy" + "B" * 33,
    "generic-credential-assignment": "api_" + 'key = "' + "Zz9" * 8 + '"',
}


@pytest.mark.parametrize(("rule", "sample"), sorted(SYNTHETIC_SAMPLES.items()))
def test_each_rule_detects_its_synthetic_sample(rule: str, sample: str) -> None:
    findings = secret_scan.scan_text(f"first line\nvalue: {sample}\n")
    assert [(f.line, f.rule) for f in findings] == [(2, rule)]


@pytest.mark.parametrize(
    "text",
    [
        "token_count = 12\n",
        'password_hint = "see the owner"\n',
        "sk-short\n",
        "Use `sk-` prefixed keys only through the Egress Gateway.\n",
        "AKIA is the prefix of an access key id.\n",
    ],
)
def test_ordinary_text_is_not_reported(text: str) -> None:
    assert secret_scan.scan_text(text) == []


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "deploy.pem", "server.key", "id_rsa", "id_ed25519"]
)
def test_forbidden_file_names_are_reported(name: str) -> None:
    assert secret_scan.is_forbidden_file_name(name)


@pytest.mark.parametrize("name", ["README.md", "env.py", "keys.py", "pyproject.toml"])
def test_ordinary_file_names_are_allowed(name: str) -> None:
    assert not secret_scan.is_forbidden_file_name(name)


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    return tmp_path


def test_main_reports_location_but_never_the_secret_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path)
    sample = SYNTHETIC_SAMPLES["github-token"]
    (repo / "notes.txt").write_text(f"ok\ntoken {sample}\n", encoding="utf-8")

    assert secret_scan.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "notes.txt:2: github-token" in out
    assert sample not in out


def test_main_reports_forbidden_file_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "config.pem").write_text("synthetic\n", encoding="utf-8")
    assert secret_scan.main(["--root", str(repo)]) == 1
    assert "config.pem: forbidden-file-name" in capsys.readouterr().out


def test_main_accepts_clean_repository_and_skips_binary_files(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(b"\x00\x01" + SYNTHETIC_SAMPLES["github-token"].encode())
    assert secret_scan.main(["--root", str(repo)]) == 0


def test_main_fails_closed_outside_a_git_repository(tmp_path: Path) -> None:
    assert secret_scan.main(["--root", str(tmp_path / "missing")]) == 2


def test_this_repository_has_no_findings() -> None:
    assert secret_scan.main(["--root", str(REPO_ROOT)]) == 0
