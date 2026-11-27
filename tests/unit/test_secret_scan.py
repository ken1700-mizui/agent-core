"""The repository secret scanner (tools/secret_scan.py).

Synthetic secret-shaped values are assembled at run time from fragments, so
this file itself never contains a secret-shaped literal. Every repository used
here is a temporary, synthetic git repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import secret_scan

REPO_ROOT = Path(__file__).resolve().parents[2]
MODES = ("index", "commit", "worktree")

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
SECRET = SYNTHETIC_SAMPLES["github-token"]


# ---------------------------------------------------------------- pure functions


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


@pytest.mark.parametrize(
    ("data", "rule"),
    [
        (b"\x00\x01" + SECRET.encode(), "binary-file-not-allowed"),
        (b"plain text\x00", "binary-file-not-allowed"),
        (b"caf\xe9\n", "non-utf8-file-not-allowed"),
    ],
)
def test_binary_and_non_utf8_content_is_rejected_not_skipped(data: bytes, rule: str) -> None:
    findings = secret_scan.scan_content("blob", data)
    assert [(f.line, f.rule) for f in findings] == [(0, rule)]


# ---------------------------------------------------------------- helpers


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "synthetic")


def _scan(repo: Path, mode: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = secret_scan.main(["--mode", mode, "--root", str(repo)])
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    return code, captured.out


# ---------------------------------------------------------------- WP01-R01: what is scanned


def test_index_mode_scans_staged_content_even_if_worktree_is_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f"ok\ntoken {SECRET}\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    (repo / "notes.txt").write_text("ok\nclean now\n", encoding="utf-8")

    code, out = _scan(repo, "index", capsys)
    assert code == 1
    assert "notes.txt:2: github-token" in out
    assert _scan(repo, "worktree", capsys)[0] == 0


def test_index_mode_ignores_unstaged_secret_but_worktree_mode_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    (repo / "notes.txt").write_text(f"ok\ntoken {SECRET}\n", encoding="utf-8")

    assert _scan(repo, "index", capsys)[0] == 0
    code, out = _scan(repo, "worktree", capsys)
    assert code == 1
    assert "notes.txt:2: github-token" in out


def test_commit_mode_scans_the_commit_not_the_worktree_or_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f"token {SECRET}\n", encoding="utf-8")
    _commit_all(repo)
    (repo / "notes.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")

    code, out = _scan(repo, "commit", capsys)
    assert code == 1
    assert "notes.txt:1: github-token" in out
    assert _scan(repo, "index", capsys)[0] == 0
    assert _scan(repo, "worktree", capsys)[0] == 0


def test_commit_mode_accepts_an_explicit_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f"token {SECRET}\n", encoding="utf-8")
    _commit_all(repo)
    (repo / "notes.txt").write_text("clean\n", encoding="utf-8")
    _commit_all(repo)

    assert secret_scan.main(["--mode", "commit", "--root", str(repo)]) == 0
    assert secret_scan.main(["--mode", "commit", "--rev", "HEAD~1", "--root", str(repo)]) == 1
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize("mode", MODES)
def test_clean_repository_passes_in_every_mode(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit_all(repo)
    assert _scan(repo, mode, capsys) == (0, "")


@pytest.mark.parametrize("mode", MODES)
def test_forbidden_file_name_is_reported_in_every_mode(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "config.pem").write_text("synthetic\n", encoding="utf-8")
    _commit_all(repo)
    code, out = _scan(repo, mode, capsys)
    assert code == 1
    assert "config.pem: forbidden-file-name" in out


# ---------------------------------------------------------------- WP01-R01: read failures


@pytest.mark.parametrize("mode", MODES)
def test_not_a_git_repository_fails_closed(tmp_path: Path, mode: str) -> None:
    assert secret_scan.main(["--mode", mode, "--root", str(tmp_path / "missing")]) == 2


def test_unknown_revision_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit_all(repo)
    assert secret_scan.main(["--mode", "commit", "--rev", "no-such-rev", "--root", str(repo)]) == 2


def test_missing_staged_object_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("synthetic content\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    object_id = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ":notes.txt"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    (repo / ".git" / "objects" / object_id[:2] / object_id[2:]).unlink()

    assert secret_scan.main(["--mode", "index", "--root", str(repo)]) == 2


def test_git_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    for mode in MODES:
        assert secret_scan.main(["--mode", mode, "--root", str(repo)]) == 2


def test_unreadable_worktree_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    (repo / "locked.txt").write_text("synthetic\n", encoding="utf-8")
    real_open = os.open

    def refuse_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path).name == "locked.txt":
            raise PermissionError("synthetic read failure")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", refuse_open)
    assert secret_scan.main(["--mode", "worktree", "--root", str(repo)]) == 2


def test_rev_is_rejected_outside_commit_mode(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        secret_scan.main(["--mode", "index", "--rev", "HEAD", "--root", str(tmp_path)])


# ---------------------------------------------------------------- WP01-R02: unsafe file types


@pytest.mark.parametrize("mode", MODES)
def test_symlink_is_rejected_and_its_target_is_never_read(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(f"token {SECRET}\n", encoding="utf-8")
    repo = _repo(tmp_path)
    (repo / "link.txt").symlink_to(outside)
    _commit_all(repo)

    code, out = _scan(repo, mode, capsys)
    assert code == 1
    assert out.splitlines() == ["link.txt: symlink-not-allowed"]


@pytest.mark.parametrize("mode", MODES)
def test_binary_file_is_rejected_in_every_mode(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01" + SECRET.encode())
    _commit_all(repo)
    code, out = _scan(repo, mode, capsys)
    assert code == 1
    assert out.splitlines() == ["blob.bin: binary-file-not-allowed"]


@pytest.mark.parametrize("mode", MODES)
def test_non_utf8_file_is_rejected_in_every_mode(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "latin1.txt").write_bytes(b"caf\xe9\n")
    _commit_all(repo)
    code, out = _scan(repo, mode, capsys)
    assert code == 1
    assert out.splitlines() == ["latin1.txt: non-utf8-file-not-allowed"]


@pytest.mark.parametrize("mode", ["index", "commit"])
def test_gitlink_entry_is_rejected(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit_all(repo)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vendored")
    _git(repo, "commit", "-q", "-m", "gitlink")

    code, out = _scan(repo, mode, capsys)
    assert code == 1
    assert out.splitlines() == ["vendored: gitlink-not-allowed"]


def test_special_file_is_never_read_in_worktree_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fifo = repo / "pipe"
    os.mkfifo(fifo)
    findings = secret_scan._worktree_entry(repo, "pipe")
    assert [f.rule for f in findings] == ["special-file-not-allowed"]


# ---------------------------------------------------------------- this repository


@pytest.mark.parametrize("mode", MODES)
def test_this_repository_has_no_findings(mode: str) -> None:
    if shutil.which("git") is None:
        pytest.fail("git is required to scan this repository")
    assert secret_scan.main(["--mode", mode, "--root", str(REPO_ROOT)]) == 0
