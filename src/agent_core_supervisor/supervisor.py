"""The Deadline Supervisor's rules (S-R01; ``DEADLINE_SUPERVISOR_DESIGN.md``).

Contract with P2 (each step refused unless the supervisor is healthy):
1. ``register`` before anything starts: the target must not exist yet; the
   registration is synced to the record before the answer.
2. ``confirm_start`` just before the start: refused when the start window
   (``start_window_ms`` from the registration) has passed, the boot changed, the
   target already exists, or the registration is not waiting for a start.
3. ``started`` after the start: the cgroup is now expected.
4. ``release`` after P2 reaped it: refused while the cgroup still has processes.

The deadline counts from the registration on the boot clock (boot ID and
CLOCK_BOOTTIME: a wall clock moved back changes nothing). At the deadline the
supervisor writes ``cgroup.kill`` (a stop REQUEST) and records a stop as
CONFIRMED only when the cgroup has no process left; a kill not confirmed within
``confirm_ms`` is KILL_UNCONFIRMED, and new work is refused until it is.
A different boot ID means the deadline cannot be restored: it is killed now.

Scope: in normal operation only registered targets are touched. FAIL_SAFE
(the record cannot be trusted, so nothing is known to be registered) kills
every sandbox-named cgroup directly under the delegated root, which holds
Agent Core sandboxes only, and refuses everything until the owner restores a
record. Nothing outside the delegated root is ever touched.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.types.ids import ReceiptId
from agent_core.types.supervisor import (
    DsHealth,
    DsRefusal,
    DsStatus,
    Registration,
    TargetState,
    TargetView,
)
from agent_core_supervisor.cgroups import CgroupError, CgroupPort
from agent_core_supervisor.record import SupervisorRecord, SupervisorRecordError

BootClock = Callable[[], tuple[str, int]]


def system_boot_clock() -> tuple[str, int]:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return boot_id, time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1_000_000


class DsRefusedError(Exception):
    def __init__(self, code: DsRefusal, detail: str = "") -> None:
        super().__init__(f"{code.value}{': ' + detail if detail else ''}")
        self.code = code


class Supervisor:
    def __init__(
        self,
        *,
        record: SupervisorRecord | None,
        record_error: str | None,
        cgroups: CgroupPort,
        instance_id: str,
        boot: BootClock = system_boot_clock,
        start_window_ms: int = 10_000,
        margin_ms: int = 1_000,
        confirm_ms: int = 2_000,
    ) -> None:
        self._record = record
        self.record_error = record_error
        self._cgroups = cgroups
        self._instance = instance_id
        self._boot = boot
        self._start_window = start_window_ms
        self._margin = margin_ms
        self._confirm = confirm_ms
        self._lock = threading.Lock()
        self._targets: dict[str, dict[str, Any]] = {}
        if record is not None:
            for event in record.entries:
                self._apply(event)
        else:
            self._fail_safe_kill()

    @classmethod
    def open(
        cls,
        *,
        record_path: Path,
        key: bytes,
        cgroups: CgroupPort,
        instance_id: str,
        boot: BootClock = system_boot_clock,
        **kw: int,
    ) -> Supervisor:
        try:
            record: SupervisorRecord | None = SupervisorRecord.open(record_path, key)
            error = None
        except (SupervisorRecordError, OSError, ValueError) as exc:
            record, error = None, f"{type(exc).__name__}: {exc}"
        return cls(
            record=record,
            record_error=error,
            cgroups=cgroups,
            instance_id=instance_id,
            boot=boot,
            **kw,
        )

    def close(self) -> None:
        with self._lock:
            if self._record is not None:
                self._record.close()
                self._record = None
                self.record_error = "closed"

    # ------------------------------------------------------------ state

    def _apply(self, event: dict[str, Any]) -> None:
        if event["kind"] == "register":
            self._targets[event["receipt_id"]] = dict(event, state=TargetState.REGISTERED.value)
        else:
            self._targets[event["receipt_id"]].update(
                state=event["state"], **{k: v for k, v in event.items() if k.endswith("_ms")}
            )

    def _write(self, event: dict[str, Any]) -> None:
        if self._record is None:
            raise DsRefusedError(DsRefusal.FAIL_SAFE, self.record_error or "")
        try:
            self._record.append(event)
        except OSError as exc:
            self._record, self.record_error = None, f"write failed: {exc}"
            raise DsRefusedError(DsRefusal.RECORD_UNAVAILABLE) from exc
        self._apply(event)

    def _set(self, receipt: str, state: TargetState, **extra: Any) -> None:
        self._write({"kind": "state", "receipt_id": receipt, "state": state.value, **extra})

    def _boot_now(self) -> tuple[str, int] | None:
        try:
            return self._boot()
        except (OSError, ValueError):
            return None

    def _health(self) -> tuple[DsHealth, str | None]:
        if self._record is None:
            return DsHealth.FAIL_SAFE, self.record_error
        reason = self._cgroups.unavailable()
        if reason is not None:
            return DsHealth.UNAVAILABLE, reason
        if self._boot_now() is None:
            return DsHealth.UNAVAILABLE, "no boot clock"
        if any(t["state"] == TargetState.KILL_UNCONFIRMED.value for t in self._targets.values()):
            return DsHealth.UNCONFIRMED_STOP, "a kill is not confirmed"
        return DsHealth.OK, None

    def _require_ok(self) -> tuple[str, int]:
        health, reason = self._health()
        if health is not DsHealth.OK:
            raise DsRefusedError(DsRefusal(health.value), reason or "")
        boot = self._boot_now()
        assert boot is not None  # noqa: S101 (checked by _health)
        return boot

    def _view(self, receipt: str) -> TargetView:
        t = self._targets[receipt]
        state = TargetState(t["state"])
        return TargetView(
            receipt_id=ReceiptId(receipt),
            target=t["target"],
            state=state,
            stop_requested=state
            in (TargetState.KILL_REQUESTED, TargetState.KILL_UNCONFIRMED, TargetState.KILLED),
            stop_confirmed=state is TargetState.KILLED,
        )

    # ------------------------------------------------------------ the contract

    def status(self) -> DsStatus:
        with self._lock:
            health, reason = self._health()
            active = [t for t in self._targets.values() if TargetState(t["state"]).active]
            return DsStatus(
                instance_id=self._instance,
                health=health,
                reason=reason,
                active=len(active),
                unconfirmed=sum(t["state"] == TargetState.KILL_UNCONFIRMED.value for t in active),
            )

    def register(self, reg: Registration) -> TargetView:
        with self._lock:
            boot_id, now = self._require_ok()
            receipt = str(reg.receipt_id)
            if receipt in self._targets:
                raise DsRefusedError(DsRefusal.DUPLICATE, "receipt already registered")
            if any(
                t["target"] == reg.target and TargetState(t["state"]).active
                for t in self._targets.values()
            ):
                raise DsRefusedError(DsRefusal.DUPLICATE, "target already registered")
            if self._cgroups.exists(reg.target):
                raise DsRefusedError(DsRefusal.STATE_MISMATCH, "the target exists before its start")
            self._write(
                {
                    "kind": "register",
                    "receipt_id": receipt,
                    "attempt_id": str(reg.attempt_id),
                    "target": reg.target,
                    "p2_instance": str(reg.p2_instance),
                    "boot_id": boot_id,
                    "at_boot_ms": now,
                    "start_by_boot_ms": now + self._start_window,
                    "deadline_boot_ms": now + reg.deadline_ms + reg.grace_ms + self._margin,
                }
            )
            return self._view(receipt)

    def _known(self, receipt: ReceiptId) -> dict[str, Any]:
        t = self._targets.get(str(receipt))
        if t is None:
            raise DsRefusedError(DsRefusal.UNKNOWN_TARGET)
        return t

    def confirm_start(self, receipt: ReceiptId) -> TargetView:
        with self._lock:
            boot_id, now = self._require_ok()
            t = self._known(receipt)
            if t["state"] != TargetState.REGISTERED.value or t["boot_id"] != boot_id:
                raise DsRefusedError(DsRefusal.STATE_MISMATCH, t["state"])
            if now > t["start_by_boot_ms"] or now >= t["deadline_boot_ms"]:
                self._set(str(receipt), TargetState.NOT_RUNNING, closed_at_ms=now)
                raise DsRefusedError(DsRefusal.EXPIRED_BEFORE_START)
            if self._cgroups.exists(t["target"]):
                raise DsRefusedError(DsRefusal.STATE_MISMATCH, "the target exists before its start")
            self._set(str(receipt), TargetState.CONFIRMED, confirmed_at_ms=now)
            return self._view(str(receipt))

    def started(self, receipt: ReceiptId) -> TargetView:
        """A report after the start (accepted even when unhealthy: the deadline
        is enforced either way)."""
        with self._lock:
            t = self._known(receipt)
            if t["state"] != TargetState.CONFIRMED.value:
                raise DsRefusedError(DsRefusal.STATE_MISMATCH, t["state"])
            if self._cgroups.exists(t["target"]):
                self._set(str(receipt), TargetState.ATTACHED)
            return self._view(str(receipt))

    def release(self, receipt: ReceiptId) -> TargetView:
        with self._lock:
            t = self._known(receipt)
            if TargetState(t["state"]).active:
                if self._cgroups.exists(t["target"]) and self._cgroups.populated(t["target"]):
                    raise DsRefusedError(DsRefusal.STILL_RUNNING)
                self._set(str(receipt), TargetState.RELEASED)
            return self._view(str(receipt))

    def view(self, receipt: ReceiptId) -> TargetView:
        with self._lock:
            self._known(receipt)
            return self._view(str(receipt))

    # ------------------------------------------------------------ enforcement

    def _fail_safe_kill(self) -> None:
        if self._cgroups.unavailable() is not None:
            return
        for target in self._cgroups.targets():
            try:
                self._cgroups.kill(target)
            except (CgroupError, OSError):
                continue  # nothing is trusted; the next tick tries again

    def tick(self) -> None:
        """One enforcement cycle (the service runs it every second)."""
        with self._lock:
            if self._record is None:
                self._fail_safe_kill()
                return
            boot = self._boot_now()
            for receipt, t in list(self._targets.items()):
                state = TargetState(t["state"])
                if state.active:
                    self._enforce(receipt, t, state, boot)

    def _enforce(
        self, receipt: str, t: dict[str, Any], state: TargetState, boot: tuple[str, int] | None
    ) -> None:
        same_boot = boot is not None and boot[0] == t["boot_id"]
        now = boot[1] if boot is not None else 0
        expired = not same_boot or now >= t["deadline_boot_ms"]
        try:
            exists = self._cgroups.exists(t["target"])
            populated = exists and self._cgroups.populated(t["target"])
        except (CgroupError, OSError):
            exists = populated = True  # cannot tell: treated as running
        if state is TargetState.REGISTERED and exists:
            expired = True  # started without a confirmed start: a mismatch, killed now
        if state in (TargetState.KILL_REQUESTED, TargetState.KILL_UNCONFIRMED):
            expired = True  # a requested stop is followed until it is confirmed
        if state in (TargetState.REGISTERED, TargetState.CONFIRMED) and not exists:
            if now > t["start_by_boot_ms"] or expired:
                self._set(receipt, TargetState.NOT_RUNNING, closed_at_ms=now)
            return
        if not expired:
            return
        if not populated:
            closed = (
                TargetState.KILLED if state.name.startswith("KILL") else TargetState.NOT_RUNNING
            )
            self._set(receipt, closed, closed_at_ms=now)
            return
        try:
            self._cgroups.kill(t["target"])
            written = True
        except (CgroupError, OSError):
            written = False
        if state not in (TargetState.KILL_REQUESTED, TargetState.KILL_UNCONFIRMED):
            self._set(receipt, TargetState.KILL_REQUESTED, kill_at_ms=now, written=written)
            return
        try:
            gone = not self._cgroups.populated(t["target"])
        except (CgroupError, OSError):
            gone = False
        if gone:
            self._set(receipt, TargetState.KILLED, closed_at_ms=now)
        elif state is TargetState.KILL_REQUESTED and (
            not same_boot or now - t.get("kill_at_ms", now) >= self._confirm
        ):
            self._set(receipt, TargetState.KILL_UNCONFIRMED)
