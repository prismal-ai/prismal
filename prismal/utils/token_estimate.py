"""Provider-agnostic token estimate (Phase TKE — SPEC-TKE-OUT-001).

``ceil(len(text) / 4)`` — documented accuracy ±15 % for English prose and JSON
versus provider tokenizers. Used for budgets and thresholds only, never for
billing (billing uses provider-reported usage).
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Estimate the token count of *text* (``ceil(len / 4)``)."""
    return math.ceil(len(text) / 4)


__all__ = ["estimate_tokens"]
