"""Records of an experiment's home chain (WP2-08; contract 7.3-7.8, D19).

All amounts are micro XTU, the unit of the ledger. One XTU appears in exactly
one place: a reservation in one home's ledger, or a number in an opening
(``prior_settled_xtu`` or one ``CarriedHold``), never both.

- ``OpeningStatement``: a home's start in the experiment. The genesis (index 0)
  has no previous closing; every later one names the closing it follows and
  must be that closing's ``successor_home_id``. Its ledger cap is
  ``cap_xtu = experiment_cap - prior_settled - carried`` (``deficit_xtu`` when
  that would be negative: the cap is then 0).
- ``ClosingStatement``: the home's totals when it is closed; it fixes its one
  successor (a value derived from the closing itself).
- ``CarriedHoldResolved``: a carried hold settled in its origin home, written
  once per (origin home, reservation) while a later home is the tip.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Self

from pydantic import Field, model_validator

from agent_core.types.base import FrozenModel
from agent_core.types.ids import ExperimentId, HomeId, ReservationId, SiteId
from agent_core.types.serialization import dump_json
from agent_core.types.values import MAX_INT64, Count, Digest

PositiveCount = Annotated[int, Field(strict=True, ge=1, le=MAX_INT64)]

_DOMAIN = b"agent-core/experiment-chain/v1\x00"


def _hash(kind: str, record: FrozenModel) -> str:
    data = _DOMAIN + kind.encode() + b"\x00" + dump_json(record)
    return "sha256:" + hashlib.sha256(data).hexdigest()


class CarriedHold(FrozenModel):
    """A hold kept by its origin home's ledger, carried as a number."""

    origin_home_id: HomeId
    reservation_id: ReservationId
    xtu: PositiveCount

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.origin_home_id), str(self.reservation_id))


def _sorted_holds(holds: tuple[CarriedHold, ...], name: str) -> None:
    keys = [h.key for h in holds]
    if keys != sorted(set(keys)):
        raise ValueError(f"{name} are sorted and unique by (origin home, reservation)")


class OpeningStatement(FrozenModel):
    experiment_id: ExperimentId
    spec_hash: Digest
    home_id: HomeId
    index: Count
    prev_closing_hash: Digest | None
    prior_settled_xtu: Count
    carried: tuple[CarriedHold, ...]
    cap_xtu: Count
    deficit_xtu: Count
    work_units_cap: PositiveCount

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.index == 0) != (self.prev_closing_hash is None):
            raise ValueError("only the genesis (index 0) has no previous closing")
        if self.index == 0 and (self.prior_settled_xtu or self.carried):
            raise ValueError("the genesis carries nothing")
        if self.cap_xtu and self.deficit_xtu:
            raise ValueError("a deficit means a zero cap")
        _sorted_holds(self.carried, "carried holds")
        return self

    @property
    def carried_xtu(self) -> int:
        return sum(h.xtu for h in self.carried)

    def balance(self) -> int:
        """prior + carried + cap - deficit: equals the experiment cap."""
        return self.prior_settled_xtu + self.carried_xtu + self.cap_xtu - self.deficit_xtu

    def opening_hash(self) -> str:
        return _hash("opening", self)


class ClosingBody(FrozenModel):
    experiment_id: ExperimentId
    home_id: HomeId
    opening_hash: Digest
    settled_xtu: Count  # this home's settled cost at closing
    held: tuple[CarriedHold, ...]  # this home's holds (origin = this home)
    carried_unresolved: tuple[CarriedHold, ...]  # earlier homes' holds still open
    invalid_trial_xtu: Count  # reported by the evaluation side; inside settled_xtu
    retry_xtu: Count  # likewise
    journal_seq: Count
    journal_head: Digest | None

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_holds(self.held, "held")
        _sorted_holds(self.carried_unresolved, "carried holds")
        if any(h.origin_home_id != self.home_id for h in self.held):
            raise ValueError("a home's own holds have it as origin")
        if any(h.origin_home_id == self.home_id for h in self.carried_unresolved):
            raise ValueError("carried holds come from earlier homes")
        if self.invalid_trial_xtu + self.retry_xtu > self.settled_xtu:
            raise ValueError("invalid and retried trials' costs are part of the settled total")
        return self

    def successor(self) -> HomeId:
        """``H(experiment_id, closing body)`` (contract 7.8): fixed by the closing."""
        bits = int(_hash("successor", self).split(":", 1)[1][:20], 16)
        return HomeId.compose(SiteId(self.home_id.site), 0, bits)


class ClosingStatement(ClosingBody):
    successor_home_id: HomeId

    @model_validator(mode="after")
    def _successor(self) -> Self:
        body = ClosingBody.model_validate(self.model_dump(exclude={"successor_home_id"}))
        if body.successor() != self.successor_home_id:
            raise ValueError("successor_home_id is derived from the closing")
        return self

    @classmethod
    def of(cls, body: ClosingBody) -> ClosingStatement:
        return cls(**body.model_dump(), successor_home_id=body.successor())

    @property
    def held_xtu(self) -> int:
        return sum(h.xtu for h in self.held)

    def closing_hash(self) -> str:
        return _hash("closing", self)


class CarriedHoldResolved(FrozenModel):
    experiment_id: ExperimentId
    recorded_in: HomeId
    origin_home_id: HomeId
    reservation_id: ReservationId
    carried_xtu: PositiveCount
    settled_xtu: Count
    origin_ref: Digest  # the origin reservation's settled entries

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.origin_home_id), str(self.reservation_id))

    @property
    def delta_xtu(self) -> int:
        """Released (positive) or settled above the hold (negative)."""
        return self.carried_xtu - self.settled_xtu

    def resolution_hash(self) -> str:
        return _hash("resolution", self)
