"""Kokoro deliberation subgraph (SPEC-KOK-SG-001).

``load_souls → deliberate → judge → act → output`` — three Markdown-authored
souls deliberate toward agreement and a single judge renders (and optionally
executes) the final decision.

Quick start::

    from prismal.agents.subgraphs.kokoro import build_kokoro_subgraph, register_kokoro

    definition = build_kokoro_subgraph()
    await register_kokoro()  # idempotent
"""

from __future__ import annotations

from prismal.agents.subgraphs.kokoro.builder import build_kokoro_subgraph, register_kokoro

__all__ = [
    "build_kokoro_subgraph",
    "register_kokoro",
]
