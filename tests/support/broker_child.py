"""Fixed local P1-side child for the WP2-02 process tests (tests only).

Usage: python broker_child.py <socket> <epoch> <request.json>

Sends one request (``start`` or ``status``) to the broker at <socket> through
``BrokerClient`` with the synthetic test key, and prints one JSON line: the
result, or ``{"error": code, "sent": bool, "detail": text}``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel

from agent_core.adapters.broker_client import BrokerClient, unix_connector
from agent_core.types.errors import SandboxUnavailableError
from agent_core.types.safety import SecretKey
from agent_core.types.sandbox import StartRequest, StatusRequest
from support import broker_world as bw


def main() -> int:
    socket_path, epoch, request_text = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    # The OS-user tests (E-2) give the P2 user and P1's own key file.
    key_file = os.environ.get("BROKER_CHILD_KEY_FILE")
    key = SecretKey(bytes.fromhex(Path(key_file).read_text().strip())) if key_file else bw.KEY
    client = BrokerClient(
        connector=unix_connector(socket_path),
        key=key,
        p2_uid=int(os.environ.get("BROKER_CHILD_P2_UID", bw.UID)),
        owner_epoch=lambda: epoch,
        admission=bw.ADMITTED,  # this child tests the broker and the OS users
        timeout_s=10.0,
    )
    request = json.loads(request_text)
    kind = request.pop("kind")
    result: BaseModel
    try:
        if kind == "start":
            result = client.start(StartRequest.model_validate_json(json.dumps(request)))
        else:
            result = client.status(StatusRequest.model_validate_json(json.dumps(request)))
    except SandboxUnavailableError as exc:
        print(json.dumps({"error": exc.code, "sent": exc.sent, "detail": str(exc)}))
        return 0
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
