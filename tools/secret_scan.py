"""Repository secret scanner (stdlib only).

Scans every file git tracks, plus untracked files that are not ignored, for
secret-shaped values and forbidden file names (AGENTS.md 3.6 and 4.9).

Exit codes: 0 = no findings, 1 = findings, 2 = the scan could not run (fail
closed). Findings print the path, line and rule only, never the matched value.

Usage: python tools/secret_scan.py [--root PATH]
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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

BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class Finding:
    """A secret-shaped match inside a text."""

    line: int
    rule: str


@dataclass(frozen=True)
class FileFinding:
    """A finding located in a repository file. ``line`` is 0 for file-name findings."""

    path: str
    line: int
    rule: str

    def render(self) -> str:
        if self.line == 0:
            return f"{self.path}: {self.rule}"
        return f"{self.path}:{self.line}: {self.rule}"


class ScanError(RuntimeError):
    """The scan could not run. Callers must fail closed."""


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


def list_repository_files(root: Path) -> list[str]:
    """Tracked files plus untracked, non-ignored files, relative to ``root``."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ScanError(f"git ls-files failed: {stderr}")
    return sorted({p for p in result.stdout.decode("utf-8").split("\0") if p})


def scan_repository(root: Path) -> list[FileFinding]:
    """Scan every listed file under ``root``."""
    findings: list[FileFinding] = []
    for relative in list_repository_files(root):
        path = root / relative
        if is_forbidden_file_name(path.name):
            findings.append(FileFinding(path=relative, line=0, rule="forbidden-file-name"))
        if not path.is_file():
            # Listed by git but deleted in the working tree: nothing to read.
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ScanError(f"cannot read {relative}: {exc}") from exc
        if b"\0" in data[:BINARY_SNIFF_BYTES]:
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(
            FileFinding(path=relative, line=f.line, rule=f.rule) for f in scan_text(text)
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan the repository for secrets.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    root: Path = args.root
    try:
        findings = scan_repository(root)
    except ScanError as exc:
        print(f"secret scan could not run: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding.render())
    if findings:
        print(f"secret scan: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
