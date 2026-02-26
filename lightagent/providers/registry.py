"""Provider registry — LiteLLM-backed LLM factory with usage tracking.

Provides provider-agnostic LLM access via ChatLiteLLM. All provider
routing is handled by LiteLLM; no provider-specific imports here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelInfo:
    """Metadata for a configured LLM model."""

    id: str
    provider: str
    available: bool = True


@dataclass
class TokenUsage:
    """Cumulative token usage and cost for a session."""

    session_id: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0


__all__ = ["ModelInfo", "TokenUsage"]
