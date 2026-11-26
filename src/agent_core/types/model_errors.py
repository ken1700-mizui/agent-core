"""Typed model-call errors (WP-05).

Every ModelPort adapter converts provider-specific failures into one of these.
Each error states its ``outcome`` (``NOT_SENT``, ``FAILED_DEFINITE`` or
``UNKNOWN``) and the usage known at that point. An unknown outcome carries
unknown usage, never zero. Messages never include request or response text.
"""

from __future__ import annotations

from typing import ClassVar

from agent_core.types.errors import AgentCoreError
from agent_core.types.model import ModelCallOutcome, Usage, UsageStatus


def _known_zero(*, synthetic: bool) -> Usage:
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=None,
        synthetic=synthetic,
    )


class ModelError(AgentCoreError):
    code: ClassVar[str] = "MODEL_CALL_FAILED"
    outcome: ClassVar[ModelCallOutcome] = ModelCallOutcome.UNKNOWN

    def __init__(self, message: str, *, usage: Usage) -> None:
        super().__init__(message)
        if self.outcome is ModelCallOutcome.UNKNOWN and usage.status is UsageStatus.FINAL:
            raise ValueError("an unknown outcome cannot carry final usage")
        self.usage = usage


class _NotSentError(ModelError):
    outcome = ModelCallOutcome.NOT_SENT

    def __init__(self, message: str, *, synthetic: bool = True) -> None:
        super().__init__(message, usage=_known_zero(synthetic=synthetic))


class ModelInvalidRequestError(_NotSentError):
    """The request does not fit the adapter's profile. Nothing was sent."""

    code = "MODEL_INVALID_REQUEST"


class ModelUnsupportedError(_NotSentError):
    """The provider, profile or content type is not supported. Nothing was sent."""

    code = "MODEL_UNSUPPORTED"


class FakeScriptMissError(_NotSentError):
    """The Fake Provider has no scripted outcome for this input. It never guesses."""

    code = "FAKE_SCRIPT_MISS"


class ModelDefiniteFailureError(ModelError):
    """Certainly not processed by the provider (refused before any content was used)."""

    code = "MODEL_FAILED_DEFINITE"
    outcome = ModelCallOutcome.FAILED_DEFINITE

    def __init__(self, message: str, *, synthetic: bool = True) -> None:
        super().__init__(message, usage=_known_zero(synthetic=synthetic))


class ModelOutcomeUnknownError(ModelError):
    """The request may have been processed (and billed): for example a broken stream."""

    code = "MODEL_OUTCOME_UNKNOWN"
    outcome = ModelCallOutcome.UNKNOWN


class ModelTimeoutError(ModelOutcomeUnknownError):
    """No complete response within the timeout. The outcome is unknown."""

    code = "MODEL_TIMEOUT"


class ModelResponseInvalidError(ModelOutcomeUnknownError):
    """The provider answered with data that fails validation. Treated as unknown."""

    code = "MODEL_RESPONSE_INVALID"
