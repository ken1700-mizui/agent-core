"""Files passed into and out of a sandbox (S-R04; D20).

Into the sandbox: an ``InputBundle`` built only from completed WP-08 reads; its
``bundle_ref()`` must equal the approved program's ``workspace_ref``. Out of the
sandbox: ``OutputFile`` records, treated as untrusted tool output (D4), never
written to the worktree directly. Both are size-, count- and path-limited, and
each file carries its sha256 digest, checked against its content.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import PurePosixPath
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.values import Digest

_DOMAIN = b"agent-core/sandbox-input-bundle/v1\x00"
Path_ = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]


def safe_relative(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        not p.is_absolute()
        and str(p) == path
        and all(part not in ("", ".", "..") for part in p.parts)
        and "\\" not in path
        and "\x00" not in path
    )


class IoLimits(FrozenModel):
    max_files: Annotated[int, Field(strict=True, ge=1, le=1_000)] = 32
    max_file_bytes: Annotated[int, Field(strict=True, ge=1, le=1 << 20)] = 16_384
    max_total_bytes: Annotated[int, Field(strict=True, ge=1, le=1 << 20)] = 32_768


DEFAULT_LIMITS = IoLimits()


class BundleFile(FrozenModel):
    path: Path_
    content_b64: Annotated[str, StringConstraints(strict=True, max_length=1_500_000)]
    digest: Digest

    @model_validator(mode="after")
    def _checked(self) -> Self:
        if not safe_relative(self.path):
            raise ValueError("a path is relative, normalized and inside the tree")
        if "sha256:" + hashlib.sha256(self.data).hexdigest() != self.digest:
            raise ValueError("the content does not match its digest")
        return self

    @property
    def data(self) -> bytes:
        try:
            return base64.b64decode(self.content_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError("the content is not base64") from exc

    @classmethod
    def of(cls, path: str, data: bytes) -> Self:
        return cls(
            path=path,
            content_b64=base64.b64encode(data).decode(),
            digest="sha256:" + hashlib.sha256(data).hexdigest(),
        )


class OutputFile(BundleFile):
    """A file the sandbox produced: untrusted data (D4)."""


def check_limits(files: tuple[BundleFile, ...], limits: IoLimits) -> None:
    paths = [f.path for f in files]
    if len(set(paths)) != len(paths):
        raise ValueError("DUPLICATE_PATH")
    if len(files) > limits.max_files:
        raise ValueError("TOO_MANY_FILES")
    sizes = [len(f.data) for f in files]
    if any(s > limits.max_file_bytes for s in sizes):
        raise ValueError("FILE_TOO_LARGE")
    if sum(sizes) > limits.max_total_bytes:
        raise ValueError("TOTAL_TOO_LARGE")


class InputBundle(FrozenModel):
    files: tuple[BundleFile, ...]

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        paths = [f.path for f in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("bundle files are sorted and unique by path")
        return self

    def bundle_ref(self) -> str:
        h = hashlib.sha256(_DOMAIN)
        for f in self.files:
            for part in (f.path, f.digest):
                data = part.encode()
                h.update(len(data).to_bytes(8, "big") + data)
        return "sha256:" + h.hexdigest()
