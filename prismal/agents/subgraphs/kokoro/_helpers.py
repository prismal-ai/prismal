"""Shared helpers for the kokoro subgraph nodes.

All Kokoro runtime state lives under ``state["metadata"]["kokoro"]``
(RF-KOK-12) — the same namespacing pattern as the multimodal layer's
``metadata["mm"]`` — so the new layer stays isolated from the rest of
``AgentState``.
"""

from __future__ import annotations

from typing import Any

_NAMESPACE = "kokoro"


def get_kokoro(state: dict[str, Any]) -> dict[str, Any]:
    """Return the ``metadata.kokoro`` namespace of *state* (empty dict if absent)."""
    metadata = state.get("metadata") or {}
    kokoro = metadata.get(_NAMESPACE) or {}
    return dict(kokoro)


def merge_kokoro(state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """Return a state update merging *updates* into ``metadata.kokoro``."""
    return {
        "metadata": {
            **(state.get("metadata") or {}),
            _NAMESPACE: {**get_kokoro(state), **updates},
        }
    }


def last_query(state: dict[str, Any]) -> str:
    """Return the latest message content as the deliberation query ('' if none)."""
    messages = state.get("messages") or []
    if not messages:
        return ""
    return str(messages[-1].content)


__all__ = ["get_kokoro", "last_query", "merge_kokoro"]
