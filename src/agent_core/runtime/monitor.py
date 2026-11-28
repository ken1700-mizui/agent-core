"""P-5: P1's deadline monitor and the liveness record (WP2-04; P-4 design 7).

Roles (P-4 7.1): P2's external stop is the primary enforcement of sandbox time
limits; this monitor checks and records. Each cycle (``run_cycle_ms``):

1. Runs past their CANCELLING or RECOVERING deadline (ledger time, so a clock
   moved back or a restarted monitor cannot extend it) become
   RECONCILIATION_REQUIRED. That is the only change it makes: no approval, no
   release of a hold, no result, no completion, no retry.
2. It overwrites its liveness record (one record) with its owner epoch,
   instance, cycle and the ledger time.

``assess`` decides whether a liveness record can be trusted: never one with an
older owner epoch, another monitor instance, a stale time, an altered MAC, or
none at all. A failure inside a cycle raises after recording nothing more; the
journal and ledger hold only the escalations already committed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from agent_core.runtime.recovery import (
    RecoveryDeadlines,
    RecoveryDeps,
    escalate_overdue,
    overdue_runs,
)
from agent_core.types.errors import AgentCoreError
from agent_core.types.ids import BrokerInstanceId, RunId
from agent_core.types.monitor import Heartbeat, HeartbeatVerdict, MonitorConfig


class HeartbeatStore(Protocol):
    def write(self, beat: Heartbeat) -> None: ...

    def read(self) -> tuple[Heartbeat | None, bool]: ...


@dataclass
class CycleReport:
    cycle: int
    escalated: list[RunId] = field(default_factory=list)


class DeadlineMonitor:
    def __init__(
        self,
        deps: RecoveryDeps,
        config: MonitorConfig,
        heartbeat: HeartbeatStore,
        *,
        p2_instance: BrokerInstanceId | None = None,
    ) -> None:
        self._deps = deps
        self._config = config
        self._heartbeat = heartbeat
        self._p2 = p2_instance
        self.instance = os.urandom(16).hex()
        self._cycle = 0
        # S-R03: set when the liveness record could not be written; admission then
        # refuses at once (it does not wait for the old record to go stale).
        self.last_error: str | None = None

    @property
    def deps(self) -> RecoveryDeps:
        return self._deps

    @property
    def config(self) -> MonitorConfig:
        return self._config

    @property
    def deadlines(self) -> RecoveryDeadlines:
        return RecoveryDeadlines(
            recovering_ms=self._config.recovering_ms, cancelling_ms=self._config.cancelling_ms
        )

    def cycle(self) -> CycleReport:
        self._cycle += 1
        report = CycleReport(self._cycle)
        for run_id in overdue_runs(self._deps, self.deadlines):
            if self._deps.active(run_id):
                continue  # a live session handles its own Run
            escalate_overdue(self._deps, run_id)
            report.escalated.append(run_id)
        now = self._deps.ledger.observe_time(self._deps.clock.now_ms())
        beat = Heartbeat(
            owner_epoch=self._deps.sm.owner_epoch,
            monitor_instance=self.instance,
            cycle=self._cycle,
            at_ms=now,
            p2_instance=self._p2,
        )
        try:
            self._heartbeat.write(beat)
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        self.last_error = None
        return report


def assess(
    store: HeartbeatStore,
    *,
    owner_epoch: int,
    monitor_instance: str,
    now_ms: int,
    config: MonitorConfig,
    p2_instance: BrokerInstanceId | None = None,
) -> HeartbeatVerdict:
    """S-R03: trusted only when authentic, of the current owner epoch, monitor
    and broker instance, not ahead of the ledger's time and not stale."""
    try:
        beat, authentic = store.read()
    except OSError:
        return HeartbeatVerdict.UNAUTHENTIC
    if not authentic:
        return HeartbeatVerdict.UNAUTHENTIC
    if beat is None:
        return HeartbeatVerdict.ABSENT
    if beat.owner_epoch != owner_epoch:
        return HeartbeatVerdict.OLDER_EPOCH  # older, or one this runtime never wrote
    if beat.monitor_instance != monitor_instance:
        return HeartbeatVerdict.OTHER_MONITOR
    if p2_instance is not None and beat.p2_instance != p2_instance:
        return HeartbeatVerdict.OTHER_BROKER
    if beat.at_ms > now_ms:
        return HeartbeatVerdict.FUTURE
    if now_ms - beat.at_ms > config.run_cycle_ms * config.stale_cycles:
        return HeartbeatVerdict.STALE
    return HeartbeatVerdict.TRUSTED


class SandboxAdmissionError(AgentCoreError):
    """S-R03: no trusted liveness record: no Permit and no sandbox start."""

    code = "SANDBOX_ADMISSION_REFUSED"


def sandbox_admission(
    monitor: DeadlineMonitor, store: HeartbeatStore, *, p2_instance: BrokerInstanceId | None
) -> Callable[[], None]:
    """The check used before a sandbox Permit is issued and before P2 is asked
    to start: raises unless this runtime's own monitor is alive and current."""

    def check() -> None:
        if monitor.last_error is not None:
            raise SandboxAdmissionError(f"the liveness record failed: {monitor.last_error}")
        deps = monitor.deps
        verdict = assess(
            store,
            owner_epoch=deps.sm.owner_epoch,
            monitor_instance=monitor.instance,
            now_ms=deps.ledger.observe_time(deps.clock.now_ms()),
            config=monitor.config,
            p2_instance=p2_instance,
        )
        if verdict is not HeartbeatVerdict.TRUSTED:
            raise SandboxAdmissionError(f"the monitor's liveness is not trusted: {verdict.value}")

    return check
