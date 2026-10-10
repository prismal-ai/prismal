"""Token extraction from LLM responses (Phase C — SPEC-CST-USG-001; TKE — SPEC-TKE-ACC-002).

Reads the LangChain-standard ``usage_metadata`` (and the OpenAI-style
``response_metadata['token_usage']`` fallback) off a message. These are
LangChain conventions, not provider SDK types, so this stays out of
``prismal/providers/`` per Critical Rule #4. Never raises — unknown shapes
yield zeros so metering can never break a turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from prismal.budget.types import TokenCounts

if TYPE_CHECKING:
    from prismal.providers.gateways.base import GatewayCallMeta


def extract_token_usage(message: object) -> TokenCounts:
    """Return the token counts carried by *message*.

    Prompt/completion precedence (unchanged since Phase C):
        1. ``message.usage_metadata`` → ``input_tokens`` / ``output_tokens``.
        2. ``message.response_metadata['token_usage']`` →
           ``prompt_tokens`` / ``completion_tokens``.
        3. ``TokenCounts(0, 0)``.

    Cache/reasoning fields (TKE) are read independently with the same
    precedence: LangChain ``input_token_details`` / ``output_token_details``
    first, then the provider shapes inside ``token_usage``, else zero.
    """
    meta = getattr(message, "usage_metadata", None)
    meta = meta if isinstance(meta, dict) else {}
    response_meta = getattr(message, "response_metadata", None)
    token_usage = response_meta.get("token_usage") if isinstance(response_meta, dict) else None
    token_usage = token_usage if isinstance(token_usage, dict) else {}

    prompt, completion = _as_int(meta.get("input_tokens")), _as_int(meta.get("output_tokens"))
    if not (prompt or completion):
        prompt = _as_int(token_usage.get("prompt_tokens"))
        completion = _as_int(token_usage.get("completion_tokens"))

    cache_read, cache_write, reasoning = _standard_details(meta)
    raw_read, raw_write, raw_reasoning = _provider_details(token_usage)
    return TokenCounts(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_read_tokens=cache_read or raw_read,
        cache_write_tokens=cache_write or raw_write,
        reasoning_tokens=reasoning or raw_reasoning,
    )


def extract_gateway_meta(message: object) -> GatewayCallMeta | None:
    """Gateway call metadata from ``response_metadata["prismal_gateway"]`` (SPEC-GW-COST-001).

    A pure dict read (the value object holds no provider SDK types). Never raises;
    ``None`` when the message did not come through a gateway.
    """
    try:
        metadata = getattr(message, "response_metadata", None)
        if not isinstance(metadata, dict):
            return None
        from prismal.providers.gateways.base import GatewayCallMeta

        return GatewayCallMeta.from_dict(metadata.get("prismal_gateway"))
    except Exception:
        return None


def _standard_details(meta: dict[str, object]) -> tuple[int, int, int]:
    """LangChain-standard ``input_token_details`` / ``output_token_details``."""
    inp = _as_dict(meta.get("input_token_details"))
    out = _as_dict(meta.get("output_token_details"))
    return (
        _as_int(inp.get("cache_read")),
        _as_int(inp.get("cache_creation")),
        _as_int(out.get("reasoning")),
    )


def _provider_details(token_usage: dict[str, object]) -> tuple[int, int, int]:
    """OpenAI / OpenRouter / Anthropic-raw shapes inside ``token_usage``."""
    prompt_details = _as_dict(token_usage.get("prompt_tokens_details"))
    completion_details = _as_dict(token_usage.get("completion_tokens_details"))
    cache_read = _as_int(prompt_details.get("cached_tokens")) or _as_int(
        token_usage.get("cache_read_input_tokens")
    )
    cache_write = (
        _as_int(token_usage.get("cache_write_tokens"))
        or _as_int(prompt_details.get("cache_write_tokens"))
        or _as_int(token_usage.get("cache_creation_input_tokens"))
    )
    reasoning = _as_int(completion_details.get("reasoning_tokens"))
    return cache_read, cache_write, reasoning


def _as_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _as_int(value: object) -> int:
    """Coerce a metadata value to a non-negative int, defaulting to 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (float, str)):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return 0
