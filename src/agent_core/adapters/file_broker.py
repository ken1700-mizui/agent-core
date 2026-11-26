"""Synthetic work-tree file broker (WP-08; C1 8.3, C1.S16, C1.S19).

What it does now:

1. ``preflight`` (before the start, before any receipt is claimed):

   - the operation names a workspace, and the current policy (same version as
     the decision) trusts that digest;
   - the root is opened as a directory without following a link at its last
     component, and must belong to this user with mode 0700 (no group or other
     access);
   - the signed manifest directly under the root is read (not through a link,
     one hard link, bounded size), its HMAC verified with the derived manifest
     key, and it must name this root path, the opened root's device and inode,
     and have exactly the operation's ``workspace_ref`` as its digest. A valid
     manifest copied into another tree, or a copied tree moved into the original
     path, is refused (other path, or other inode).

2. C1.S19: safe path resolution is not available in this environment (Python
   has no ``openat2``; the component-wise ``dir_fd`` walk is proposed in
   docs/design/phase-1/WP-08_FILE_OPS.md and not adopted). Every read and write
   is refused before any target is opened, both in ``preflight`` and again in
   ``read`` and ``write`` inside the callback.

Only the root directory and the manifest file are ever opened here. Nothing is
created, changed or removed. This module is imported by no other agent_core
module (import-linter); the composition root builds the broker.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from agent_core.ports.files import Redeem
from agent_core.safety.workspace import manifest_digest, verify_manifest
from agent_core.types.errors import FileOperationRefusedError, WorkspaceRejectedError
from agent_core.types.files import (
    MANIFEST_FILE_NAME,
    MAX_MANIFEST_BYTES,
    ExecutionTicket,
    SignedWorkspaceManifest,
    WorkspaceManifest,
)
from agent_core.types.safety import (
    ActionDescriptor,
    ActionKind,
    DispatchAuthorization,
    Policy,
    SecretKey,
)
from agent_core.types.serialization import StrictJsonError, parse_strict_json

# C1 8.3 requires resolution beneath the root that refuses links (openat2 with
# RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS, or an equivalent
# the owner accepted). None is adopted, so S19 refuses every file operation.
SAFE_RESOLUTION_ADOPTED: Final = False

_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def safe_resolution_available() -> bool:
    return SAFE_RESOLUTION_ADOPTED


def _refuse_without_safe_resolution() -> None:
    if not safe_resolution_available():
        raise FileOperationRefusedError("SAFE_FILE_ACCESS_UNAVAILABLE")


class SyntheticFileBroker:
    def __init__(self, *, root: Path, key: SecretKey) -> None:
        if type(key) is not SecretKey:
            raise TypeError("the broker needs the manifest key")
        path = os.fspath(root)
        if not os.path.isabs(path) or os.path.normpath(path) != path:
            raise ValueError("the work tree root is a normalized absolute path")
        self._root = path
        self._key = key

    def __repr__(self) -> str:
        return "<SyntheticFileBroker>"

    # ------------------------------------------------------------ before the start

    def preflight(self, descriptor: ActionDescriptor, policy: Policy | None) -> None:
        self._check_workspace(descriptor, policy)
        _refuse_without_safe_resolution()

    def _check_workspace(self, descriptor: ActionDescriptor, policy: Policy | None) -> None:
        ref = descriptor.workspace_ref
        if descriptor.kind is not ActionKind.TOOL_CALL or not descriptor.target_resources:
            raise WorkspaceRejectedError("NOT_A_FILE_OPERATION")
        if ref is None:
            raise WorkspaceRejectedError("WORKSPACE_REF_MISSING")
        if (
            type(policy) is not Policy
            or policy.tenant_id != descriptor.tenant_id
            or policy.policy_version != descriptor.policy_version
            or ref not in policy.trusted_synthetic_workspaces
        ):
            raise WorkspaceRejectedError("WORKSPACE_NOT_TRUSTED")
        manifest, device, inode = self._load_manifest()
        if manifest.root_path != self._root:
            raise WorkspaceRejectedError("ROOT_MISMATCH")
        if (manifest.root_device, manifest.root_inode) != (device, inode):
            raise WorkspaceRejectedError("ROOT_IDENTITY_MISMATCH")
        if manifest_digest(manifest) != ref:
            raise WorkspaceRejectedError("WORKSPACE_MISMATCH")

    def _load_manifest(self) -> tuple[WorkspaceManifest, int, int]:
        try:
            fd = os.open(self._root, _DIR_FLAGS)
        except OSError:
            raise WorkspaceRejectedError("ROOT_UNUSABLE") from None
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise WorkspaceRejectedError("ROOT_PERMISSIONS")
            raw = _read_manifest(os.path.join(self._root, MANIFEST_FILE_NAME))
        finally:
            os.close(fd)
        try:
            parse_strict_json(raw)
            signed = SignedWorkspaceManifest.model_validate_json(raw)
        except (StrictJsonError, ValidationError):
            raise WorkspaceRejectedError("MANIFEST_INVALID") from None
        return verify_manifest(self._key, signed), info.st_dev, info.st_ino

    # ------------------------------------------------------------ inside the callback

    def read(
        self, authorization: DispatchAuthorization, ticket: ExecutionTicket, redeem: Redeem
    ) -> bytes:
        _refuse_without_safe_resolution()
        raise FileOperationRefusedError("SAFE_FILE_ACCESS_NOT_IMPLEMENTED")

    def write(
        self,
        authorization: DispatchAuthorization,
        ticket: ExecutionTicket,
        redeem: Redeem,
        content: bytes,
    ) -> None:
        _refuse_without_safe_resolution()
        raise FileOperationRefusedError("SAFE_FILE_ACCESS_NOT_IMPLEMENTED")


def _read_manifest(path: str) -> bytes:
    """The manifest's bytes: a regular file with one link, not reached through a
    link at its last component, at most MAX_MANIFEST_BYTES."""
    try:
        fd = os.open(path, _FILE_FLAGS)
    except OSError:
        raise WorkspaceRejectedError("MANIFEST_UNREADABLE") from None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_MANIFEST_BYTES
        ):
            raise WorkspaceRejectedError("MANIFEST_UNREADABLE")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MANIFEST_BYTES:
                raise WorkspaceRejectedError("MANIFEST_UNREADABLE")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)
