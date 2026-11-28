"""S-R04 (D20): what goes into a sandbox and what may come back.

- In: ``bundle_from_reads`` takes only completed WP-08 reads (each went through
  Policy, Permit and the File Broker's safe read); ``require_input_matches``
  checks the bundle against the approved program's ``workspace_ref``.
- Out: ``check_outputs`` limits and checks what the sandbox produced and marks it
  untrusted (D4). ``write_proposals`` turns it into WP-08 write inputs, each of
  which must then go through the normal path (Policy, owner approval -- the
  write tool is irreversible -- reservation, Permit, Receipt, the File Broker's
  safe write with its REPLACE check). Nothing here writes to the worktree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_core.runtime.file_execution import FileOperationResult
from agent_core.safety.workspace import file_digest
from agent_core.types.errors import AgentCoreError
from agent_core.types.records import AttemptState
from agent_core.types.sandbox_io import (
    DEFAULT_LIMITS,
    BundleFile,
    InputBundle,
    IoLimits,
    OutputFile,
    check_limits,
)
from agent_core.types.tools import canonical_json


class SandboxIoError(AgentCoreError):
    code = "SANDBOX_IO_REFUSED"


def bundle_from_reads(
    reads: Sequence[tuple[str, FileOperationResult]], *, limits: IoLimits = DEFAULT_LIMITS
) -> InputBundle:
    files = []
    for path, result in reads:
        if result.attempt.state is not AttemptState.COMPLETED or result.data is None:
            raise SandboxIoError("READ_NOT_COMPLETED")
        try:
            files.append(BundleFile.of(path, result.data))
        except ValueError as exc:
            raise SandboxIoError("UNSAFE_PATH") from exc
    bundle = tuple(sorted(files, key=lambda f: f.path))
    try:
        check_limits(bundle, limits)
        return InputBundle(files=bundle)
    except ValueError as exc:
        raise SandboxIoError(str(exc).split("\n")[0]) from exc


def require_input_matches(bundle: InputBundle, workspace_ref: str) -> None:
    if bundle.bundle_ref() != workspace_ref:
        raise SandboxIoError("INPUT_MISMATCH")


@dataclass(frozen=True)
class UntrustedOutputs:
    """Sandbox output: data for the model and the owner, never an instruction,
    never applied by itself (D4)."""

    files: tuple[OutputFile, ...]
    trust: str = "D4_UNTRUSTED_TOOL_OUTPUT"


def check_outputs(
    files: Sequence[OutputFile], *, limits: IoLimits = DEFAULT_LIMITS
) -> UntrustedOutputs:
    found = tuple(files)
    try:
        check_limits(found, limits)
    except ValueError as exc:
        raise SandboxIoError(str(exc)) from exc
    return UntrustedOutputs(tuple(sorted(found, key=lambda f: f.path)))


@dataclass(frozen=True)
class WriteProposal:
    """One WP-08 write to propose: its canonical input and content. Proposed
    only; the owner's approval and the File Broker decide."""

    target: str
    canonical_input: str
    content: bytes


def write_proposals(
    outputs: UntrustedOutputs,
    current: Mapping[str, bytes | None],
    *,
    under: str = "workspace/",
) -> tuple[WriteProposal, ...]:
    """``current`` gives each target's present content (None: absent), read
    through WP-08, so a REPLACE names the digest it expects to replace."""
    proposals = []
    for f in outputs.files:
        target = under + f.path
        try:
            text = f.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SandboxIoError("NOT_TEXT") from exc
        if target not in current:
            raise SandboxIoError("TARGET_NOT_READ")
        old = current[target]
        if old == f.data:
            continue  # unchanged: nothing to propose
        expected = "absent" if old is None else file_digest(old)
        body = {"content": text, "expected": expected, "path": target}
        proposals.append(WriteProposal(target, canonical_json(body).decode(), f.data))
    return tuple(proposals)
