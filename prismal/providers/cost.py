"""Per-call USD cost computation (Phase C — SPEC-CST-COST-001).

The single new module that imports ``litellm`` (Critical Rule #4: provider SDK
imports live only in ``prismal/providers/``). It prices a call from LiteLLM's
native model map first, then a configurable per-model fallback table in
``settings.budget_pricing``. Never raises — pricing failures degrade to a
zero-cost ``"none"`` estimate rather than breaking a turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import litellm

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class CostEstimate:
    """The USD cost of one call and where the figure came from."""

    cost_usd: float
    source: str  # "litellm" | "table" | "none"

    @property
    def estimated(self) -> bool:
        """True unless the figure is LiteLLM's authoritative price."""
        return self.source != "litellm"


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    settings: Settings | None = None,
) -> CostEstimate:
    """Return the USD :class:`CostEstimate` for one LLM call.

    1. ``litellm.cost_per_token`` (authoritative) → ``source="litellm"``.
    2. ``settings.budget_pricing[model]`` (per-1k input/output) → ``"table"``.
    3. ``CostEstimate(0.0, "none")``.
    """
    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        total = float(prompt_cost) + float(completion_cost)
        return CostEstimate(cost_usd=total, source="litellm")
    except Exception:
        logger.debug("litellm_cost_miss", model=model)

    table = getattr(settings, "budget_pricing", None) if settings is not None else None
    if isinstance(table, dict):
        entry = table.get(model)
        if isinstance(entry, dict):
            input_per_1k = float(entry.get("input", 0.0))
            output_per_1k = float(entry.get("output", 0.0))
            total = (prompt_tokens / 1000.0) * input_per_1k + (
                completion_tokens / 1000.0
            ) * output_per_1k
            return CostEstimate(cost_usd=total, source="table")

    return CostEstimate(cost_usd=0.0, source="none")
