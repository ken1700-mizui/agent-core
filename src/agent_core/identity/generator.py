"""ID generation with injected Clock and RandomSource.

- The 48-bit timestamp comes from ``Clock.now_ms``; it must lie in
  [``MIN_GENERATION_TIMESTAMP_MS``, ``MAX_TIMESTAMP_MS``], otherwise
  ``ClockOutOfRangeError`` (fatal). A clock that goes backwards is accepted:
  IDs are not an ordering (E 2.2, item 3).
- The 80 random bits come from ``RandomSource.token_bytes(10)``. A failing
  source or wrong-length output raises ``RandomSourceError`` (fatal).
- ``issue_unique`` passes a new ID to a write function. If the write raises
  ``DuplicateIdError``, the random part is regenerated (same timestamp) and
  the write retried, at most ``MAX_DUPLICATE_RETRIES`` times; then
  ``IdCollisionRetryExhaustedError`` (fatal). Other errors propagate unchanged.
- Contract for ``write``: raise ``DuplicateIdError`` only when it is certain
  that nothing was stored (the store detected the duplicate and rolled the
  whole transaction back). A write whose outcome is unknown must raise a
  different error (for example ``JournalWriteOutcomeUnknownError``), which is
  never retried here, so an unknown write is not repeated under a new ID.

Durable uniqueness is the Journal's UNIQUE constraint (WP-03), not this class.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_core.ports.clock import Clock
from agent_core.ports.randomness import RandomSource
from agent_core.types.errors import (
    ClockOutOfRangeError,
    DuplicateIdError,
    IdCollisionRetryExhaustedError,
    RandomSourceError,
)
from agent_core.types.ids import (
    MAX_TIMESTAMP_MS,
    MIN_GENERATION_TIMESTAMP_MS,
    RANDOM_BYTES,
    EntityId,
    SiteId,
)

# E_INTEGRATION 2.2: regenerate and rewrite at most 3 times, then FATAL.
MAX_DUPLICATE_RETRIES = 3


class IdGenerator:
    def __init__(self, site: SiteId, clock: Clock, random: RandomSource) -> None:
        self._site = SiteId(site)
        self._clock = clock
        self._random = random

    @property
    def site(self) -> SiteId:
        return self._site

    def new[T: EntityId](self, id_type: type[T]) -> T:
        return id_type.compose(self._site, self._timestamp(), self._random_bits())

    def issue_unique[T: EntityId, R](self, id_type: type[T], write: Callable[[T], R]) -> R:
        timestamp = self._timestamp()
        last_duplicate: DuplicateIdError | None = None
        for _ in range(MAX_DUPLICATE_RETRIES + 1):
            candidate = id_type.compose(self._site, timestamp, self._random_bits())
            try:
                return write(candidate)
            except DuplicateIdError as exc:
                last_duplicate = exc
        raise IdCollisionRetryExhaustedError(MAX_DUPLICATE_RETRIES + 1) from last_duplicate

    def _timestamp(self) -> int:
        now = self._clock.now_ms()
        if type(now) is not int or not MIN_GENERATION_TIMESTAMP_MS <= now <= MAX_TIMESTAMP_MS:
            raise ClockOutOfRangeError("clock value cannot be encoded in an id")
        return now

    def _random_bits(self) -> int:
        try:
            data = self._random.token_bytes(RANDOM_BYTES)
        except Exception as exc:
            # Re-raised as a typed fatal error with the cause attached; not swallowed.
            raise RandomSourceError("random source failed") from exc
        if type(data) is not bytes or len(data) != RANDOM_BYTES:
            raise RandomSourceError("random source returned malformed output")
        return int.from_bytes(data, "big")
