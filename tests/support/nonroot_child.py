"""Fixed local child process for WP08-R09: the file broker run by a non-root user.

Usage (started by tests/unit/test_wp08_nonroot.py, as uid 65534 when the suite
runs as root, otherwise as the current non-root user):

    python nonroot_child.py <work_dir>

The child creates a managed area and a synthetic tree in ``work_dir`` (its own,
0700), reads and writes through the broker, prints ``ready``, waits for one line
on stdin (the parent then plants entries it owns), and prints one JSON line per
case. The broker is driven directly; the redeem function only returns the
binding (the gate's ticket checks are tested elsewhere). Synthetic data only.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from agent_core.adapters.file_broker import SyntheticFileBroker, create_synthetic_workspace
from agent_core.safety.workspace import manifest_digest, manifest_key
from agent_core.types.errors import SafetyError
from agent_core.types.files import TicketBinding
from agent_core.types.ids import PepInstanceId, PermitId, ReceiptId
from agent_core.types.safety import Consumer, DispatchAuthorization, Receipt, SecretKey
from agent_core.types.tools import canonical_json
from support import builders as b
from support import safety_world as s
from support import tool_world as tw

FILES = {"workspace/a.txt": b"synthetic a\n", "workspace/out/r.txt": b"synthetic old\n"}


def _authorization(descriptor: Any) -> DispatchAuthorization:
    receipt = Receipt(
        receipt_version=1,
        receipt_id=b.new_id(ReceiptId),
        permit_id=b.new_id(PermitId),
        tenant_id=descriptor.tenant_id,
        attempt_id=descriptor.attempt_id,
        reservation_id=descriptor.reservation_id,
        consumer=Consumer.BROKER,
        pep_instance_id=b.new_id(PepInstanceId),
        owner_epoch=1,
        revocation_gen=0,
        expires_at_ms=s.NOW + 60_000,
    )
    return DispatchAuthorization(
        receipt=receipt, action_hash=descriptor.action_hash(), descriptor=descriptor
    )


def _redeem_for(authorization: DispatchAuthorization) -> Any:
    def redeem(_ticket: Any, operation: Any) -> TicketBinding:
        return TicketBinding(
            receipt_id=authorization.receipt.receipt_id,
            attempt_id=authorization.descriptor.attempt_id,
            action_hash=authorization.action_hash,
            operation=operation,
        )

    return redeem


def main() -> int:
    work = Path(sys.argv[1])
    owner = SecretKey(secrets.token_bytes(32))
    area = work / "area"
    area.mkdir(mode=0o700)
    signed = create_synthetic_workspace(
        area=area, workspace_id="tree", files=FILES, key=manifest_key(owner), now_ms=s.NOW
    )
    digest = manifest_digest(signed.manifest)
    policy = s.policy(trusted_synthetic_workspaces=(digest,))
    broker = SyntheticFileBroker(area=area, workspace_id="tree", key=manifest_key(owner))
    root = area / "tree"

    def read(path: str) -> dict[str, Any]:
        descriptor = s.read_descriptor(
            s.ref(),
            workspace_ref=digest,
            canonical_input=canonical_json({"path": path}).decode(),
        )
        authorization = _authorization(descriptor)
        try:
            with broker.prepare(descriptor, policy) as prepared:
                _outcome, data = prepared.read(authorization, object(), _redeem_for(authorization))  # type: ignore[arg-type]
        except SafetyError as exc:
            return {"ok": False, "reasons": list(exc.reasons)}
        return {"ok": True, "data": data.decode()}

    def write(path: str, content: str, expected: str = "absent") -> dict[str, Any]:
        descriptor = s.write_descriptor(
            s.ref(),
            workspace_ref=digest,
            canonical_input=tw.write_input(path, content=content, expected=expected),
        )
        authorization = _authorization(descriptor)
        try:
            with broker.prepare(descriptor, policy) as prepared:
                prepared.write(
                    authorization,
                    object(),  # type: ignore[arg-type]
                    _redeem_for(authorization),
                    content.encode(),
                )
        except SafetyError as exc:
            return {"ok": False, "reasons": list(exc.reasons)}
        info = os.lstat(root / path)
        return {"ok": True, "uid": info.st_uid, "data": (root / path).read_text()}

    results: dict[str, Any] = {"uid": os.geteuid()}
    results["read"] = read("workspace/a.txt")
    results["create"] = write("workspace/out/new.txt", "synthetic new\n")
    # Permission violation only a non-root user meets: no read permission on the
    # file being replaced (root would read it anyway).
    (root / "workspace/out/r.txt").chmod(0o000)
    from agent_core.safety.workspace import file_digest

    results["replace_unreadable"] = write(
        "workspace/out/r.txt", "x", expected=file_digest(FILES["workspace/out/r.txt"])
    )
    print("ready", flush=True)
    sys.stdin.readline()  # the parent plants its entries now
    results["read_root_owned"] = read("workspace/root.txt")
    results["read_symlink"] = read("workspace/link.txt")
    results["read_in_root_dir"] = read("workspace/rootdir/f.txt")
    results["create_in_root_dir"] = write("workspace/rootdir/new.txt", "x")
    print(json.dumps(results, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
