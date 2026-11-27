"""Canonical JSON serialization and typed restore errors."""

from __future__ import annotations

import json
import traceback
from typing import Any

import pytest

from agent_core.types.errors import RecordValidationError
from agent_core.types.events import Event
from agent_core.types.ids import RunId
from agent_core.types.records import Attempt, EngineRequest, Run, Step, Task
from agent_core.types.serialization import dump_json, load_json
from agent_core.types.values import Budget, Currency, sha256_digest
from support import builders as b


@pytest.mark.parametrize(
    "builder",
    [b.task, b.run, b.step, b.attempt, b.consumed_attempt, b.event, b.engine_request],
)
def test_round_trip_is_lossless_and_byte_stable(builder: Any) -> None:
    record = builder()
    data = dump_json(record)
    restored = load_json(type(record), data)
    assert restored == record
    assert dump_json(restored) == data


def test_canonical_json_is_compact_with_sorted_keys() -> None:
    data = dump_json(b.engine_request(estimated_cost=Budget(currency=Currency.XTU, micro_units=7)))
    decoded = json.loads(data)
    assert data == json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
    assert decoded["estimated_cost"] == {"currency": "XTU", "micro_units": 7}


def test_restored_ids_are_typed() -> None:
    record = b.run()
    restored = load_json(Run, dump_json(record))
    assert type(restored.run_id) is RunId


@pytest.mark.parametrize(
    ("record_type", "mutate", "issue_type"),
    [
        (Task, lambda d: d.pop("status"), "missing"),
        (Task, lambda d: d.update(status="DONE"), "enum"),
        (Step, lambda d: d.update(run_seq=0), "greater_than_equal"),
        (Step, lambda d: d.update(run_seq="1"), "int_type"),
        (Run, lambda d: d.update(extra=1), "extra_forbidden"),
        (Event, lambda d: d.update(schema_version=4), "unsupported_schema_version"),
        (Event, lambda d: d.update(schema_version=3), "payload_required"),
        (Event, lambda d: d.update(schema_version=2), "superseded_schema_version"),
        (Attempt, lambda d: d.update(attempt_id="att_dev01_" + "0" * 31), "invalid_id"),
    ],
)
def test_invalid_input_raises_a_typed_error_with_issue_types(
    record_type: Any, mutate: Any, issue_type: str
) -> None:
    builder = {
        Task: b.task,
        Step: b.step,
        Run: b.run,
        Event: b.event,
        Attempt: b.attempt,
    }[record_type]
    data = json.loads(dump_json(builder()))
    mutate(data)
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(record_type, json.dumps(data))
    error = excinfo.value
    assert error.record_type == record_type.__name__
    assert issue_type in {issue.type for issue in error.issues}


def test_validation_errors_do_not_echo_input_values() -> None:
    marker = "synthetic-secret-marker-value"
    data = json.loads(dump_json(b.engine_request()))
    data["kind"] = marker
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(EngineRequest, json.dumps(data))
    assert marker not in str(excinfo.value)
    assert marker not in repr(excinfo.value.issues)


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b'{"a": NaN}', b"\xff"])
def test_malformed_json_raises_a_typed_error(raw: bytes) -> None:
    with pytest.raises(RecordValidationError):
        load_json(Task, raw)


def test_sha256_digest_format() -> None:
    assert sha256_digest(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


# ---------------------------------------------------------------- WP02-R01: JSON input rules


def _task_json() -> str:
    return dump_json(b.task()).decode()


def test_duplicate_top_level_key_is_rejected_even_if_it_would_change_meaning() -> None:
    raw = _task_json()
    # Appending a second "status" would silently win in a last-key-wins parser.
    tampered = raw[:-1] + ',"status":"COMPLETED"}'
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, tampered)
    assert {i.type for i in excinfo.value.issues} == {"json_duplicate_key"}


def test_duplicate_nested_key_is_rejected() -> None:
    raw = _task_json()
    tampered = raw.replace('"micro_units":5000000', '"micro_units":5000000,"micro_units":1')
    assert tampered != raw
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, tampered)
    assert {i.type for i in excinfo.value.issues} == {"json_duplicate_key"}


@pytest.mark.parametrize(
    ("replacement", "issue"),
    [
        ('"micro_units":NaN', "json_non_finite_number"),
        ('"micro_units":Infinity', "json_non_finite_number"),
        ('"micro_units":-Infinity', "json_non_finite_number"),
        ('"micro_units":1e400', "json_non_finite_number"),
        ('"micro_units":"\\ud800"', "json_invalid_string"),
    ],
)
def test_non_finite_numbers_and_invalid_strings_are_rejected(replacement: str, issue: str) -> None:
    tampered = _task_json().replace('"micro_units":5000000', replacement)
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, tampered)
    assert {i.type for i in excinfo.value.issues} == {issue}


@pytest.mark.parametrize(
    "raw",
    [b"\xef\xbb\xbf{}", b'{"a":"\xff"}', b'{"a":1}\n{"b":2}', b'{"a":"line\nbreak"}'],
    ids=["bom", "invalid-utf8", "trailing-data", "raw-control-char"],
)
def test_malformed_encodings_are_rejected(raw: bytes) -> None:
    with pytest.raises(RecordValidationError):
        load_json(Task, raw)


# ---------------------------------------------------------------- WP02-R02: no input leakage


MARKER = "synthetic-secret-" + "Zx9" * 6


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_key_names_are_not_echoed(nested: bool) -> None:
    data = json.loads(_task_json())
    target = data["budget_total"] if nested else data
    target[MARKER] = MARKER
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, json.dumps(data))
    error = excinfo.value
    assert "extra_forbidden" in {i.type for i in error.issues}
    rendered = "".join(traceback.format_exception(error))
    for text in (str(error), repr(error.issues), rendered):
        assert MARKER not in text
    assert error.__cause__ is None
    assert error.__suppress_context__


def test_known_locations_are_kept_for_diagnosis() -> None:
    data = json.loads(_task_json())
    data["budget_total"]["micro_units"] = -1
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, json.dumps(data))
    assert ("budget_total.micro_units", "greater_than_equal") in {
        (i.location, i.type) for i in excinfo.value.issues
    }


def test_duplicate_key_name_is_not_echoed() -> None:
    raw = _task_json()[:-1] + f',"{MARKER}":1,"{MARKER}":2}}'
    with pytest.raises(RecordValidationError) as excinfo:
        load_json(Task, raw)
    assert MARKER not in "".join(traceback.format_exception(excinfo.value))
