"""Token extraction from LLM responses (Phase C — SPEC-CST-USG-001).

Reads the LangChain-standard ``usage_metadata`` (and the OpenAI-style
``response_metadata['token_usage']`` fallback) off a message. These are
LangChain conventions, not provider SDK types, so this stays out of
``prismal/providers/`` per Critical Rule #4. Never raises — unknown shapes
yield zeros so metering can never break a turn.
"""

from __future__ import annotations

from prismal.budget.types import TokenCounts


def extract_token_usage(message: object) -> TokenCounts:
    """Return the prompt/completion token counts carried by *message*.

    Precedence:
        1. ``message.usage_metadata`` → ``input_tokens`` / ``output_tokens``.
        2. ``message.response_metadata['token_usage']`` →
           ``prompt_tokens`` / ``completion_tokens``.
        3. ``TokenCounts(0, 0)``.
    """
    meta = getattr(message, "usage_metadata", None)
    if isinstance(meta, dict):
        prompt = _as_int(meta.get("input_tokens"))
        completion = _as_int(meta.get("output_tokens"))
        if prompt or completion:
            return TokenCounts(prompt_tokens=prompt, completion_tokens=completion)

    response_meta = getattr(message, "response_metadata", None)
    if isinstance(response_meta, dict):
        token_usage = response_meta.get("token_usage")
        if isinstance(token_usage, dict):
            prompt = _as_int(token_usage.get("prompt_tokens"))
            completion = _as_int(token_usage.get("completion_tokens"))
            if prompt or completion:
                return TokenCounts(prompt_tokens=prompt, completion_tokens=completion)

    return TokenCounts(0, 0)


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
