"""Audit writer (WP-11; C2 4.5, C2.X25). Minimal; full enforcement is WP-09.

- Separate from the observation logger: its records are ``AuditRecord`` and
  its failures are fatal (``AuditWriteError``). An operation that needs an
  audit record must stop when the record cannot be written.
- It never calls the SAG or any other component; it only serializes the fixed
  record and hands the line to its sink. Re-entering it while it writes (for
  example from a hook in the sink) raises ``AuditRecursionError``.
"""

from __future__ import annotations

from agent_core.ports.log_sink import LogSink
from agent_core.types.errors import AuditRecursionError, AuditWriteError
from agent_core.types.observability import AuditRecord
from agent_core.types.serialization import dump_json

DEFAULT_MAX_AUDIT_RECORD_BYTES = 2_048
_MAX_SETTING = 1_048_576


class AuditWriter:
    def __init__(
        self, *, sink: LogSink, max_record_bytes: int = DEFAULT_MAX_AUDIT_RECORD_BYTES
    ) -> None:
        if type(max_record_bytes) is not int or not 0 < max_record_bytes <= _MAX_SETTING:
            raise AuditWriteError("max_record_bytes must be an int in [1, 1 MiB]")
        self._sink = sink
        self._max_record_bytes = max_record_bytes
        self._writing = False

    def write(self, record: AuditRecord) -> None:
        if self._writing:
            raise AuditRecursionError("the audit writer was re-entered")
        if type(record) is not AuditRecord:
            raise AuditWriteError("an AuditRecord is required")
        line = dump_json(record) + b"\n"
        if len(line) > self._max_record_bytes:
            raise AuditWriteError("audit record exceeds the size limit")
        self._writing = True
        try:
            self._sink.write(line)
        except AuditRecursionError:
            raise
        except Exception as exc:  # noqa: BLE001 (re-raised as a typed fatal error below)
            # Only the error type is kept: a sink message could carry anything.
            raise AuditWriteError(
                f"audit record could not be written ({type(exc).__name__})"
            ) from None
        finally:
            self._writing = False
