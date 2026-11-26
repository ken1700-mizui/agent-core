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


class AuditWriter:
    def __init__(self, *, sink: LogSink) -> None:
        self._sink = sink
        self._writing = False

    def write(self, record: AuditRecord) -> None:
        if self._writing:
            raise AuditRecursionError("the audit writer was re-entered")
        if type(record) is not AuditRecord:
            raise AuditWriteError("an AuditRecord is required")
        self._writing = True
        try:
            self._sink.write(dump_json(record) + b"\n")
        except AuditRecursionError:
            raise
        except Exception as exc:
            raise AuditWriteError("audit record could not be written") from exc
        finally:
            self._writing = False
