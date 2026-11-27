"""Signed synthetic work-tree manifests (WP-08; PHASE_1_IMPLEMENTATION_PLAN 2).

- The MAC is HMAC-SHA-256 under a key derived from the owner key for this
  purpose only: ``HMAC(owner_key, "agent-core/v1/workspace-manifest-key")``. The
  owner key itself, the permit key or a key for another purpose does not verify
  a manifest.
- ``manifest_digest`` (domain-tagged SHA-256 of the manifest body) is the
  operation's ``workspace_ref``; the owner pins it in the policy's
  ``trusted_synthetic_workspaces``.
- Binding the manifest to the actual tree (root device and inode) is the file
  broker's job: this module is pure and does no I/O.
"""

from __future__ import annotations

import hashlib
import hmac

from agent_core.types.errors import WorkspaceRejectedError
from agent_core.types.files import SignedWorkspaceManifest, WorkspaceManifest
from agent_core.types.safety import SecretKey
from agent_core.types.serialization import dump_json

_KEY_LABEL = b"agent-core/v1/workspace-manifest-key\x00"
_MAC_DOMAIN = b"agent-core/workspace-manifest-mac/v1\x00"
_DIGEST_DOMAIN = b"agent-core/workspace-manifest/v1\x00"


def manifest_key(owner: SecretKey) -> SecretKey:
    """The manifest key, derived from the owner key for this purpose only."""
    if type(owner) is not SecretKey:
        raise TypeError("the manifest key is derived from a SecretKey")
    return SecretKey(hmac.new(owner.reveal(), _KEY_LABEL, hashlib.sha256).digest())


def _mac(key: SecretKey, manifest: WorkspaceManifest) -> str:
    return hmac.new(key.reveal(), _MAC_DOMAIN + dump_json(manifest), hashlib.sha256).hexdigest()


def sign_manifest(key: SecretKey, manifest: WorkspaceManifest) -> SignedWorkspaceManifest:
    if type(key) is not SecretKey or type(manifest) is not WorkspaceManifest:
        raise TypeError("a manifest is signed with a SecretKey")
    return SignedWorkspaceManifest(manifest=manifest, mac=_mac(key, manifest))


def verify_manifest(key: SecretKey, signed: SignedWorkspaceManifest) -> WorkspaceManifest:
    """The manifest, if its MAC is valid under ``key``; refused otherwise."""
    if type(key) is not SecretKey or type(signed) is not SignedWorkspaceManifest:
        raise WorkspaceRejectedError("MANIFEST_INVALID")
    manifest = WorkspaceManifest.model_validate(signed.manifest)
    if not hmac.compare_digest(_mac(key, manifest), signed.mac):
        raise WorkspaceRejectedError("MANIFEST_MAC_INVALID")
    return manifest


def manifest_digest(manifest: WorkspaceManifest) -> str:
    return "sha256:" + hashlib.sha256(_DIGEST_DOMAIN + dump_json(manifest)).hexdigest()


def file_digest(data: bytes) -> str:
    """The plain SHA-256 digest of file bytes (``WriteChange`` content and state)."""
    if type(data) is not bytes:
        raise TypeError("a file digest is over bytes")
    return "sha256:" + hashlib.sha256(data).hexdigest()
