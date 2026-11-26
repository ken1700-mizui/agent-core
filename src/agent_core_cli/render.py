"""Output of the CLI: fixed JSON lines or human-readable text, never raw data.

Every value printed goes through ``clean``: control characters (C0, C1, DEL),
format characters (bidirectional overrides, zero-width characters), line and
paragraph separators and unassigned or private code points are written as
visible escapes, so nothing printed can act as a terminal control sequence.
Values are bounded in length. The CLI prints identifiers, states, digests,
numbers and fixed codes only: never a prompt, a file's content, a key or a free
exception message.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

SCHEMA: Final = "agent-core-cli/1"
MAX_TEXT: Final = 1_024
_UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def clean(value: object) -> str:
    """A printable, bounded form of ``value``."""
    text = str(value)
    out: list[str] = []
    for char in text:
        if unicodedata.category(char) in _UNSAFE_CATEGORIES:
            code = ord(char)
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(char)
        if sum(len(part) for part in out) > MAX_TEXT:
            break
    result = "".join(out)
    return result if len(result) <= MAX_TEXT else result[: MAX_TEXT - 3] + "..."


def _clean_tree(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        return {clean(k): _clean_tree(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean_tree(v) for v in value]
    return clean(value)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Printer:
    """One event per call: a JSON line (``--json``) or a text block."""

    def __init__(self, stream: TextIO, *, as_json: bool) -> None:
        self._stream = stream
        self.as_json = as_json

    def event(self, kind: str, **fields: Any) -> None:
        data = _clean_tree({"schema": SCHEMA, "kind": kind, **fields})
        if self.as_json:
            self._stream.write(json.dumps(data, sort_keys=True, ensure_ascii=True) + "\n")
        else:
            self._stream.write(_text(kind, data))
        self._stream.flush()

    def line(self, text: str) -> None:
        if not self.as_json:
            self._stream.write(clean(text) + "\n")
            self._stream.flush()


def _text(kind: str, data: dict[str, Any]) -> str:
    """A small, fixed text form of each event."""
    render = _TEXT.get(kind)
    if render is not None:
        return render(data)
    lines = [f"[{kind}]"]
    lines.extend(
        f"  {key}: {value}" for key, value in data.items() if key not in ("schema", "kind")
    )
    return "\n".join(lines) + "\n"


def _pairs(data: Mapping[str, Any], keys: tuple[str, ...], indent: str = "  ") -> list[str]:
    return [f"{indent}{key}: {_plain(data.get(key))}" for key in keys]


def _plain(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _outcome(data: dict[str, Any]) -> str:
    lines = [f"[outcome] run {data['run_id']}"]
    lines += _pairs(
        data,
        (
            "run_status",
            "reason",
            "model_calls",
            "tool_calls",
            "verification_reads",
            "repairs",
            "ledger_model_calls",
            "ledger_tool_calls",
            "error",
        ),
    )
    return "\n".join(lines) + "\n"


def _approval(data: dict[str, Any], *, header: bool = True) -> list[str]:
    lines = ["[approval required]"] if header else []
    lines += _pairs(
        data,
        (
            "run_id",
            "attempt_id",
            "operation",
            "tool_id",
            "tool_version",
            "manifest_hash",
            "artifact_hash",
            "effect_class",
        ),
    )
    for target in data.get("targets", []):
        lines.append(f"  target: {target['access']} {target['path']}")
    change = data.get("change")
    if change:
        lines.append(
            f"  change: {change['op']} {change['path']} before={_plain(change['before'])} "
            f"after={change['after']} size={change['size']}"
        )
    lines += _pairs(data, ("action_hash", "cost_cap_micro", "expires_at", "resumable"))
    lines.append(f"  cost unit: {data.get('unit', '-')}")
    lines.append(f"  to approve exactly this action: approve {data['action_hash']}")
    return lines


def _approval_request(data: dict[str, Any]) -> str:
    return "\n".join(_approval(data)) + "\n"


def _pending(data: dict[str, Any]) -> str:
    items = data.get("items", [])
    if not items:
        return "[pending] no action waits for approval\n"
    lines: list[str] = []
    for item in items:
        lines += _approval(item)
    return "\n".join(lines) + "\n"


def _status(data: dict[str, Any]) -> str:
    lines = ["[status]", f"  cost unit: {data['unit']}; real API cost: {data['real_api_cost']}"]
    if not data["tasks"]:
        lines.append("  no task")
    for task in data["tasks"]:
        lines.append(f"  task {task['task_id']}")
        lines.append(f"    task status: {task['status']}")
        for run in task["runs"]:
            lines.append(f"    run {run['run_id']}")
            lines += _pairs(
                run,
                ("spent_micro", "hold_micro", "ledger_model_calls", "ledger_tool_calls"),
                "      ",
            )
            lines.append(f"      run status: {run['status']}")
    return "\n".join(lines) + "\n"


def _steps(data: dict[str, Any]) -> str:
    lines = [f"[steps] run {data['run_id']}"]
    for step in data["steps"]:
        lines.append(f"  {step['index']}. {step['kind']} {step['status']}")
        for attempt in step["attempts"]:
            lines.append(
                f"     attempt {attempt['state']}"
                f" reservation={_plain(attempt['reservation_state'])}"
                f" spent={attempt['spent_micro']} hold={attempt['hold_micro']}"
                f" cost_final={_plain(attempt['cost_final'])}"
            )
    return "\n".join(lines) + "\n"


def _budget(data: dict[str, Any]) -> str:
    lines = [
        f"[budget] run {data['run_id']}",
        f"  cost unit: {data['unit']}; real API cost: {data['real_api_cost']}",
    ]
    for scope in data["scopes"]:
        lines.append(
            f"  {scope['scope']}: cap={_plain(scope['cap_micro'])} spent={scope['spent_micro']}"
            f" hold={scope['hold_micro']}"
        )
    if not data["unsettled"]:
        lines.append("  unsettled: none")
    for item in data["unsettled"]:
        lines.append(
            f"  unsettled: {item['step_kind']} attempt {item['attempt_id']}"
            f" reservation={item['reservation_state']} hold={item['hold_micro']}"
            f" cost_final={_plain(item['cost_final'])}"
        )
    return "\n".join(lines) + "\n"


def _stops(data: dict[str, Any]) -> str:
    items = data.get("items", [])
    if not items:
        return "[stops] none\n"
    lines = ["[stops]"]
    for item in items:
        lines.append(
            f"  {item['scope_type']} {item['scope_id']}"
            f" stop_seq={item['stop_seq']} {item['status']}"
        )
    return "\n".join(lines) + "\n"


def _error(data: dict[str, Any]) -> str:
    detail = data.get("detail")
    return f"error: {data['code']}" + (f" ({detail})" if detail else "") + "\n"


def _bye(data: dict[str, Any]) -> str:
    return f"bye (exit code {data['exit_code']})\n"


_TEXT: Final = {
    "outcome": _outcome,
    "approval_request": _approval_request,
    "pending": _pending,
    "status": _status,
    "steps": _steps,
    "budget": _budget,
    "stops": _stops,
    "error": _error,
    "bye": _bye,
}
