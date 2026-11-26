"""Serialization and restore.

- ``dump_json``: canonical JSON bytes: UTF-8, keys sorted, no whitespace,
  NaN/Infinity refused. Collections are already in canonical order (records
  validate it), so equal records give equal bytes.
- ``load_json``: strict restore from JSON. Any failure, including malformed
  JSON, raises ``RecordValidationError`` listing locations and error types
  only, never input values.

Unknown event ``schema_version`` values are rejected by the Event model.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from agent_core.types.errors import RecordValidationError, ValidationIssue


def dump_json(record: BaseModel) -> bytes:
    data = record.model_dump(mode="json")
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def load_json[M: BaseModel](model_type: type[M], data: bytes | str) -> M:
    try:
        return model_type.model_validate_json(data)
    except ValidationError as exc:
        issues = tuple(
            ValidationIssue(location=".".join(str(p) for p in e["loc"]) or "$", type=e["type"])
            for e in exc.errors(include_input=False, include_url=False)
        )
        raise RecordValidationError(model_type.__name__, issues) from exc
