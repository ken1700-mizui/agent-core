"""Serialization and restore (Phase 1 internal storage format).

``dump_json`` writes canonical JSON bytes: UTF-8, keys sorted, no whitespace,
NaN/Infinity refused. Collections are already in canonical order (records
validate it), so equal records give equal bytes. This is a Phase 1 internal
format produced by Python's ``json`` module; conformance to RFC 8785 (JCS) has
not been verified and is not claimed.

``load_json`` restores strictly, in two passes:

1. A pre-check with the standard ``json`` parser rejects input that a
   last-key-wins parser would silently reinterpret: duplicate keys at any
   depth, ``NaN``/``Infinity``/overflowing numbers, lone surrogates, invalid
   UTF-8, a byte-order mark, trailing data and raw control characters.
2. Pydantic validates the same bytes in strict JSON mode.

Any failure raises ``RecordValidationError``. Its issues hold locations made
only of declared field names and indexes (anything else, such as an unknown
key supplied in the input, is replaced by ``<unknown>``) and error types,
never input values. The original exception is not chained, so tracebacks do
not carry input either.
"""

from __future__ import annotations

import json
import math
import typing
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_core.types.errors import RecordValidationError, ValidationIssue

UNKNOWN_LOCATION = "<unknown>"
# Deeper nesting is rejected (records and tool arguments never need more).
MAX_JSON_DEPTH = 64


def dump_json(record: BaseModel) -> bytes:
    data = record.model_dump(mode="json")
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class StrictJsonError(ValueError):
    """Input rejected by the strict JSON rules. The message is only the issue type."""

    def __init__(self, issue_type: str) -> None:
        super().__init__(issue_type)
        self.issue_type = issue_type


_InputRejectedError = StrictJsonError


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InputRejectedError("json_duplicate_key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> Any:
    raise _InputRejectedError("json_non_finite_number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _InputRejectedError("json_non_finite_number")
    return value


def _check_strings(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise _InputRejectedError("json_too_deep")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise _InputRejectedError("json_invalid_string") from None
        elif isinstance(item, dict):
            stack.extend((k, depth + 1) for k in item)
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def parse_strict_json(data: bytes | str) -> Any:
    """Parse JSON under the strict rules of ``load_json`` (duplicate keys, non-finite
    numbers, invalid strings or encodings, nesting deeper than ``MAX_JSON_DEPTH``).
    Raises ``StrictJsonError`` whose message is only the issue type."""
    return _precheck(data)


def _precheck(data: bytes | str) -> Any:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _InputRejectedError("json_invalid_encoding") from None
    else:
        text = data
    if text.startswith("﻿"):
        raise _InputRejectedError("json_invalid_encoding")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except RecursionError:
        raise _InputRejectedError("json_too_deep") from None
    except _InputRejectedError:
        raise
    except ValueError:
        raise _InputRejectedError("json_invalid") from None
    _check_strings(parsed)
    return parsed


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for arg in typing.get_args(annotation):
        found.extend(_model_types(arg))
    return found


def _known_field_names(model_type: type[BaseModel]) -> frozenset[str]:
    names: set[str] = set()
    pending: list[type[BaseModel]] = [model_type]
    seen: set[type[BaseModel]] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for name, field in current.model_fields.items():
            names.add(name)
            pending.extend(_model_types(field.annotation))
    return frozenset(names)


def _safe_location(loc: tuple[int | str, ...], known: frozenset[str]) -> str:
    parts = [str(p) if isinstance(p, int) or p in known else UNKNOWN_LOCATION for p in loc]
    return ".".join(parts) or "$"


def load_json[M: BaseModel](model_type: type[M], data: bytes | str) -> M:
    try:
        _precheck(data)
    except _InputRejectedError as exc:
        issue = ValidationIssue(location="$", type=exc.issue_type)
        raise RecordValidationError(model_type.__name__, (issue,)) from None
    try:
        return model_type.model_validate_json(data)
    except ValidationError as exc:
        known = _known_field_names(model_type)
        issues = tuple(
            ValidationIssue(location=_safe_location(e["loc"], known), type=e["type"])
            for e in exc.errors(include_input=False, include_url=False, include_context=False)
        )
    raise RecordValidationError(model_type.__name__, issues) from None
