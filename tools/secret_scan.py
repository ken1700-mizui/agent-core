"""Repository secret scanner (stdlib only).

Checks content that would be published for secret-shaped values, forbidden file
names, and file types that Phase 1 never needs (AGENTS.md 3.6 and 4.9).

Modes (one is required):

- ``index``: the staged content in the git index, i.e. exactly what the next
  commit would contain. Use before every commit.
- ``commit``: the tree of one commit (``--rev``, default ``HEAD``). Use in CI.
- ``worktree``: tracked and untracked, non-ignored files in the working tree.
  Symbolic links are never followed.

Only regular, UTF-8 text files without NUL bytes are accepted. Symbolic links,
submodules (gitlinks), binary files, non-UTF-8 files and special files are
reported as findings. There is no allowlist in Phase 1; one would need an
explicit design and owner approval.

Exit codes: 0 = no findings, 1 = findings, 2 = the scan could not run (fail
closed). Output names the path, line and rule only, never the matched value.

Usage: python -I tools/secret_scan.py --mode {index,commit,worktree} [--rev REV] [--root PATH]
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-api-key", re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    (
        "generic-credential-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|"
            r"auth[_-]?token|password|passwd)\b\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{16,}['\"]"
        ),
    ),
)

FORBIDDEN_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".netrc",
    "credentials.json",
)

# git object modes (index and tree entries)
MODE_REGULAR = ("100644", "100755")
MODE_SYMLINK = "120000"
MODE_GITLINK = "160000"

GIT_TIMEOUT_SECONDS = 120
MAX_ERROR_DETAIL = 200


@dataclass(frozen=True)
class Finding:
    """A secret-shaped match inside a text."""

    line: int
    rule: str


@dataclass(frozen=True)
class FileFinding:
    """A finding located in a repository path. ``line`` is 0 for whole-file findings."""

    path: str
    line: int
    rule: str

    def render(self) -> str:
        if self.line == 0:
            return f"{self.path}: {self.rule}"
        return f"{self.path}:{self.line}: {self.rule}"


class ScanError(RuntimeError):
    """The scan could not run or could not read a target. Callers must fail closed."""


def scan_text(text: str) -> list[Finding]:
    """Return one finding per (line, rule) match in ``text``."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in RULES:
            if pattern.search(line):
                findings.append(Finding(line=number, rule=rule))
    return findings


def is_forbidden_file_name(name: str) -> bool:
    """Return True when a file with this base name must never be committed."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in FORBIDDEN_FILE_PATTERNS)


def scan_content(path: str, data: bytes) -> list[FileFinding]:
    """Check the bytes of one regular file. Only UTF-8 text without NUL is accepted."""
    if b"\0" in data:
        return [FileFinding(path=path, line=0, rule="binary-file-not-allowed")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [FileFinding(path=path, line=0, rule="non-utf8-file-not-allowed")]
    return [FileFinding(path=path, line=f.line, rule=f.rule) for f in scan_text(text)]


# ---------------------------------------------------------------- git access


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    command = ["git", "-C", str(root), *args]
    try:
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"cannot run git {args[0]}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:MAX_ERROR_DETAIL]
        raise ScanError(f"git {args[0]} failed ({result.returncode}): {detail}")
    return result.stdout


def _decode_path(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScanError("a repository path is not valid UTF-8") from exc


def _read_blobs(root: Path, object_ids: list[str]) -> dict[str, bytes]:
    """Read blobs with one ``git cat-file --batch`` call. Missing objects fail the scan."""
    unique = sorted(set(object_ids))
    if not unique:
        return {}
    output = _git(root, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in unique).encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for object_id in unique:
        end = output.find(b"\n", pos)
        if end < 0:
            raise ScanError("truncated output from git cat-file")
        header = output[pos:end].decode("ascii", errors="replace").split()
        if len(header) != 3 or header[0] != object_id or header[1] != "blob":
            raise ScanError(f"cannot read object {object_id}: {' '.join(header)}")
        try:
            size = int(header[2])
        except ValueError as exc:
            raise ScanError(f"unexpected size for object {object_id}") from exc
        start = end + 1
        data = output[start : start + size]
        if len(data) != size or output[start + size : start + size + 1] != b"\n":
            raise ScanError(f"truncated content for object {object_id}")
        blobs[object_id] = data
        pos = start + size + 1
    if pos != len(output):
        raise ScanError("unexpected trailing output from git cat-file")
    return blobs


def _scan_git_entries(root: Path, entries: list[tuple[str, str, str]]) -> list[FileFinding]:
    """Scan (mode, object_id, path) entries from the index or a tree."""
    findings: list[FileFinding] = []
    to_read: list[tuple[str, str]] = []
    for mode, object_id, path in entries:
        if is_forbidden_file_name(PurePosixPath(path).name):
            findings.append(FileFinding(path=path, line=0, rule="forbidden-file-name"))
        if mode in MODE_REGULAR:
            to_read.append((object_id, path))
        elif mode == MODE_SYMLINK:
            findings.append(FileFinding(path=path, line=0, rule="symlink-not-allowed"))
        elif mode == MODE_GITLINK:
            findings.append(FileFinding(path=path, line=0, rule="gitlink-not-allowed"))
        else:
            findings.append(FileFinding(path=path, line=0, rule="special-entry-not-allowed"))
    blobs = _read_blobs(root, [object_id for object_id, _ in to_read])
    for object_id, path in to_read:
        findings.extend(scan_content(path, blobs[object_id]))
    return findings


def scan_index(root: Path) -> list[FileFinding]:
    """Scan the staged content: exactly what the next commit would contain."""
    entries: list[tuple[str, str, str]] = []
    for record in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        fields = meta.decode("ascii", errors="replace").split()
        if len(fields) != 3 or not raw_path:
            raise ScanError("unexpected output from git ls-files --stage")
        mode, object_id, stage = fields
        path = _decode_path(raw_path)
        if stage != "0":
            raise ScanError(f"unmerged index entry: {path}")
        entries.append((mode, object_id, path))
    return _scan_git_entries(root, entries)


def scan_commit(root: Path, rev: str) -> list[FileFinding]:
    """Scan the full tree of one commit."""
    commit = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    commit_id = commit.decode("ascii").strip()
    entries: list[tuple[str, str, str]] = []
    for record in _git(root, "ls-tree", "-r", "-z", "--full-tree", commit_id).split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        fields = meta.decode("ascii", errors="replace").split()
        if len(fields) != 3 or not raw_path:
            raise ScanError("unexpected output from git ls-tree")
        mode, _kind, object_id = fields
        entries.append((mode, object_id, _decode_path(raw_path)))
    return _scan_git_entries(root, entries)


# ---------------------------------------------------------------- working tree


def _worktree_entry(root: Path, relative: str) -> list[FileFinding]:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
        raise ScanError(f"unexpected path from git: {relative}")
    if is_forbidden_file_name(parts[-1]):
        name_findings = [FileFinding(path=relative, line=0, rule="forbidden-file-name")]
    else:
        name_findings = []
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            # A tracked file deleted in the working tree: there is no content to publish.
            return name_findings
        except OSError as exc:
            raise ScanError(f"cannot stat {relative}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            # Never follow: a link may point outside the repository.
            return [*name_findings, FileFinding(path=relative, line=0, rule="symlink-not-allowed")]
        last = index == len(parts) - 1
        if not last and not stat.S_ISDIR(info.st_mode):
            raise ScanError(f"unexpected non-directory parent in {relative}")
    if stat.S_ISDIR(info.st_mode):
        return [*name_findings, FileFinding(path=relative, line=0, rule="gitlink-not-allowed")]
    if not stat.S_ISREG(info.st_mode):
        rule = "special-file-not-allowed"
        return [*name_findings, FileFinding(path=relative, line=0, rule=rule)]
    return [*name_findings, *scan_content(relative, _read_regular_no_follow(current, info))]


def _read_regular_no_follow(path: Path, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ScanError(f"cannot open {path.name}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_ino != expected.st_ino
            or info.st_dev != expected.st_dev
        ):
            raise ScanError(f"{path.name} changed while it was being scanned")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise ScanError(f"cannot read {path.name}: {exc}") from exc
    finally:
        os.close(fd)


def scan_worktree(root: Path) -> list[FileFinding]:
    """Scan tracked and untracked, non-ignored files in the working tree."""
    raw = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = sorted({_decode_path(p) for p in raw.split(b"\0") if p})
    findings: list[FileFinding] = []
    for relative in paths:
        findings.extend(_worktree_entry(root, relative))
    return findings


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan repository content for secrets.")
    parser.add_argument("--mode", required=True, choices=("index", "commit", "worktree"))
    parser.add_argument("--rev", default=None, help="commit to scan (commit mode, default HEAD)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    root: Path = args.root
    mode: str = args.mode
    rev: str | None = args.rev
    if rev is not None and mode != "commit":
        parser.error("--rev is only valid with --mode commit")
    try:
        if mode == "index":
            findings = scan_index(root)
        elif mode == "commit":
            findings = scan_commit(root, rev or "HEAD")
        else:
            findings = scan_worktree(root)
    except ScanError as exc:
        print(f"secret scan ({mode}) could not run: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding.render())
    if findings:
        print(f"secret scan ({mode}): {len(findings)} finding(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
