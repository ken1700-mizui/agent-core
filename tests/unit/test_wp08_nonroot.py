"""WP08-R09: the file broker under a non-root user.

This container runs as root. When it does, the child process runs as the
existing unprivileged user ``nobody`` (uid 65534) through ``subprocess``'s
``user``/``group`` arguments: no user is added, no OS setting or permission is
changed. Its work directory is a new private (0700) directory under the sticky
``/tmp``, owned by that user and removed afterwards. When the suite already runs
as a non-root user, the child simply runs as that user.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = REPO_ROOT / "tests" / "support" / "nonroot_child.py"
NOBODY = 65_534


@pytest.fixture
def work() -> Any:
    path = Path(tempfile.mkdtemp(prefix="agent-core-nonroot-", dir="/tmp"))
    try:
        if os.geteuid() == 0:
            os.chown(path, NOBODY, NOBODY)
        path.chmod(0o700)
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=False)


def _plant(work: Path) -> None:
    """Entries the child does not own (created by the parent)."""
    tree = work / "area" / "tree" / "workspace"
    root_owned = tree / "root.txt"
    root_owned.write_bytes(b"owned by another user\n")
    os.chown(root_owned, os.geteuid() if os.geteuid() != 0 else 0, -1)
    (tree / "link.txt").symlink_to("/etc/hostname")
    rootdir = tree / "rootdir"
    rootdir.mkdir(mode=0o755)
    (rootdir / "f.txt").write_bytes(b"inside another user's directory\n")


def test_reads_writes_and_refusals_as_a_non_root_user(work: Path) -> None:
    if os.geteuid() != 0:
        # Not root: the planted entries would be ours; this run checks only that the
        # broker works without privileges (recorded in WP-08_FILE_OPS.md 10.4).
        user: dict[str, Any] = {}
    else:
        user = {"user": NOBODY, "group": NOBODY, "extra_groups": []}
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(str(REPO_ROOT / p) for p in ("src", "tests")),
    }
    with subprocess.Popen(
        [sys.executable, str(CHILD), str(work)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=work,
        **user,
    ) as child:
        assert child.stdout is not None
        assert child.stdin is not None
        line = child.stdout.readline().strip()
        assert line == "ready", line + (child.stderr.read() if child.stderr else "")
        _plant(work)
        out, err = child.communicate("go\n", timeout=120)
    assert child.returncode == 0, err[-3000:]
    results = json.loads(out.strip().splitlines()[-1])

    if os.geteuid() == 0:
        assert results["uid"] == NOBODY
    else:
        assert results["uid"] == os.geteuid()
    # Normal read and CREATE without privileges.
    assert results["read"] == {"ok": True, "data": "synthetic a\n"}
    assert results["create"]["ok"] is True
    assert results["create"]["uid"] == results["uid"]
    assert results["create"]["data"] == "synthetic new\n"
    # A file the user cannot read: REPLACE cannot check the expected state.
    assert results["replace_unreadable"] == {"ok": False, "reasons": ["TARGET_UNSAFE"]}
    # A link is never followed.
    assert results["read_symlink"] == {"ok": False, "reasons": ["TARGET_UNSAFE"]}
    if os.geteuid() == 0:
        # Entries of another user (root): a file, and a directory on the path.
        assert results["read_root_owned"] == {"ok": False, "reasons": ["TARGET_UNSAFE"]}
        assert results["read_in_root_dir"] == {"ok": False, "reasons": ["PATH_UNSAFE"]}
        assert results["create_in_root_dir"] == {"ok": False, "reasons": ["PATH_UNSAFE"]}
