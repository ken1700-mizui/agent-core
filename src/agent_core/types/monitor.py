"""P-5 monitor types (WP2-04; P-4 design 7.3, 7.5).

Candidate values only (D17): not final operating values. The liveness record
(heartbeat) is one record, overwritten each cycle; it is trusted only when it
names the current owner epoch and monitor instance and is fresh.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from agent_core.types.base import FrozenModel
from agent_core.types.ids import BrokerInstanceId
from agent_core.types.values import Count, Epoch, TimestampMs

PositiveMs = Annotated[int, Field(strict=True, ge=1, le=86_400_000)]
InstanceHex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]


class MonitorConfig(FrozenModel):
    """Candidates for tests (D17): sandbox 300 s, cycles 1 s / 10 s, CANCELLING
    60 s, RECOVERING 600 s, stop grace 10 s."""

    sandbox_limit_ms: PositiveMs = 300_000
    sandbox_cycle_ms: PositiveMs = 1_000
    run_cycle_ms: PositiveMs = 10_000
    cancelling_ms: PositiveMs = 60_000
    recovering_ms: PositiveMs = 600_000
    stop_grace_ms: PositiveMs = 10_000
    # A heartbeat older than this many run cycles is stale.
    stale_cycles: Annotated[int, Field(strict=True, ge=1, le=100)] = 3


class Heartbeat(FrozenModel):
    """The monitor's liveness record (one, overwritten; P-4 7.5)."""

    owner_epoch: Epoch
    monitor_instance: InstanceHex
    cycle: Count
    at_ms: TimestampMs  # the ledger's time (high-water), not the raw wall clock
    p2_instance: BrokerInstanceId | None


class HeartbeatVerdict(StrEnum):
    TRUSTED = "TRUSTED"
    ABSENT = "ABSENT"
    UNAUTHENTIC = "UNAUTHENTIC"  # bad MAC, unreadable, wrong owner or permissions
    OLDER_EPOCH = "OLDER_EPOCH"
    OTHER_MONITOR = "OTHER_MONITOR"
    OTHER_BROKER = "OTHER_BROKER"
    FUTURE = "FUTURE"  # a time ahead of the ledger's time: never trusted
    STALE = "STALE"
