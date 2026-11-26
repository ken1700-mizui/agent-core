"""File operation types (WP-08; C1 8.3, PHASE_1_IMPLEMENTATION_PLAN 2).

- ``WorkspaceManifest`` is the record Agent Core writes when it creates a
  synthetic work tree: which tree (root path, device and inode), that it is
  synthetic, and what it held at creation. Signed with HMAC-SHA-256 under a key
  derived for this purpose only (``agent_core.safety.workspace``). Its digest is
  the operation's ``workspace_ref`` and must be in the policy's trusted list.
- ``FileOperation`` is what a broker is about to do; ``TicketBinding`` is what
  the gate bound the execution ticket to. The gate compares them.
- ``ExecutionTicket`` is an opaque handle. It carries nothing: only the gate that
  issued it knows whether it is open, used or closed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.errors import FileEffect as FileEffect  # re-exported
from agent_core.types.ids import AttemptId, ReceiptId
from agent_core.types.safety import MacHex, RelPath, WriteChange
from agent_core.types.tools import ResourceAccess
from agent_core.types.values import Digest, Identifier, TimestampMs, require_sorted_unique

# The signed manifest's file name, directly under the tree's root.
MANIFEST_FILE_NAME: Final = ".agent-core-workspace.json"
# Temporary files of a write (WP-08 R05): this prefix and 32 random hex digits.
TEMP_FILE_PREFIX: Final = ".agent-core-tmp-"
RESERVED_PREFIXES: Final = (MANIFEST_FILE_NAME, TEMP_FILE_PREFIX)
MAX_MANIFEST_BYTES: Final = 1_048_576
MAX_ENTRIES: Final = 4_096


class EntryKind(StrEnum):
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"


class WorkspaceEntry(FrozenModel):
    """One entry of the tree at creation."""

    path: RelPath
    kind: EntryKind
    size: Annotated[int, Field(strict=True, ge=0, le=1_073_741_824)] | None
    content_digest: Digest | None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if ".." in self.path.split("/") or "*" in self.path:
            raise ValueError("an entry is a concrete relative path")
        if self.path.split("/")[0] == MANIFEST_FILE_NAME:
            raise ValueError("the manifest is not an entry")
        is_file = self.kind is EntryKind.FILE
        if is_file != (self.size is not None) or is_file != (self.content_digest is not None):
            raise ValueError("files have a size and a digest; directories have neither")
        return self


class WorkspaceManifest(FrozenModel):
    manifest_version: Literal[1]
    workspace_id: Identifier
    # Phase 1 operates on synthetic trees only (PHASE_1_IMPLEMENTATION_PLAN 2).
    synthetic: Literal[True]
    root_path: Annotated[
        str, StringConstraints(strict=True, min_length=2, max_length=4_096, pattern=r"^/")
    ]
    root_device: Annotated[int, Field(strict=True, ge=0)]
    root_inode: Annotated[int, Field(strict=True, ge=1)]
    entries: Annotated[tuple[WorkspaceEntry, ...], Field(max_length=MAX_ENTRIES)]
    created_at_ms: TimestampMs

    @field_validator("root_path")
    @classmethod
    def _normal(cls, value: str) -> str:
        parts = value.split("/")[1:]
        if any(p in ("", ".", "..") for p in parts) or "\x00" in value:
            raise ValueError("the root is a normalized absolute path")
        return value

    @field_validator("entries")
    @classmethod
    def _sorted(cls, value: tuple[WorkspaceEntry, ...]) -> tuple[WorkspaceEntry, ...]:
        require_sorted_unique(tuple(e.path for e in value), "entries")
        return value


class SignedWorkspaceManifest(FrozenModel):
    manifest: WorkspaceManifest
    mac: MacHex


class FileOperation(FrozenModel):
    """The one operation a broker is about to perform, as the broker sees it."""

    access: ResourceAccess
    path: RelPath
    change: WriteChange | None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.access is ResourceAccess.WRITE) != (self.change is not None):
            raise ValueError("a write names its change; a read has none")
        if self.change is not None and self.change.path != self.path:
            raise ValueError("the change is for this path")
        return self


class TicketBinding(FrozenModel):
    """What an execution ticket was issued for (returned once, when it is redeemed)."""

    receipt_id: ReceiptId
    attempt_id: AttemptId
    action_hash: Digest
    operation: FileOperation


class ExecutionTicket:
    """An opaque handle for one file operation inside one gate callback. It holds no
    binding and no state of its own; constructing one grants nothing."""

    __slots__ = ("__weakref__",)

    def __repr__(self) -> str:
        return "<ExecutionTicket>"

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("tickets are not serializable")
