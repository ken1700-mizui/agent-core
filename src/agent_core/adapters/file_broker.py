"""Synthetic work-tree file broker (WP-08; ADR-0008; C1 8.3, C1.S16, C1.S19).

Path resolution (ADR-0008, accepted for Phase 1 managed synthetic trees only):

- The trusted managed area is an absolute, normalized path the composition root
  names. It is reached from ``/`` one name at a time with
  ``O_DIRECTORY | O_NOFOLLOW``. Ancestors must be directories owned by root or
  by this user; one writable by group or others needs the sticky bit. The area
  itself is ours and 0700.
- The tree's root is opened relative to the area fd: a directory, ours, 0700, on
  the area's device. Everything after that is relative to fds already opened;
  no absolute path string is used again.
- The signed manifest is opened relative to the root fd: first with ``O_PATH``
  (no open side effects; it must be a regular file with one link, ours, not
  writable by group or others, at most 1 MiB, on the same device), then for
  reading, and the two must be the same inode. Its HMAC (derived key) and the
  policy's trusted digest are checked, and it must name this root path and the
  root fd's device and inode.
- Inside the tree each directory is opened one name at a time
  (``O_DIRECTORY | O_NOFOLLOW``): ours, not writable by group or others, on the
  root's device. ``""``, ``.``, ``..``, NUL, backslash, absolute paths and the
  reserved names (the manifest, temporary files) are refused before any open.

Single writer: the broker holds ``flock(LOCK_EX | LOCK_NB)`` on the root fd from
``prepare`` to ``close``. That excludes cooperating brokers (in this or another
process). It does not stop a non-cooperating process of the same OS user, or
root (ADR-0008, limits).

C1.S19: when a condition of ADR-0008 cannot be met here (platform, flags,
``dir_fd`` support, ``flock``), every operation is refused before anything is
opened, and again inside the callback.

The manifest is the record of the tree at creation. Later managed changes are
the COMPLETED attempts in the Journal whose descriptor carries a
``WriteChange``; the manifest is not rewritten and does not prove the current
content. This module is imported by no other agent_core module (import-linter);
the composition root builds the broker.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import stat
import sys
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from importlib.abc import SourceLoader
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Final, NamedTuple, NoReturn

from pydantic import ValidationError

from agent_core.ports.files import Redeem
from agent_core.safety.workspace import file_digest, manifest_digest, sign_manifest, verify_manifest
from agent_core.types.errors import (
    FileEffect,
    FileOperationFailedError,
    FileOperationRefusedError,
    PermitRejectedError,
    WorkspaceRejectedError,
)
from agent_core.types.files import (
    MANIFEST_FILE_NAME,
    MAX_MANIFEST_BYTES,
    TEMP_FILE_PREFIX,
    EntryKind,
    ExecutionTicket,
    FileOperation,
    FileOutcome,
    SignedWorkspaceManifest,
    TicketBinding,
    WorkspaceEntry,
    WorkspaceManifest,
)
from agent_core.types.safety import (
    ActionDescriptor,
    ActionKind,
    DispatchAuthorization,
    Policy,
    SecretKey,
    WriteChange,
    WriteOp,
)
from agent_core.types.serialization import StrictJsonError, dump_json, parse_strict_json
from agent_core.types.tools import ResourceAccess

# ADR-0008: adopted for Phase 1 managed synthetic trees, where its conditions hold.
SAFE_RESOLUTION_ADOPTED: Final = True
MAX_FILE_BYTES: Final = 1_048_576
# The read_file output holds at most this many bytes of content (its schema).
MAX_READ_BYTES: Final = 65_536
# The write_file content argument holds at most this many characters (its schema).
MAX_WRITE_BYTES: Final = 65_536

# WP08-R04: the builtin file tools this broker runs, with the access each one has.
# Their trusted registry entries must pin ``builtin_artifact_hash()``.
#
# WP08-R08, what this does NOT guarantee: the hash covers this module's source as
# loaded and as it is on disk now (they must agree with the trusted catalog). It
# does not cover the running Python objects, the bytecode actually executed, the
# modules this one imports, or the interpreter. It is no defence against code
# that runs inside this process (it can replace any function). Isolating and
# pinning executables properly is left to a later phase.
BUILTIN_FILE_TOOLS: dict[tuple[str, str], ResourceAccess] = {
    ("read_file", "1.0.0"): ResourceAccess.READ,
    ("write_file", "1.0.0"): ResourceAccess.WRITE,
}
_ARTIFACT_DOMAIN = b"agent-core/builtin-file-broker/v1\x00"


def artifact_hash_of(source: bytes) -> str:
    """The artifact hash of a broker build: domain-tagged SHA-256 of its source."""
    return "sha256:" + hashlib.sha256(_ARTIFACT_DOMAIN + source).hexdigest()


def current_source() -> bytes:
    """This module's source as the import system would load it now."""
    spec: ModuleSpec | None = globals().get("__spec__")
    if spec is None or spec.origin is None or not isinstance(spec.loader, SourceLoader):
        raise FileOperationRefusedError("ARTIFACT_MISMATCH")
    return spec.loader.get_data(spec.origin)


# The source of the code that is running (read once, when the module was loaded).
_LOADED_ARTIFACT: Final = artifact_hash_of(current_source())


def builtin_artifact_hash() -> str:
    """The artifact hash of the broker code running in this process."""
    return _LOADED_ARTIFACT


_MAX_PATH = 4_096
_MAX_NAME = 255
_WORKSPACE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_FLAGS: Final = ("O_PATH", "O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK", "O_NOCTTY")
_DIR_FD_CALLS: Final = ("open", "stat", "mkdir", "link", "rename", "unlink")


def missing_capabilities() -> tuple[str, ...]:
    """The ADR-0008 conditions this process cannot meet (empty when all hold)."""
    missing: list[str] = []
    if sys.platform != "linux":
        missing.append("platform")
    missing += [flag for flag in _FLAGS if not hasattr(os, flag)]
    with_dir_fd = {getattr(f, "__name__", "") for f in os.supports_dir_fd}
    missing += [f"{name}-dir-fd" for name in _DIR_FD_CALLS if name not in with_dir_fd]
    if "stat" not in {getattr(f, "__name__", "") for f in os.supports_follow_symlinks}:
        missing.append("stat-nofollow")
    if not hasattr(fcntl, "flock") or not hasattr(os, "fsync"):
        missing.append("flock" if not hasattr(fcntl, "flock") else "fsync")
    return tuple(missing)


def safe_resolution_available() -> bool:
    return SAFE_RESOLUTION_ADOPTED and not missing_capabilities()


def _require_safe_resolution() -> None:
    if not safe_resolution_available():
        raise FileOperationRefusedError("SAFE_FILE_ACCESS_UNAVAILABLE")


def _dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _none(reason: str) -> NoReturn:
    raise FileOperationFailedError(reason, effect=FileEffect.NONE)


def split_target(path: str) -> tuple[str, ...]:
    """The names of a relative path inside the tree; refused (no effect) when it is
    empty, absolute, has an empty, ``.`` or ``..`` part, NUL or backslash, is too
    long, or names a reserved file."""
    if (
        type(path) is not str
        or not path
        or len(path) > _MAX_PATH
        or path.startswith("/")
        or "\x00" in path
        or "\\" in path
    ):
        _none("PATH_INVALID")
    parts = tuple(path.split("/"))
    for part in parts:
        if part in ("", ".", "..") or len(part) > _MAX_NAME:
            _none("PATH_INVALID")
        if part == MANIFEST_FILE_NAME or part.startswith(TEMP_FILE_PREFIX):
            _none("PATH_INVALID")
    return parts


def _normal_area(area: Path | str) -> str:
    path = os.fspath(area)
    if type(path) is not str or not path.startswith("/") or path == "/" or "\x00" in path:
        raise ValueError("the managed area is an absolute path below /")
    if any(part in ("", ".", "..") for part in path.split("/")[1:]):
        raise ValueError("the managed area is a normalized absolute path")
    return path


def _private_dir(info: os.stat_result, device: int | None = None) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) & 0o077 == 0
        and (device is None or info.st_dev == device)
    )


def _safe_ancestor(info: os.stat_result) -> bool:
    mode = info.st_mode
    if not stat.S_ISDIR(mode) or info.st_uid not in (0, os.geteuid()):
        return False
    return not (stat.S_IMODE(mode) & 0o022) or bool(mode & stat.S_ISVTX)


def _open_area(area: str) -> int:
    """The managed area's fd, reached from ``/`` one name at a time."""
    names = area.split("/")[1:]
    try:
        fd = os.open("/", _dir_flags())
    except OSError:
        raise WorkspaceRejectedError("AREA_UNSAFE") from None
    try:
        if not _safe_ancestor(os.fstat(fd)):
            raise WorkspaceRejectedError("AREA_UNSAFE")
        for index, name in enumerate(names):
            try:
                child = os.open(name, _dir_flags(), dir_fd=fd)
            except OSError:
                raise WorkspaceRejectedError("AREA_UNSAFE") from None
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            last = index == len(names) - 1
            if not (_private_dir(info) if last else _safe_ancestor(info)):
                raise WorkspaceRejectedError("AREA_UNSAFE")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_regular(dir_fd: int, name: str, *, device: int, limit: int) -> tuple[int, os.stat_result]:
    """A regular file directly in ``dir_fd``, opened for reading only after an
    ``O_PATH`` look (no open side effects for FIFOs or devices): one link, ours,
    not writable by group or others, on ``device``, at most ``limit`` bytes; the
    read fd must be the inode that was looked at. Raises ``LookupError`` with the
    reason otherwise."""
    try:
        probe = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        raise LookupError("MISSING") from None
    except OSError:
        raise LookupError("UNSAFE") from None
    try:
        seen = os.fstat(probe)
    finally:
        os.close(probe)
    if (
        not stat.S_ISREG(seen.st_mode)
        or seen.st_nlink != 1
        or seen.st_uid != os.geteuid()
        or stat.S_IMODE(seen.st_mode) & 0o022
        or seen.st_dev != device
    ):
        raise LookupError("UNSAFE")
    if seen.st_size > limit:
        raise LookupError("TOO_LARGE")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        raise LookupError("UNSAFE") from None
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (seen.st_dev, seen.st_ino) or not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise LookupError("CHANGED")
    return fd, info


def _read_bounded(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(fd, 65_536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise LookupError("TOO_LARGE")
        chunks.append(chunk)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


_LOOKUP_REASONS: Final = {
    "MISSING": "TARGET_MISSING",
    "UNSAFE": "TARGET_UNSAFE",
    "TOO_LARGE": "READ_TOO_LARGE",
    "CHANGED": "TARGET_CHANGED",
}


def _state(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PreparedWorkspace:
    """A verified tree, held through fds and the writer lock until ``close``."""

    __slots__ = ("_area_fd", "_closed", "_descriptor", "_device", "_digest", "_root_fd")

    def __init__(
        self, *, area_fd: int, root_fd: int, device: int, digest: str, descriptor: ActionDescriptor
    ) -> None:
        self._area_fd = area_fd
        self._root_fd = root_fd
        self._device = device
        self._digest = digest
        self._descriptor = descriptor
        self._closed = False

    def __repr__(self) -> str:
        return "<PreparedWorkspace>"

    def __enter__(self) -> PreparedWorkspace:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._root_fd)  # releases the writer lock
            os.close(self._area_fd)

    @property
    def workspace_ref(self) -> str:
        return self._digest

    @property
    def root_inode(self) -> int:
        return os.fstat(self._root_fd).st_ino

    @contextmanager
    def resolve_parent(self, path: str) -> Iterator[tuple[int, str]]:
        """The parent directory fd of ``path`` and its last name, walked from the
        root fd one name at a time. Refused with no effect when unsafe."""
        if self._closed:
            _none("PREPARED_CLOSED")
        parts = split_target(path)
        current = self._root_fd
        try:
            for name in parts[:-1]:
                try:
                    child = os.open(name, _dir_flags(), dir_fd=current)
                except OSError:
                    _none("PATH_UNSAFE")
                if current != self._root_fd:
                    os.close(current)
                current = child
                info = os.fstat(current)
                if not (
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) & 0o022 == 0
                    and info.st_dev == self._device
                ):
                    _none("PATH_UNSAFE")
            yield current, parts[-1]
        finally:
            if current != self._root_fd:
                os.close(current)

    def _check_still_safe(self, authorization: DispatchAuthorization) -> None:
        if not safe_resolution_available():
            _none("SAFE_FILE_ACCESS_UNAVAILABLE")
        if self._closed:
            _none("PREPARED_CLOSED")
        if authorization.descriptor != self._descriptor:
            _none("PREPARED_FOR_ANOTHER_OPERATION")

    def _redeem(
        self, ticket: ExecutionTicket, redeem: Redeem, operation: FileOperation
    ) -> TicketBinding:
        """Use the ticket before any part of the target is opened."""
        try:
            return redeem(ticket, operation)
        except PermitRejectedError as exc:
            raise FileOperationFailedError(*exc.reasons, effect=FileEffect.NONE) from None

    def read(
        self, authorization: DispatchAuthorization, ticket: ExecutionTicket, redeem: Redeem
    ) -> tuple[FileOutcome, bytes]:
        """The target's bytes, read only from a verified fd of a regular file with one
        link (WP08-R03). Reading changes nothing, so every failure is definite."""
        self._check_still_safe(authorization)
        targets = self._descriptor.target_resources
        if len(targets) != 1 or targets[0].access is not ResourceAccess.READ:
            _none("NOT_A_READ")
        path = targets[0].path
        binding = self._redeem(
            ticket,
            redeem,
            FileOperation(
                access=ResourceAccess.READ, path=path, change=None, workspace_ref=self._digest
            ),
        )
        with self.resolve_parent(path) as (parent, name):
            try:
                fd, before = _open_regular(parent, name, device=self._device, limit=MAX_READ_BYTES)
            except LookupError as exc:
                _none(_LOOKUP_REASONS.get(str(exc.args[0]), "TARGET_UNSAFE"))
            except OSError:
                _none("READ_FAILED")
            try:
                data = _read_bounded(fd, MAX_READ_BYTES)
                after = os.fstat(fd)
            except LookupError:
                _none("READ_TOO_LARGE")
            except OSError:
                _none("READ_FAILED")
            finally:
                os.close(fd)
        if _state(before) != _state(after) or len(data) != after.st_size:
            _none("TARGET_CHANGED_DURING_READ")
        outcome = FileOutcome(
            binding=binding, result_digest=file_digest(data), result_size=len(data)
        )
        return outcome, data

    def write(
        self,
        authorization: DispatchAuthorization,
        ticket: ExecutionTicket,
        redeem: Redeem,
        content: bytes,
    ) -> FileOutcome:
        """One CREATE or REPLACE (WP08-R05). The ticket is used first, for the change
        computed from the actual bytes. Then: the expected state is checked, the
        bytes go to a new temporary file in the same directory (unpredictable name,
        O_CREAT | O_EXCL), are written in full and fsynced; the commit is ``link``
        (CREATE, refuses an existing target) or ``rename`` (REPLACE, atomic within
        the directory); the directory is fsynced and the result verified.

        Failures before the commit leave the target unchanged (effect NONE; the
        temporary file is removed). A failing commit, or any failure after it, is
        UNKNOWN: nothing is restored and nothing is retried."""
        self._check_still_safe(authorization)
        change = self._descriptor.apply_change
        targets = self._descriptor.target_resources
        if change is None or [t.access for t in targets] != [ResourceAccess.WRITE]:
            _none("NOT_A_WRITE")
        if type(content) is not bytes or len(content) > MAX_WRITE_BYTES:
            _none("WRITE_TOO_LARGE")
        actual = WriteChange(
            op=change.op,
            path=change.path,
            expected_pre=change.expected_pre,
            content_digest=file_digest(content),
            content_size=len(content),
        )
        binding = self._redeem(
            ticket,
            redeem,
            FileOperation(
                access=ResourceAccess.WRITE,
                path=change.path,
                change=actual,
                workspace_ref=self._digest,
            ),
        )
        with self.resolve_parent(change.path) as (parent, name):
            self._check_expected(parent, name, change)
            temp = _write_temporary(parent, content, authorization.receipt.receipt_id)
            self._commit(parent, name, temp, change, content)
        return FileOutcome(
            binding=binding, result_digest=actual.content_digest, result_size=len(content)
        )

    def _check_expected(self, parent: int, name: str, change: WriteChange) -> None:
        if change.op is WriteOp.CREATE:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError:
                _none("TARGET_UNSAFE")
            _none("TARGET_EXISTS")
        try:
            fd, _info = _open_regular(parent, name, device=self._device, limit=MAX_FILE_BYTES)
        except LookupError as exc:
            _none(_LOOKUP_REASONS.get(str(exc.args[0]), "TARGET_UNSAFE"))
        except OSError:
            _none("TARGET_UNSAFE")
        try:
            current = _read_bounded(fd, MAX_FILE_BYTES)
        except (LookupError, OSError):
            _none("TARGET_UNSAFE")
        finally:
            os.close(fd)
        if file_digest(current) != change.expected_pre:
            _none("EXPECTED_STATE_MISMATCH")

    def _commit(
        self, parent: int, name: str, temp: str, change: WriteChange, content: bytes
    ) -> None:
        try:
            if change.op is WriteOp.CREATE:
                os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            else:
                os.rename(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        except FileExistsError:
            _discard(parent, temp)
            _none("TARGET_EXISTS")  # another writer created it; nothing of ours landed
        except OSError:
            raise FileOperationFailedError("COMMIT_FAILED", effect=FileEffect.UNKNOWN) from None
        try:
            if change.op is WriteOp.CREATE:
                os.unlink(temp, dir_fd=parent)
            os.fsync(parent)
            fd, info = _open_regular(parent, name, device=self._device, limit=MAX_FILE_BYTES)
            try:
                written = _read_bounded(fd, MAX_FILE_BYTES)
            finally:
                os.close(fd)
        except (OSError, LookupError):
            raise FileOperationFailedError(
                "AFTER_COMMIT_FAILED", effect=FileEffect.UNKNOWN
            ) from None
        if written != content or info.st_size != change.content_size:
            raise FileOperationFailedError("VERIFY_FAILED", effect=FileEffect.UNKNOWN)


def _check_artifact(descriptor: ActionDescriptor) -> None:
    """WP08-R04: the operation's tool is one of this broker's builtin file tools,
    with the access it declares, and its trusted entry pins exactly the code that
    runs here, which is also the code the import system would load now."""
    tool = descriptor.tool
    if tool is None or len(descriptor.target_resources) != 1:
        raise FileOperationRefusedError("ARTIFACT_MISMATCH")
    access = BUILTIN_FILE_TOOLS.get((tool.tool_id, tool.version))
    if access is not descriptor.target_resources[0].access:
        raise FileOperationRefusedError("ARTIFACT_MISMATCH")
    if tool.artifact_hash != builtin_artifact_hash():
        raise FileOperationRefusedError("ARTIFACT_MISMATCH")
    if artifact_hash_of(current_source()) != builtin_artifact_hash():
        raise FileOperationRefusedError("ARTIFACT_MISMATCH")


def temp_tag(receipt_id: str) -> str:
    """The 16 hex digits that tie a temporary file to the receipt of its write."""
    return hashlib.sha256(_TEMP_TAG_DOMAIN + str(receipt_id).encode()).hexdigest()[:16]


def _write_temporary(parent: int, content: bytes, receipt_id: str) -> str:
    """A new temporary file in ``parent`` holding ``content``, written in full and
    fsynced. Its name is the prefix, the receipt's tag and 16 random hex digits.
    On failure it is removed again (the target was never touched)."""
    temp = TEMP_FILE_PREFIX + temp_tag(receipt_id) + secrets.token_hex(8)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(temp, flags, 0o600, dir_fd=parent)
    except OSError:
        _none("TEMP_CREATE_FAILED")
    try:
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, content)
            os.fsync(fd)
            if os.fstat(fd).st_size != len(content):
                raise OSError("temporary file size differs")
        finally:
            os.close(fd)
    except OSError:
        _discard(parent, temp)
        _none("TEMP_WRITE_FAILED")
    return temp


def _discard(parent: int, temp: str) -> None:
    """Remove our temporary file; if that fails, recovery removes it later."""
    try:
        os.unlink(temp, dir_fd=parent)
    except OSError:
        return


_TEMP_NAME: Final = re.compile(re.escape(TEMP_FILE_PREFIX) + r"([0-9a-f]{16})[0-9a-f]{16}")
_TEMP_TAG_DOMAIN: Final = b"agent-core/broker-temp-tag/v1\x00"


class Recovery(NamedTuple):
    """What ``recover_temporaries`` removed, and the broker-form temporary files it
    left because no listed receipt accounts for them."""

    removed: tuple[str, ...]
    kept: tuple[str, ...]


class SyntheticFileBroker:
    def __init__(self, *, area: Path | str, workspace_id: str, key: SecretKey) -> None:
        if type(key) is not SecretKey:
            raise TypeError("the broker needs the manifest key")
        if type(workspace_id) is not str or _WORKSPACE_ID.fullmatch(workspace_id) is None:
            raise ValueError("a workspace id is a lowercase identifier")
        self._area = _normal_area(area)
        self._workspace_id = workspace_id
        self._key = key

    def __repr__(self) -> str:
        return "<SyntheticFileBroker>"

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("the file broker is not extended (its code is the pinned artifact)")

    def recover_temporaries(
        self, *, policy: Policy | None, tenant_id: str, receipts: Collection[str]
    ) -> Recovery:
        """An explicit maintenance operation (WP08-R07; only the composition root can
        reach the broker): remove the temporary files left by stopped writes whose
        receipts the caller lists from the ledger (``recovery_receipts``).

        - The tree must be the current policy's trusted tree for the tenant, after
          the same root and manifest checks as ``prepare``, under the writer lock.
        - Removed: regular files of ours whose name is the broker form and whose tag
          names a listed receipt. Kept (reported): broker-form files of no listed
          receipt, or that are not regular files of ours. Anything else is not
          looked at. Targets are never removed; nothing is written or retried.
        Detailed recovery management is WP-12."""
        _require_safe_resolution()
        tags = {temp_tag(r) for r in receipts}
        area_fd = _open_area(self._area)
        try:
            root_fd = self._open_root(area_fd)
            try:
                device = os.fstat(root_fd).st_dev
                manifest = self._manifest(root_fd, device)
                root = os.fstat(root_fd)
                if manifest.root_path != f"{self._area}/{self._workspace_id}" or (
                    manifest.root_device,
                    manifest.root_inode,
                ) != (root.st_dev, root.st_ino):
                    raise WorkspaceRejectedError("ROOT_IDENTITY_MISMATCH")
                if (
                    type(policy) is not Policy
                    or policy.tenant_id != tenant_id
                    or manifest_digest(manifest) not in policy.trusted_synthetic_workspaces
                ):
                    raise WorkspaceRejectedError("WORKSPACE_NOT_TRUSTED")
                removed: list[str] = []
                kept: list[str] = []
                _sweep(root_fd, "", device, tags, removed, kept)
                return Recovery(tuple(sorted(removed)), tuple(sorted(kept)))
            finally:
                os.close(root_fd)
        finally:
            os.close(area_fd)

    def _open_root(self, area_fd: int) -> int:
        """The root fd (ours, 0700, the area's device), locked for this writer."""
        try:
            root_fd = os.open(self._workspace_id, _dir_flags(), dir_fd=area_fd)
        except OSError:
            raise WorkspaceRejectedError("ROOT_UNUSABLE") from None
        try:
            root = os.fstat(root_fd)
            if not _private_dir(root):
                raise WorkspaceRejectedError("ROOT_PERMISSIONS")
            if root.st_dev != os.fstat(area_fd).st_dev:
                raise WorkspaceRejectedError("MOUNT_BOUNDARY")
            try:
                fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FileOperationRefusedError("WORKSPACE_BUSY") from None
        except BaseException:
            os.close(root_fd)
            raise
        return root_fd

    def prepare(self, descriptor: ActionDescriptor, policy: Policy | None) -> PreparedWorkspace:
        """Before the start: S19, the policy's trust, the area, the root, the lock and
        the manifest. Nothing but the area's ancestors, the area, the root and the
        manifest is opened; nothing is changed."""
        _require_safe_resolution()
        ref = descriptor.workspace_ref
        if descriptor.kind is not ActionKind.TOOL_CALL or not descriptor.target_resources:
            raise WorkspaceRejectedError("NOT_A_FILE_OPERATION")
        _check_artifact(descriptor)
        if ref is None:
            raise WorkspaceRejectedError("WORKSPACE_REF_MISSING")
        if (
            type(policy) is not Policy
            or policy.tenant_id != descriptor.tenant_id
            or policy.policy_version != descriptor.policy_version
            or ref not in policy.trusted_synthetic_workspaces
        ):
            raise WorkspaceRejectedError("WORKSPACE_NOT_TRUSTED")
        area_fd = _open_area(self._area)
        root_fd = -1
        try:
            root_fd = self._open_root(area_fd)
            root = os.fstat(root_fd)
            manifest = self._manifest(root_fd, root.st_dev)
            if manifest.root_path != f"{self._area}/{self._workspace_id}":
                raise WorkspaceRejectedError("ROOT_MISMATCH")
            if (manifest.root_device, manifest.root_inode) != (root.st_dev, root.st_ino):
                raise WorkspaceRejectedError("ROOT_IDENTITY_MISMATCH")
            if manifest_digest(manifest) != ref:
                raise WorkspaceRejectedError("WORKSPACE_MISMATCH")
        except BaseException:
            if root_fd >= 0:
                os.close(root_fd)
            os.close(area_fd)
            raise
        return PreparedWorkspace(
            area_fd=area_fd, root_fd=root_fd, device=root.st_dev, digest=ref, descriptor=descriptor
        )

    def _manifest(self, root_fd: int, device: int) -> WorkspaceManifest:
        try:
            fd, _info = _open_regular(
                root_fd, MANIFEST_FILE_NAME, device=device, limit=MAX_MANIFEST_BYTES
            )
        except (LookupError, OSError):
            raise WorkspaceRejectedError("MANIFEST_UNREADABLE") from None
        try:
            raw = _read_bounded(fd, MAX_MANIFEST_BYTES)
        except (LookupError, OSError):
            raise WorkspaceRejectedError("MANIFEST_UNREADABLE") from None
        finally:
            os.close(fd)
        try:
            parse_strict_json(raw)
            signed = SignedWorkspaceManifest.model_validate_json(raw)
        except (StrictJsonError, ValidationError):
            raise WorkspaceRejectedError("MANIFEST_INVALID") from None
        return verify_manifest(self._key, signed)


def _sweep(
    dir_fd: int,
    prefix: str,
    device: int,
    tags: set[str],
    removed: list[str],
    kept: list[str],
) -> None:
    for entry in sorted(os.scandir(dir_fd), key=lambda e: e.name):
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if info.st_uid != os.geteuid() or info.st_dev != device:
                continue  # not ours or another file system: never descended into
            child = os.open(entry.name, _dir_flags(), dir_fd=dir_fd)
            try:
                _sweep(child, f"{prefix}{entry.name}/", device, tags, removed, kept)
            finally:
                os.close(child)
            continue
        match = _TEMP_NAME.fullmatch(entry.name)
        if match is None:
            continue
        ours = stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
        if ours and match.group(1) in tags:
            os.unlink(entry.name, dir_fd=dir_fd)
            removed.append(prefix + entry.name)
        else:
            kept.append(prefix + entry.name)
    os.fsync(dir_fd)


# ---------------------------------------------------------------- creating a managed tree


def _creation_plan(files: Mapping[str, bytes]) -> tuple[list[str], list[str]]:
    """Directories and files to create, checked before anything is created."""
    dirs: set[str] = set()
    for path, data in files.items():
        try:
            parts = split_target(path)
        except FileOperationFailedError:
            raise WorkspaceRejectedError("PATH_INVALID") from None
        if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
            raise WorkspaceRejectedError("CONTENT_INVALID")
        dirs.update("/".join(parts[: i + 1]) for i in range(len(parts) - 1))
    if dirs & set(files):
        raise WorkspaceRejectedError("PATH_INVALID")  # a file is also a directory
    return sorted(dirs, key=lambda d: (d.count("/"), d)), sorted(files)


def _open_dir_beneath(root_fd: int, path: str) -> int:
    current = os.dup(root_fd)
    try:
        for name in path.split("/"):
            child = os.open(name, _dir_flags(), dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _parent_and_name(root_fd: int, path: str) -> tuple[int, str]:
    head, _, name = path.rpartition("/")
    return (_open_dir_beneath(root_fd, head) if head else os.dup(root_fd)), name


def _create_file(dir_fd: int, name: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def create_synthetic_workspace(
    *,
    area: Path | str,
    workspace_id: str,
    files: Mapping[str, bytes],
    key: SecretKey,
    now_ms: int,
) -> SignedWorkspaceManifest:
    """Create a managed synthetic tree ``<area>/<workspace_id>`` (0700) with the given
    synthetic files (0600) and its signed manifest (written last, 0600). Every
    path is checked before anything is created. A failure part way leaves a tree
    without a manifest, which ``prepare`` refuses; it is not removed by guessing."""
    _require_safe_resolution()
    if type(key) is not SecretKey:
        raise TypeError("the manifest is signed with the manifest key")
    if type(workspace_id) is not str or _WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise WorkspaceRejectedError("WORKSPACE_ID_INVALID")
    area_path = _normal_area(area)
    dirs, names = _creation_plan(files)
    area_fd = _open_area(area_path)
    try:
        try:
            os.mkdir(workspace_id, 0o700, dir_fd=area_fd)
        except FileExistsError:
            raise WorkspaceRejectedError("WORKSPACE_EXISTS") from None
        root_fd = os.open(workspace_id, _dir_flags(), dir_fd=area_fd)
        try:
            os.fchmod(root_fd, 0o700)
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for path in dirs:
                parent, name = _parent_and_name(root_fd, path)
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                    child = os.open(name, _dir_flags(), dir_fd=parent)
                    os.fchmod(child, 0o700)
                    os.close(child)
                finally:
                    os.close(parent)
            for path in names:
                parent, name = _parent_and_name(root_fd, path)
                try:
                    _create_file(parent, name, files[path])
                    os.fsync(parent)
                finally:
                    os.close(parent)
            root = os.fstat(root_fd)
            entries = [
                WorkspaceEntry(path=d, kind=EntryKind.DIRECTORY, size=None, content_digest=None)
                for d in dirs
            ] + [
                WorkspaceEntry(
                    path=p,
                    kind=EntryKind.FILE,
                    size=len(files[p]),
                    content_digest=file_digest(files[p]),
                )
                for p in names
            ]
            manifest = WorkspaceManifest(
                manifest_version=1,
                workspace_id=workspace_id,
                synthetic=True,
                root_path=f"{area_path}/{workspace_id}",
                root_device=root.st_dev,
                root_inode=root.st_ino,
                entries=tuple(sorted(entries, key=lambda e: e.path)),
                created_at_ms=now_ms,
            )
            signed = sign_manifest(key, manifest)
            _create_file(root_fd, MANIFEST_FILE_NAME, dump_json(signed))
            os.fsync(root_fd)
            os.fsync(area_fd)
            return signed
        finally:
            os.close(root_fd)
    finally:
        os.close(area_fd)


# ================================================================ composition-root private files

# WP10-R01: the composition root's own private files (its keys and settings) are
# read with the same checks as the managed area: the directory is reached from
# "/" one name at a time (trusted ancestors, a private last directory), and a file
# is looked at with O_PATH before it is opened (no FIFO or device side effect),
# must be one link, ours, on the same device, and is read with a bound enforced
# while reading.


def open_private_dir(path: Path | str) -> int:
    """The fd of a private directory (0700-like, ours) below trusted ancestors.
    Raises ``WorkspaceRejectedError("AREA_UNSAFE")`` otherwise."""
    _require_safe_resolution()
    try:
        normal = _normal_area(path)
    except ValueError:
        raise WorkspaceRejectedError("AREA_UNSAFE") from None
    return _open_area(normal)


def read_private_file(dir_fd: int, name: str, *, limit: int, mode: int = 0o600) -> bytes:
    """The bytes of the regular file ``name`` directly in ``dir_fd``: one link,
    ours, exactly ``mode``, on the directory's device, at most ``limit`` bytes
    (also while reading). Raises ``LookupError`` with a reason otherwise."""
    if "/" in name or name in ("", ".", ".."):
        raise LookupError("UNSAFE")
    device = os.fstat(dir_fd).st_dev
    fd, info = _open_regular(dir_fd, name, device=device, limit=limit)
    try:
        if stat.S_IMODE(info.st_mode) != mode:
            raise LookupError("UNSAFE")
        return _read_bounded(fd, limit)
    finally:
        os.close(fd)


def write_private_file(dir_fd: int, name: str, data: bytes, *, mode: int = 0o600) -> None:
    """A new file ``name`` in ``dir_fd`` holding ``data`` (written in full and
    fsynced), then the directory fsynced. Never follows or replaces an entry."""
    if "/" in name or name in ("", ".", ".."):
        raise LookupError("UNSAFE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, mode, dir_fd=dir_fd)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(dir_fd)


def replace_private_file(dir_fd: int, name: str, data: bytes, *, tag: str) -> None:
    """Atomically replace ``name`` in ``dir_fd``: a new temporary file (unique
    ``tag``, written in full and fsynced), renamed over ``name``, then the
    directory fsynced. A crash leaves the old file or the new one, never a part;
    a left temporary file is never taken for the saved one."""
    temp = f"{name}.tmp-{tag}"
    write_private_file(dir_fd, temp, data)
    try:
        os.rename(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        os.unlink(temp, dir_fd=dir_fd)
        raise
    os.fsync(dir_fd)
