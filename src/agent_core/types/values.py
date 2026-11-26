"""Value types shared by records: bounded integers, hashes, labels, budgets, caps.

Integers are bounded to the signed 64-bit range because they are stored in
SQLite INTEGER columns (WP-03) and money is a 64-bit integer of minimum units
(E_INTEGRATION 4.3). There are no float fields: amounts and counts are integers,
so NaN and Infinity cannot appear.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

from agent_core.types.base import FrozenModel

MAX_INT64 = (1 << 63) - 1

# A position in the journal: gapless and starting at 1 (A.I1, E_INTEGRATION 2.3).
Seq = Annotated[int, Field(strict=True, ge=1, le=MAX_INT64)]
# Generation counters: access_epoch, owner_epoch, revocation_gen.
Epoch = Annotated[int, Field(strict=True, ge=0, le=MAX_INT64)]
# Non-negative counts and minimum currency units.
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_INT64)]
# Milliseconds since the Unix epoch, within the 48-bit range used by IDs.
TimestampMs = Annotated[int, Field(strict=True, ge=0, le=(1 << 48) - 1)]

Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
KeyedDigest = Annotated[str, StringConstraints(strict=True, pattern=r"^hmac-sha256:[0-9a-f]{64}$")]
# Lowercase dotted identifier: event types, request kinds, keyed-hash names.
Identifier = Annotated[
    str,
    StringConstraints(
        strict=True, max_length=64, pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
    ),
]
# Data label (C2), for example "synthetic".
Label = Annotated[
    str,
    StringConstraints(strict=True, max_length=64, pattern=r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$"),
]
PolicyVersion = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
]
SemVer = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
]


def sha256_digest(data: bytes) -> str:
    """Return the ``Digest`` string of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def require_sorted_unique[T: str](values: Sequence[T], what: str) -> None:
    """Collections are stored in one canonical order, so equal sets serialize equally."""
    if list(values) != sorted(set(values)):
        raise ValueError(f"{what} must be sorted and contain no duplicates")


def require_unique[T](values: Sequence[T], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} must not contain duplicates")


class EngineName(StrEnum):
    """The 12 logical Engines (E_INTEGRATION 1.1)."""

    RUNTIME = "runtime"
    CONTEXT = "context"
    MEMORY = "memory"
    TOOL = "tool"
    PLANNING = "planning"
    EVALUATION = "evaluation"
    IMPROVEMENT = "improvement"
    MODEL_GATEWAY = "model_gateway"
    COST = "cost"
    SAFETY = "safety"
    ORCHESTRATION = "orchestration"
    OBSERVABILITY_RECOVERY = "observability_recovery"


class EngineVersion(FrozenModel):
    """``engine@version`` in E_INTEGRATION 8."""

    engine: EngineName
    version: SemVer

    def __str__(self) -> str:
        return f"{self.engine.value}@{self.version}"


class Currency(StrEnum):
    """Phase 1 uses only the test unit XTU (D 10). Real currencies need owner approval."""

    XTU = "XTU"


class Budget(FrozenModel):
    """An amount in minimum units (1 XTU = 1,000,000 micro units). Never negative."""

    currency: Currency
    micro_units: Count


class NonMonetaryCaps(FrozenModel):
    """Non-monetary limits (D 11.2). All are required so no dimension is unbounded."""

    model_calls: Count
    tool_calls: Count
    context_rebuilds: Count
    replans: Count
    memory_operations: Count
    retries: Count
    total_operations: Count
    wall_clock_ms: Count


class KeyedHash(FrozenModel):
    """A named keyed hash (HMAC) stored instead of a sensitive value (C2)."""

    name: Identifier
    value: KeyedDigest
