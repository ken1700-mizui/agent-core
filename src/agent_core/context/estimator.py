"""The synthetic token estimator (WP-14; B1 4.1).

Phase 1 has no real tokenizer and must not call one (no external measurement).
The estimate is deterministic integer arithmetic over UTF-8 bytes: one token per
three bytes, rounded up, plus one per text. It is recorded as an estimate
(``exact=False``) with this estimator's id and version; it is not a measured
count of any real model, and its error against real tokenizers is unknown.
"""

from __future__ import annotations

from typing import Final

from agent_core.types.context import EstimatorRef

SYNTHETIC_ESTIMATOR: Final = EstimatorRef(estimator_id="synthetic_utf8_bytes", version="1.0.0")


def estimate_text(text: str) -> int:
    """Tokens of ``text`` (already checked to be valid UTF-8 text)."""
    size = len(text.encode("utf-8"))
    return -(-size // 3) + 1
