"""Typed model-call errors (WP-05, WP05-R01).

Every ModelPort adapter converts provider-specific failures into one of these.
Each error states its ``outcome``:

- ``NOT_SENT``: certainly nothing left this process. Zero tokens, known zero cost.
- ``FAILED_DEFINITE``: sent, but certainly not processed by the provider.
  Zero tokens; whether the refusal is billed is not known here (cost unknown).
- ``UNKNOWN``: may have been processed (and billed).

The usage attached to an error is independent of the outcome: an unknown outcome
may still carry billing data a provider reported separately. Zero tokens never
imply a known zero cost. Messages never include request or response text.
"""

from __future__ import annotations

from typing import ClassVar

from agent_core.types.errors import AgentCoreError
from agent_core.types.model import CostStatus, ModelCallOutcome, Usage, UsageStatus
from agent_core.types.values import Budget, Currency


def _no_tokens(*, cost_known_zero: bool, synthetic: bool) -> Usage:
    """Zero tokens. The cost is a known zero only when nothing left the process."""
    return Usage(
        status=UsageStatus.FINAL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost=Budget(currency=Currency.XTU, micro_units=0) if cost_known_zero else None,
        cost_status=CostStatus.FINAL if cost_known_zero else CostStatus.UNKNOWN,
        synthetic=synthetic,
    )


class ModelError(AgentCoreError):
    code: ClassVar[str] = "MODEL_CALL_FAILED"
    outcome: ClassVar[ModelCallOutcome] = ModelCallOutcome.UNKNOWN

    def __init__(self, message: str, *, usage: Usage) -> None:
        super().__init__(message)
        self.usage = usage


class _NotSentError(ModelError):
    outcome = ModelCallOutcome.NOT_SENT

    def __init__(self, message: str, *, synthetic: bool = True) -> None:
        super().__init__(message, usage=_no_tokens(cost_known_zero=True, synthetic=synthetic))


class ModelInvalidRequestError(_NotSentError):
    """The request does not fit the adapter's profile. Nothing was sent."""

    code = "MODEL_INVALID_REQUEST"


class ModelUnsupportedError(_NotSentError):
    """The provider, profile or content type is not supported. Nothing was sent."""

    code = "MODEL_UNSUPPORTED"


class ModelNotSentError(_NotSentError):
    """Certainly nothing was sent (for example the connection was refused)."""

    code = "MODEL_NOT_SENT"


class FakeScriptMissError(_NotSentError):
    """The Fake Provider has no scripted outcome for this input. It never guesses."""

    code = "FAKE_SCRIPT_MISS"


class ModelDefiniteFailureError(ModelError):
    """Sent, but certainly not processed by the provider (for example refused).

    No tokens were used, but whether such a refusal is billed is not known here,
    so the cost stays unknown until reconciled (WP-09)."""

    code = "MODEL_FAILED_DEFINITE"
    outcome = ModelCallOutcome.FAILED_DEFINITE

    def __init__(self, message: str, *, synthetic: bool = True) -> None:
        super().__init__(message, usage=_no_tokens(cost_known_zero=False, synthetic=synthetic))


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
