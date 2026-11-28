"""Messages and states of the Deadline Supervisor (S-R01; D20).

The Deadline Supervisor (DS) is a process separate from P1 and P2 whose only
job is the time limit: it holds each sandbox's registered deadline and, when it
passes, kills that sandbox's cgroup. It has no approval, Permit, cost or retry
path. P2 talks to it before and after a start; P1 never talks to it.

A target is one cgroup directly under the DS's delegated root, named by P2
(``agentcore-...``): a single path component, so it cannot name anything
outside the root.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

from agent_core.types.base import FrozenModel
from agent_core.types.ids import AttemptId, BrokerInstanceId, ReceiptId
from agent_core.types.sandbox import DeadlineMs, GraceMs

TARGET_PATTERN = r"^agentcore-[a-z0-9][a-z0-9_.-]{0,100}$"
Target = Annotated[str, StringConstraints(strict=True, pattern=TARGET_PATTERN)]


class DsHealth(StrEnum):
    OK = "OK"
    FAIL_SAFE = "FAIL_SAFE"  # the record cannot be trusted: everything refused
    UNCONFIRMED_STOP = "UNCONFIRMED_STOP"  # a kill was not confirmed: new work refused
    UNAVAILABLE = "UNAVAILABLE"  # no cgroup v2 root, no boot clock


class TargetState(StrEnum):
    REGISTERED = "REGISTERED"  # registered, the start not yet confirmed
    CONFIRMED = "CONFIRMED"  # P2 may start it now (within the start window)
    ATTACHED = "ATTACHED"  # started; the cgroup was seen
    KILL_REQUESTED = "KILL_REQUESTED"  # cgroup.kill written, not yet confirmed
    KILLED = "KILLED"  # confirmed: the cgroup has no process
    KILL_UNCONFIRMED = "KILL_UNCONFIRMED"  # the kill could not be confirmed in time
    NOT_RUNNING = "NOT_RUNNING"  # at its deadline the cgroup did not exist
    RELEASED = "RELEASED"  # P2 reaped it and the cgroup is gone

    @property
    def active(self) -> bool:
        return self in _ACTIVE


_ACTIVE = frozenset(
    {
        TargetState.REGISTERED,
        TargetState.CONFIRMED,
        TargetState.ATTACHED,
        TargetState.KILL_REQUESTED,
        TargetState.KILL_UNCONFIRMED,
    }
)


class DsRefusal(StrEnum):
    FAIL_SAFE = "FAIL_SAFE"
    UNCONFIRMED_STOP = "UNCONFIRMED_STOP"
    UNAVAILABLE = "UNAVAILABLE"
    DUPLICATE = "DUPLICATE"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    EXPIRED_BEFORE_START = "EXPIRED_BEFORE_START"
    STATE_MISMATCH = "STATE_MISMATCH"
    STILL_RUNNING = "STILL_RUNNING"
    RECORD_UNAVAILABLE = "RECORD_UNAVAILABLE"
    MALFORMED = "MALFORMED"
    AUTHENTICATION = "AUTHENTICATION"
    PEER = "PEER"


class Registration(FrozenModel):
    receipt_id: ReceiptId
    attempt_id: AttemptId
    target: Target
    deadline_ms: DeadlineMs
    grace_ms: GraceMs
    p2_instance: BrokerInstanceId


class TargetView(FrozenModel):
    receipt_id: ReceiptId
    target: Target
    state: TargetState
    stop_requested: bool
    stop_confirmed: bool


class DsStatus(FrozenModel):
    instance_id: str
    health: DsHealth
    reason: str | None
    active: int
    unconfirmed: int
