"""Entity IDs (E_INTEGRATION 2.2).

Format: ``<kind>_<site>_<value>``

- ``kind``: a 3-letter prefix per entity type (``IdKind``).
- ``site``: ``SiteId``. It starts with ``dev`` (development side) or ``eval``
  (evaluation side) and separates the two sides' ID spaces.
- ``value``: 128 bits as exactly 32 lowercase hexadecimal digits. The upper
  48 bits are a millisecond timestamp and the lower 80 bits come from the OS
  cryptographic random source.

The hex form was chosen because the design does not fix one: it is fixed
width, has a single spelling (no case or alphabet ambiguity) and maps 1:1 to
the 128-bit value. IDs identify; they never order. Execution order comes from
``run_seq`` and ``tenant_seq`` (E_INTEGRATION 2.3), not from comparing IDs.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import PydanticCustomError, core_schema

from agent_core.types.errors import IdKindMismatchError, InvalidIdError

TIMESTAMP_BITS = 48
RANDOM_BITS = 80
RANDOM_BYTES = RANDOM_BITS // 8
MAX_TIMESTAMP_MS = (1 << TIMESTAMP_BITS) - 1
MAX_RANDOM_BITS_VALUE = (1 << RANDOM_BITS) - 1
# Generation refuses clocks earlier than 2024-01-01T00:00:00Z. Not in the design:
# it catches an unset or reset clock (for example 0) before it is encoded in IDs.
MIN_GENERATION_TIMESTAMP_MS = 1_704_067_200_000

_SITE_PATTERN = re.compile(r"(?P<role>dev|eval)[0-9a-z]{1,12}")
_ID_PATTERN = re.compile(r"(?P<kind>[a-z]{3})_(?P<site>[0-9a-z]+)_(?P<value>[0-9a-f]{32})")


class SiteRole(StrEnum):
    DEV = "dev"
    EVAL = "eval"


class IdKind(StrEnum):
    """ID prefixes used in Phase 1. Further kinds are added by the WP that needs them."""

    TASK = "tsk"
    RUN = "run"
    STEP = "stp"
    ATTEMPT = "att"
    EVENT = "evt"
    REQUEST = "req"
    PERMIT = "prm"
    RECEIPT = "rcp"
    RESERVATION = "rsv"
    TENANT = "tnt"
    RUN_GRANT = "grt"
    MODEL_REQUEST = "mrq"
    REPLAY_RECORD = "rpl"
    APPROVAL = "apv"
    USER = "usr"
    EXPERIMENT = "exp"
    PEP_INSTANCE = "pep"


def _check_int(value: object, maximum: int, what: str) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise InvalidIdError(f"{what} must be an int in [0, {maximum}]")
    return value


class SiteId(str):
    """Site part of an ID: ``dev`` or ``eval`` followed by 1-12 of ``[0-9a-z]``."""

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        if type(value) is not str and not isinstance(value, SiteId):
            raise InvalidIdError("site id must be a string")
        if _SITE_PATTERN.fullmatch(value) is None:
            raise InvalidIdError("malformed site id")
        return super().__new__(cls, value)

    @property
    def role(self) -> SiteRole:
        return SiteRole.EVAL if self.startswith(SiteRole.EVAL.value) else SiteRole.DEV

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )

    @classmethod
    def _validate(cls, value: object) -> SiteId:
        # Always re-check the content: an instance can be forged with str.__new__.
        if not isinstance(value, str):
            raise PydanticCustomError("id_type", "site id must be a string")
        try:
            return cls(value)
        except InvalidIdError as exc:
            raise PydanticCustomError("invalid_id", str(exc)) from exc


class EntityId(str):
    """Base class of typed IDs. Use a concrete subclass or ``parse_entity_id``."""

    __slots__ = ()
    KIND: ClassVar[IdKind]

    def __new__(cls, value: str) -> Self:
        if cls is EntityId:
            raise TypeError("use a concrete ID type or parse_entity_id()")
        if not isinstance(value, str):
            raise InvalidIdError("id must be a string")
        if isinstance(value, EntityId) and type(value) is not cls:
            raise IdKindMismatchError(f"expected a {cls.KIND.value} id, got {value.KIND.value}")
        match = _ID_PATTERN.fullmatch(value)
        if match is None:
            raise InvalidIdError("malformed id")
        if match.group("kind") != cls.KIND.value:
            if match.group("kind") in _KINDS:
                raise IdKindMismatchError(f"expected a {cls.KIND.value} id")
            raise InvalidIdError("unknown id kind")
        SiteId(match.group("site"))
        return super().__new__(cls, str(value))

    @classmethod
    def compose(cls, site: SiteId, timestamp_ms: int, random_bits: int) -> Self:
        """Build an ID from its parts. Range checks fail with ``InvalidIdError``."""
        _check_int(timestamp_ms, MAX_TIMESTAMP_MS, "timestamp_ms")
        _check_int(random_bits, MAX_RANDOM_BITS_VALUE, "random_bits")
        value = (timestamp_ms << RANDOM_BITS) | random_bits
        return cls(f"{cls.KIND.value}_{SiteId(site)}_{value:032x}")

    @property
    def kind(self) -> IdKind:
        return self.KIND

    @property
    def site(self) -> SiteId:
        return SiteId(self.split("_")[1])

    @property
    def _value(self) -> int:
        return int(self.split("_")[2], 16)

    @property
    def timestamp_ms(self) -> int:
        return self._value >> RANDOM_BITS

    @property
    def random_bits(self) -> int:
        return self._value & MAX_RANDOM_BITS_VALUE

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )

    @classmethod
    def _validate(cls, value: object) -> EntityId:
        # Always re-check the content: being typed is not proof of validity
        # (an instance can be forged with str.__new__).
        if not isinstance(value, str):
            raise PydanticCustomError("id_type", "id must be a string")
        try:
            return cls(value)
        except IdKindMismatchError as exc:
            raise PydanticCustomError("id_kind_mismatch", str(exc)) from exc
        except InvalidIdError as exc:
            raise PydanticCustomError("invalid_id", str(exc)) from exc


class TaskId(EntityId):
    __slots__ = ()
    KIND = IdKind.TASK


class RunId(EntityId):
    __slots__ = ()
    KIND = IdKind.RUN


class StepId(EntityId):
    __slots__ = ()
    KIND = IdKind.STEP


class AttemptId(EntityId):
    __slots__ = ()
    KIND = IdKind.ATTEMPT


class EventId(EntityId):
    __slots__ = ()
    KIND = IdKind.EVENT


class RequestId(EntityId):
    __slots__ = ()
    KIND = IdKind.REQUEST


class PermitId(EntityId):
    __slots__ = ()
    KIND = IdKind.PERMIT


class ReceiptId(EntityId):
    __slots__ = ()
    KIND = IdKind.RECEIPT


class ReservationId(EntityId):
    __slots__ = ()
    KIND = IdKind.RESERVATION


class TenantId(EntityId):
    __slots__ = ()
    KIND = IdKind.TENANT


class RunGrantId(EntityId):
    __slots__ = ()
    KIND = IdKind.RUN_GRANT


class ModelRequestId(EntityId):
    __slots__ = ()
    KIND = IdKind.MODEL_REQUEST


class ReplayRecordId(EntityId):
    __slots__ = ()
    KIND = IdKind.REPLAY_RECORD


class ApprovalId(EntityId):
    __slots__ = ()
    KIND = IdKind.APPROVAL


class UserId(EntityId):
    __slots__ = ()
    KIND = IdKind.USER


class ExperimentId(EntityId):
    __slots__ = ()
    KIND = IdKind.EXPERIMENT


class PepInstanceId(EntityId):
    __slots__ = ()
    KIND = IdKind.PEP_INSTANCE


_ID_TYPES: dict[str, type[EntityId]] = {
    t.KIND.value: t
    for t in (
        TaskId,
        RunId,
        StepId,
        AttemptId,
        EventId,
        RequestId,
        PermitId,
        ReceiptId,
        ReservationId,
        TenantId,
        RunGrantId,
        ModelRequestId,
        ReplayRecordId,
        ApprovalId,
        UserId,
        ExperimentId,
        PepInstanceId,
    )
}
_KINDS = frozenset(_ID_TYPES)
if {k.value for k in IdKind} != _KINDS:
    raise RuntimeError("every IdKind needs an ID type")


def parse_entity_id(value: str) -> EntityId:
    """Parse any known ID and return an instance of its typed class."""
    if not isinstance(value, str):
        raise InvalidIdError("id must be a string")
    match = _ID_PATTERN.fullmatch(value)
    if match is None:
        raise InvalidIdError("malformed id")
    id_type = _ID_TYPES.get(match.group("kind"))
    if id_type is None:
        raise InvalidIdError("unknown id kind")
    return id_type(value)
