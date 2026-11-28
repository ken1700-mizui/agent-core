"""Helpers for the WP2-03/04 Docker tests (``sandbox`` category; E-1 to E-4).

The image is the one recorded for E-3: ``docker.io/library/busybox:1.37.0``,
manifest digest ``sha256:a7dbc319...289ec``, image ID ``sha256:30ecbe15...647fb``
(official Docker Hub library image, pulled by dockerd through the environment's
HTTPS proxy, 2026-11-28). Only the project's fixed diagnostic programs run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_core.runtime.sandbox_execution import (
    SandboxOperationResult,
    SandboxProgram,
    run_sandbox_operation,
)
from agent_core.types.ids import BrokerInstanceId, RequestId
from agent_core.types.records import Attempt
from agent_core_broker.docker_api import DockerAPI
from agent_core_broker.docker_backend import LABEL, DockerProfile, DockerSandbox, program_ref
from agent_core_broker.record import ExecutionRecord
from agent_core_broker.server import Broker
from support import broker_world as bw
from support import builders as b
from support import ledger_world as lw
from support import sandbox_world as sw

IMAGE_DIGEST = "busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
IMAGE_ID = "sha256:30ecbe1509090b38ef3260f3609d64d8261230464eaa4d235901db496e9647fb"
PROFILE = DockerProfile(image_digest=IMAGE_DIGEST, image_id=IMAGE_ID)


def sandbox(directory: Path, *, diagnose: bool = True) -> DockerSandbox:
    staging = directory / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    found = DockerSandbox(DockerAPI(), PROFILE, staging)
    if diagnose:
        found.self_diagnose()
    return found


def broker(directory: Path, *, clock: Any = None, backend: DockerSandbox | None = None) -> Broker:
    directory.mkdir(parents=True, exist_ok=True)
    path = bw.record_path(directory)
    if not path.exists():
        ExecutionRecord.create(path, bw.KEY).close()
    return Broker.open(
        record_path=path,
        sandbox=backend if backend is not None else sandbox(directory),
        key=bw.KEY,
        p1_uid=bw.UID,
        instance_id=b.new_id(BrokerInstanceId),
        clock=clock if clock is not None else bw.ManualMonotonic(),
        allowed_profiles=frozenset({PROFILE.profile_hash()}),
        # This dev environment has no cgroup v2 and no systemd, so no Deadline
        # Supervisor: only the fixed diagnostics run here (D17, D20). Not SG-1.
        supervisor=None,
        diagnostics_without_supervisor=True,
    )


def program(name: str, **overrides: Any) -> SandboxProgram:
    values: dict[str, Any] = {
        "sandbox_profile_hash": PROFILE.profile_hash(),
        "image_digest": b.digest(IMAGE_DIGEST),
        "workspace_ref": b.digest("workspace"),
        "program_ref": program_ref(name),
        "deadline_ms": 30_000,
        "grace_ms": 1_000,
    }
    values.update(overrides)
    return SandboxProgram(**values)


def consumed(st: lw.Setup, name: str, **overrides: Any) -> Attempt:
    p = program(name, **overrides)
    attempt = lw.issued(
        st, lw.step_in(st), registration=sw.SANDBOX_TOOL, canonical_input=sw.program_input(p)
    )
    return lw.consume(st, attempt)


def run(st: lw.Setup, p2: Broker, attempt: Attempt, **kw: Any) -> SandboxOperationResult:
    import time

    kw.setdefault("admission", bw.ADMITTED)
    return run_sandbox_operation(
        gate=lw.gate(st),
        sandbox=kw.pop("client", None) or bw.make_client(p2, epoch=st.journal.owner_epoch),
        attempt=attempt,
        new_request_id=lambda: b.new_id(RequestId),
        max_polls=kw.pop("max_polls", 150),
        wait=kw.pop("wait", lambda: time.sleep(0.2)),
        **lw.actual(st, attempt),
        **kw,
    )


def remove_managed_containers() -> int:
    api = DockerAPI()
    query = api.q(json.dumps({"label": [f"{LABEL}=true"]}))
    found = api.call("GET", f"/containers/json?all=true&filters={query}") or []
    for item in found:
        api.call("DELETE", f"/containers/{item['Id']}?force=true&v=true", ok=(204, 404))
    return len(found)
