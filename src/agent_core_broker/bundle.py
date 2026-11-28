"""P2's check of an input bundle before it reaches a sandbox (S-R04)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from pydantic import ValidationError

from agent_core.types.sandbox_io import DEFAULT_LIMITS, InputBundle, IoLimits, check_limits


class BundleRefusedError(ValueError):
    pass


def read_bundle(
    path: Path, workspace_ref: str, *, limits: IoLimits = DEFAULT_LIMITS
) -> InputBundle:
    """A regular file (no link), within the size limit, whose bundle reference is
    the approved ``workspace_ref`` and whose every file matches its digest."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise BundleRefusedError("UNREADABLE") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BundleRefusedError("NOT_A_REGULAR_FILE")
        if info.st_size > 2 * limits.max_total_bytes + 4_096 * limits.max_files:
            raise BundleRefusedError("TOO_LARGE")
        data = os.read(fd, info.st_size + 1)
    finally:
        os.close(fd)
    try:
        bundle = InputBundle.model_validate_json(data)
        check_limits(bundle.files, limits)
    except (ValidationError, ValueError) as exc:
        raise BundleRefusedError("INVALID") from exc
    if bundle.bundle_ref() != workspace_ref:
        raise BundleRefusedError("INPUT_MISMATCH")
    return bundle
