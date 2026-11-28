"""Docker sandbox backend for P2 (WP2-03; C1 sandbox design 4-6, 8; D17).

Only fixed diagnostic programs of the project run here (E-4). There is no path
for any other program: ``knows_program`` accepts only the refs of
``DIAGNOSTIC_PROGRAMS``, and only after the self-diagnosis passed. Untrusted code
needs all nine checks, including a MAC (AppArmor or SELinux); this backend never
offers it (``untrusted_allowed`` is reported, nothing uses it).

Profile (one, fixed, hashed into ``profile_hash``): image pinned by ID and
digest; non-root user 65534; ``--network none``; read-only root; all
capabilities dropped; ``no-new-privileges``; the daemon's default seccomp; no
privileged mode, devices, ports or host namespaces; memory (no swap), CPU and
process-count limits; ``/tmp`` a size-limited tmpfs (``noexec``); an optional
read-only input directory at ``/in`` (P2's staging copy). Writes are therefore
limited to the tmpfs and the log (``max-size`` set on the daemon). The output
is the program's stdout, read with a size limit.

Every container's effective settings are read back (inspect) and compared with
the profile **before** it starts; any difference removes it and refuses the
start. That check is what ``CONTAINED`` means (``agent_core.types.sandbox``).

Auxiliary supervisor (P-4, not a protection basis): the program runs under
``timeout -s KILL`` inside the container. P2's own deadline (``tick``) is the
primary stop; the daemon's stop (SIGTERM, then SIGKILL after the grace) is how
P2 stops a container.

After a crash of P2, containers it created are found by label (``adopt``):
the broker reaps those whose result is recorded and keeps enforcing the
deadline (wall clock from the label) of the others.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_core.types.ids import ReceiptId
from agent_core.types.sandbox import Containment, SandboxState
from agent_core.types.values import sha256_digest
from agent_core_broker.backend import Observation
from agent_core_broker.docker_api import DockerAPI, DockerError

LABEL = "agent_core.managed"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_INPUT_FILES = 64
MAX_INPUT_BYTES = 1024 * 1024
CONTAINER_TMP = "/tmp"  # noqa: S108 (a path inside the container, not on the host)
# The in-container supervisor kills this long after P2's deadline (candidate).
SUPERVISOR_MARGIN_MS = 5_000
_SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class DockerProfile:
    image_digest: str  # repository digest, e.g. "busybox@sha256:..."
    image_id: str  # local image ID ("sha256:...")
    user: str = "65534:65534"
    memory_bytes: int = 256 * 1024 * 1024
    pids_limit: int = 64
    nano_cpus: int = 1_000_000_000
    tmpfs_bytes: int = 16 * 1024 * 1024

    def profile_hash(self) -> str:
        data = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(b"agent-core/docker-profile/v1\x00" + data).hexdigest()

    def tmpfs(self) -> dict[str, str]:
        # The container's own /tmp (a tmpfs inside it), not a host path.
        return {CONTAINER_TMP: f"rw,noexec,nosuid,nodev,size={self.tmpfs_bytes}"}

    def host_config(self, input_dir: Path | None) -> dict[str, Any]:
        binds = [] if input_dir is None else [f"{input_dir}:/in:ro"]
        return {
            "NetworkMode": "none",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": self.memory_bytes,
            "MemorySwap": self.memory_bytes,
            "PidsLimit": self.pids_limit,
            "NanoCpus": self.nano_cpus,
            "Tmpfs": self.tmpfs(),
            "Binds": binds,
            "AutoRemove": False,
            "RestartPolicy": {"Name": "no"},
            "CgroupnsMode": "private",
            "IpcMode": "private",
            # Docker's init as PID 1: PID 1 of a PID namespace ignores signals it
            # has no handler for, so a supervisor running as PID 1 could not kill
            # its program (observed 2026-11-28). With init, the auxiliary
            # ``timeout`` supervisor works; P2's stop stays the primary means.
            "Init": True,
        }


# Fixed diagnostic programs (E-4). Each prints one line; a failure of the check
# it makes is exit 3. Bounded by design (counts, sizes, the profile's limits).
DIAGNOSTIC_PROGRAMS: dict[str, str] = {
    "diag.exit0": "echo agent-core-diag-ok",
    "diag.exit1": "echo failing; exit 1",
    "diag.sleep": "sleep 3600",
    "diag.ignore_term": "trap '' TERM; while :; do sleep 1; done",
    "diag.identity": "echo uid=$(id -u) gid=$(id -g)",
    "diag.network": (
        "if wget -q -T 2 -O /dev/null http://1.1.1.1/ 2>/dev/null"
        " || nslookup example.com >/dev/null 2>&1; then echo NETWORK_REACHABLE; exit 3;"
        " fi; echo no-network"
    ),
    "diag.rootfs": (
        "if touch /agent-core-probe 2>/dev/null; then echo ROOT_WRITABLE; exit 3; fi;"
        " echo read-only"
    ),
    "diag.tmpfs_limit": (
        "dd if=/dev/zero of=/tmp/fill bs=1M count=64 2>/dev/null;"
        ' n=$(du -m /tmp/fill | cut -f1); if [ "$n" -gt 16 ]; then echo UNLIMITED=$n; exit 3; fi;'
        " echo limited=$n"
    ),
    "diag.tmpfs_noexec": (
        "cp /bin/busybox /tmp/bb 2>/dev/null && /tmp/bb true 2>/dev/null"
        " && { echo TMP_EXECUTABLE; exit 3; }; echo noexec"
    ),
    "diag.pids_limit": (
        "n=$(cat /sys/fs/cgroup/pids/pids.max 2>/dev/null || cat /sys/fs/cgroup/pids.max);"
        ' if [ "$n" = 64 ]; then echo pids.max=$n; else echo PIDS_UNLIMITED=$n; exit 3; fi'
    ),
    "diag.memory_limit": (
        "n=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null"
        " || cat /sys/fs/cgroup/memory.max);"
        ' if [ "$n" = 268435456 ]; then echo memory.limit=$n;'
        " else echo MEMORY_UNLIMITED=$n; exit 3; fi"
    ),
    "diag.status": "grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status",
    # P-5 B: can the code inside stop the in-container supervisor? (A check only;
    # the supervisor is never a protection basis.) Then it sleeps.
    "diag.kill_supervisor": (
        "for p in $(ps -o pid,comm | awk '$2==\"timeout\"{print $1}'); do kill -9 $p; done;"
        " echo supervisor-killed; sleep 3600"
    ),
    "diag.input": (
        "ls /in && cat /in/* && if touch /in/x 2>/dev/null; then echo IN_WRITABLE; exit 3; fi"
    ),
}


def program_ref(name: str) -> str:
    return sha256_digest(f"agent-core/diagnostic-program/{name}".encode())


_BY_REF = {program_ref(name): name for name in DIAGNOSTIC_PROGRAMS}


@dataclass
class Diagnosis:
    checks: dict[str, bool]
    details: dict[str, str]

    @property
    def fixed_programs_allowed(self) -> bool:
        return all(v for k, v in self.checks.items() if k != "mac")

    @property
    def untrusted_allowed(self) -> bool:
        return all(self.checks.values())


def _parse_time(text: str) -> float | None:
    if not text or text.startswith("0001-"):
        return None
    head, _, rest = text.partition(".")
    fraction = rest.rstrip("Z")[:6].ljust(6, "0") if rest else "000000"
    return datetime.fromisoformat(f"{head}.{fraction}+00:00").timestamp()


def _demux(data: bytes) -> bytes:
    """stdout frames of a non-TTY log stream (8-byte headers)."""
    out, offset = bytearray(), 0
    while offset + 8 <= len(data):
        kind, size = data[offset], int.from_bytes(data[offset + 4 : offset + 8], "big")
        chunk = data[offset + 8 : offset + 8 + size]
        if kind == 1:
            out += chunk
        offset += 8 + size
    return bytes(out)


@dataclass
class DockerSandbox:
    api: DockerAPI
    profile: DockerProfile
    staging: Path
    diagnosis: Diagnosis | None = None
    _ids: dict[ReceiptId, str] = field(default_factory=dict)
    _stopped: set[ReceiptId] = field(default_factory=set)
    outputs: dict[ReceiptId, bytes] = field(default_factory=dict)

    # ------------------------------------------------------------ diagnosis

    def self_diagnose(self) -> Diagnosis:
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        try:
            version = self.api.version()
            info = self.api.info()
            checks["daemon"] = True
        except DockerError as exc:
            details["daemon"] = str(exc)
            self.diagnosis = Diagnosis(dict.fromkeys(_CHECKS, False), details)
            return self.diagnosis
        low = tuple(int(x) for x in str(version.get("MinAPIVersion", "9.9")).split("."))
        high = tuple(int(x) for x in str(version.get("ApiVersion", "0.0")).split("."))
        checks["api"] = low <= (1, 44) <= high
        options = " ".join(info.get("SecurityOptions") or [])
        checks["seccomp"] = "name=seccomp" in options and "profile=unconfined" not in options
        checks["userns"] = "name=userns" in options
        checks["no_new_privileges"] = "name=no-new-privileges" in options
        checks["cgroup_limits"] = bool(
            info.get("MemoryLimit") and info.get("PidsLimit") and info.get("CpuCfsQuota")
        )
        try:
            image = self.api.call("GET", f"/images/{self.api.q(self.profile.image_id)}/json")
            digests = image.get("RepoDigests") or []
            checks["image"] = image.get("Id") == self.profile.image_id and (
                self.profile.image_digest in digests
            )
        except DockerError as exc:
            checks["image"] = False
            details["image"] = str(exc)
        checks["no_managed_leftovers_unknown"] = True  # leftovers are adopted (``adopt``)
        checks["mac"] = "name=apparmor" in options or "name=selinux" in options
        details["security_options"] = options
        details["cgroup_version"] = str(info.get("CgroupVersion"))
        self.diagnosis = Diagnosis(checks, details)
        return self.diagnosis

    @property
    def ready(self) -> bool:
        return self.diagnosis is not None and self.diagnosis.fixed_programs_allowed

    def knows_program(self, ref: str) -> bool:
        return self.ready and ref in _BY_REF

    # ------------------------------------------------------------ input staging

    def stage_input(self, receipt_id: ReceiptId, source: Path) -> Path:
        """Copy regular files (no links, no special files, size and count
        limits) from ``source`` into P2's staging area, read-only for the container."""
        target = self.staging / f"in-{_hex(receipt_id)}"
        target.mkdir(mode=0o755)
        total = 0
        for count, entry in enumerate(sorted(os.scandir(source), key=lambda e: e.name)):
            if count >= MAX_INPUT_FILES:
                raise ValueError("too many input files")
            info = os.lstat(entry.path)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"not a regular file: {entry.name}")
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                data = os.read(fd, MAX_INPUT_BYTES + 1)
            finally:
                os.close(fd)
            total += len(data)
            if total > MAX_INPUT_BYTES:
                raise ValueError("input too large")
            out = os.open(target / entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                os.write(out, data)
            finally:
                os.close(out)
        return target

    # ------------------------------------------------------------ lifecycle

    def _verify(self, inspected: dict[str, Any], input_dir: Path | None) -> list[str]:
        host = inspected.get("HostConfig") or {}
        config = inspected.get("Config") or {}
        expected = self.profile.host_config(input_dir)
        problems = [
            k
            for k, v in expected.items()
            if k not in ("AutoRemove", "RestartPolicy") and host.get(k) != v
        ]
        if (host.get("RestartPolicy") or {}).get("Name") != "no":
            problems.append("RestartPolicy")
        for name, bad in (("CapAdd", None), ("Devices", None), ("PortBindings", None)):
            if host.get(name) not in (bad, [], {}):
                problems.append(name)
        for name in ("PidMode", "UTSMode", "UsernsMode"):
            if host.get(name) not in ("", None):
                problems.append(name)
        if inspected.get("Image") != self.profile.image_id:
            problems.append("Image")
        if config.get("User") != self.profile.user or not config.get("NetworkDisabled"):
            problems.append("Config")
        if any(not e.startswith("PATH=") for e in config.get("Env") or []):
            problems.append("Env")
        if inspected.get("Mounts") and any(
            m.get("Type") != "bind" or m.get("RW") for m in inspected.get("Mounts") or []
        ):
            problems.append("Mounts")
        return problems

    def start(
        self,
        receipt_id: ReceiptId,
        ref: str,
        deadline_ms: int,
        *,
        input_dir: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Containment:
        if not self.knows_program(ref):
            raise DockerError("not an allowed program")
        script = DIAGNOSTIC_PROGRAMS[_BY_REF[ref]]
        # The supervisor is a backstop after P2's own deadline (P2 stops first).
        seconds = max(1, -(-(deadline_ms + SUPERVISOR_MARGIN_MS) // 1000))
        host = self.profile.host_config(input_dir)
        if overrides:  # tests only: a container that differs from the profile
            host.update(overrides)
        body = {
            "Image": self.profile.image_id,
            "Cmd": ["timeout", "-s", "KILL", str(seconds), "sh", "-c", script],
            "User": self.profile.user,
            "Env": [f"PATH={_SAFE_PATH}"],
            "WorkingDir": CONTAINER_TMP,
            "NetworkDisabled": True,
            "Labels": {
                LABEL: "true",
                "agent_core.receipt_id": str(receipt_id),
                "agent_core.deadline_unix_ms": str(int(time.time() * 1000) + deadline_ms),
            },
            "HostConfig": host,
        }
        name = f"acsbx-{_hex(receipt_id)}"
        created = self.api.call("POST", f"/containers/create?name={name}", body)
        cid = str(created["Id"])
        inspected = self.api.call("GET", f"/containers/{cid}/json")
        problems = self._verify(inspected, input_dir)
        if problems:
            self.api.call("DELETE", f"/containers/{cid}?force=true&v=true", ok=(204, 404))
            raise DockerError("the container differs from the profile: " + ",".join(problems))
        self._ids[receipt_id] = cid
        self.api.call("POST", f"/containers/{cid}/start", ok=(204, 304))
        return Containment.CONTAINED

    def _container(self, receipt_id: ReceiptId) -> str | None:
        cid = self._ids.get(receipt_id)
        if cid is not None:
            return cid
        name = f"acsbx-{_hex(receipt_id)}"
        try:
            found = self.api.call("GET", f"/containers/{name}/json")
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise
        if (found.get("Config") or {}).get("Labels", {}).get(LABEL) != "true":
            return None
        self._ids[receipt_id] = str(found["Id"])
        return self._ids[receipt_id]

    def observe(self, receipt_id: ReceiptId) -> Observation | None:
        cid = self._container(receipt_id)
        if cid is None:
            return None
        inspected = self.api.call("GET", f"/containers/{cid}/json")
        state = inspected.get("State") or {}
        if state.get("Running") or state.get("Status") in ("created", "restarting"):
            return Observation(SandboxState.RUNNING, None, None, None)
        if receipt_id in self._stopped:
            return Observation(SandboxState.STOPPED, None, None, None)
        started, finished = (
            _parse_time(state.get("StartedAt", "")),
            _parse_time(state.get("FinishedAt", "")),
        )
        if started is None or finished is None:
            return Observation(SandboxState.RUNNING, None, None, None)
        output = self._output(cid)
        self.outputs[receipt_id] = output
        return Observation(
            SandboxState.FINISHED,
            int(state.get("ExitCode", 255)) & 0xFF,
            sha256_digest(output),
            max(0, int((finished - started) * 1000)),
        )

    def _output(self, cid: str) -> bytes:
        status, data = self.api.raw("GET", f"/containers/{cid}/logs?stdout=1&stderr=0")
        if status != 200:
            raise DockerError(f"logs answered {status}", status)
        return _demux(data)[:MAX_OUTPUT_BYTES]

    def stop(self, receipt_id: ReceiptId, grace_ms: int) -> bool:
        cid = self._container(receipt_id)
        if cid is None:
            return False
        seconds = max(0, grace_ms // 1000)
        self.api.call(
            "POST", f"/containers/{cid}/stop?t={seconds}", ok=(204, 304), timeout_s=seconds + 15
        )
        inspected = self.api.call("GET", f"/containers/{cid}/json")
        stopped = not (inspected.get("State") or {}).get("Running")
        if stopped:
            self._stopped.add(receipt_id)
        return stopped

    def reap(self, receipt_id: ReceiptId) -> None:
        cid = self._container(receipt_id)
        if cid is not None:
            self.api.call("DELETE", f"/containers/{cid}?force=true&v=true", ok=(204, 404))
        self._ids.pop(receipt_id, None)
        staged = self.staging / f"in-{_hex(receipt_id)}"
        if staged.is_dir():  # P-5 item 9: temporary data goes with the container
            for item in staged.iterdir():
                item.unlink()
            staged.rmdir()

    def adopt(self) -> list[tuple[ReceiptId, str, bool, int]]:
        """Containers created by a broker (by label): receipt, id, running, deadline."""
        query = self.api.q(json.dumps({"label": [f"{LABEL}=true"]}))
        found = self.api.call("GET", f"/containers/json?all=true&filters={query}")
        adopted = []
        for item in found or []:
            labels = item.get("Labels") or {}
            receipt = ReceiptId(labels["agent_core.receipt_id"])
            self._ids[receipt] = str(item["Id"])
            deadline = int(labels.get("agent_core.deadline_unix_ms", "0"))
            adopted.append((receipt, str(item["Id"]), item.get("State") == "running", deadline))
        return adopted


_CHECKS = (
    "daemon",
    "api",
    "seccomp",
    "userns",
    "no_new_privileges",
    "cgroup_limits",
    "image",
    "no_managed_leftovers_unknown",
    "mac",
)


def _hex(receipt_id: ReceiptId) -> str:
    return hashlib.sha256(str(receipt_id).encode()).hexdigest()[:32]
