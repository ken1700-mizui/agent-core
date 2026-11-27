"""ModelPort (WP-05; E 7.4, ADR-0003): the provider-independent model interface.

Contract:

- ``complete`` takes a common ``ModelRequest`` and returns a common
  ``ModelResponse`` whose ``model_request_id`` is the request's.
- Every failure is a ``ModelError`` subclass with an explicit outcome
  (``NOT_SENT``, ``FAILED_DEFINITE``, ``UNKNOWN``). Adapters convert
  provider-specific errors; any other exception escaping an adapter breaks
  this contract and callers must treat it as ``UNKNOWN``.
- A ModelPort does not authorize, reserve budget, retry or fall back to another
  model. Those belong to the runtime: each new call is a new Attempt with its
  own authorization and reservation (E 7.4).
- A tool request in a response is data; it is never executed by the port.

Runtime and Context depend on this port, never on a concrete provider.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.types.model import ModelRequest, ModelResponse


@runtime_checkable
class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """One call. Raises a ``ModelError`` subclass on failure; never retries."""
        ...
