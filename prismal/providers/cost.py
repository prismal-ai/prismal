"""Per-call USD cost computation (Phase C — SPEC-CST-COST-001; TKE — SPEC-TKE-ACC-003).

The single new module that imports ``litellm`` (Critical Rule #4: provider SDK
imports live only in ``prismal/providers/``). Precedence (ADR-WAVE-001 §2.3):
a gateway-reported billed figure, then LiteLLM's native model map (cache-aware),
then a configurable per-model fallback table in ``settings.budget_pricing``.
Never raises — pricing failures degrade to a zero-cost ``"none"`` estimate
rather than breaking a turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import litellm
from litellm.types.utils import ModelResponse

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger(__name__)

_AUTHORITATIVE_SOURCES = frozenset({"gateway", "litellm", "subscription"})


@dataclass(frozen=True)
class CostEstimate:
    """The USD cost of one call and where the figure came from."""

    cost_usd: float
    source: str  # "subscription" | "gateway" | "litellm" | "table" | "none"
    equivalent_usd: float = 0.0  # Phase GW: list-price equivalent of a subscription call

    @property
    def estimated(self) -> bool:
        """True unless the figure is gateway-billed or LiteLLM's authoritative price."""
        return self.source not in _AUTHORITATIVE_SOURCES


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    settings: Settings | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reported_cost_usd: float | None = None,
    response: object | None = None,
    cost_mode: Literal["metered", "subscription"] = "metered",
) -> CostEstimate:
    """Return the USD :class:`CostEstimate` for one LLM call.

    ``prompt_tokens`` is the total input *including* cached tokens.

    0. ``cost_mode="subscription"`` (flat-fee gateway plan, Phase GW) →
       ``CostEstimate(0.0, "subscription", equivalent_usd=…)`` where the
       equivalent is ``reported_cost_usd`` if given, else the steps 2-3 price.
    1. ``reported_cost_usd`` (gateway-billed) → ``source="gateway"``.
    2. ``litellm.completion_cost(response)`` for a raw ``ModelResponse``, else
       ``litellm.cost_per_token`` with cache kwargs → ``"litellm"``.
    3. ``settings.budget_pricing[model]`` (per-1k input/output/cache_read/
       cache_write; cache rates fall back to ``input``) → ``"table"``.
    4. ``CostEstimate(0.0, "none")``.
    """
    if cost_mode == "subscription":
        if reported_cost_usd is not None:
            equivalent = float(reported_cost_usd)
        else:
            equivalent = _priced(
                model,
                prompt_tokens,
                completion_tokens,
                settings,
                cache_read_tokens,
                cache_write_tokens,
                response,
            ).cost_usd
        return CostEstimate(cost_usd=0.0, source="subscription", equivalent_usd=equivalent)

    if reported_cost_usd is not None:
        return CostEstimate(cost_usd=float(reported_cost_usd), source="gateway")

    return _priced(
        model,
        prompt_tokens,
        completion_tokens,
        settings,
        cache_read_tokens,
        cache_write_tokens,
        response,
    )


def _priced(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    settings: Settings | None,
    cache_read_tokens: int,
    cache_write_tokens: int,
    response: object | None,
) -> CostEstimate:
    """Steps 2-4 of the precedence: litellm → table → none."""
    if isinstance(response, ModelResponse):
        try:
            total = float(litellm.completion_cost(completion_response=response))
            return CostEstimate(cost_usd=total, source="litellm")
        except Exception:
            logger.debug("litellm_completion_cost_miss", model=model)

    try:
        cache_kwargs: dict[str, int] = {}
        if cache_read_tokens:
            cache_kwargs["cache_read_input_tokens"] = cache_read_tokens
        if cache_write_tokens:
            cache_kwargs["cache_creation_input_tokens"] = cache_write_tokens
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **cache_kwargs,
        )
        total = float(prompt_cost) + float(completion_cost)
        return CostEstimate(cost_usd=total, source="litellm")
    except Exception:
        logger.debug("litellm_cost_miss", model=model)

    table = getattr(settings, "budget_pricing", None) if settings is not None else None
    if isinstance(table, dict):
        entry = table.get(model)
        if isinstance(entry, dict):
            return CostEstimate(
                cost_usd=_table_cost(
                    entry, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens
                ),
                source="table",
            )

    return CostEstimate(cost_usd=0.0, source="none")


def _table_cost(
    entry: dict[str, float],
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    input_per_1k = float(entry.get("input", 0.0))
    output_per_1k = float(entry.get("output", 0.0))
    if not (cache_read_tokens or cache_write_tokens):
        # v3.12.0 expression, kept verbatim so default results are bit-identical.
        return (prompt_tokens / 1000.0) * input_per_1k + (
            completion_tokens / 1000.0
        ) * output_per_1k
    read_per_1k = float(entry.get("cache_read", input_per_1k))
    write_per_1k = float(entry.get("cache_write", input_per_1k))
    uncached = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
    return (
        uncached * input_per_1k
        + cache_read_tokens * read_per_1k
        + cache_write_tokens * write_per_1k
        + completion_tokens * output_per_1k
    ) / 1000.0
