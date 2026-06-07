"""Kokoro deliberation agents (Fase K).

Two new agent types built on callable injection (DD-KOK-004):

* :class:`~prismal.agents.kokoro.soul_agent.SoulAgent` — a persona sub-agent
  whose personality is injected from a Markdown soul (``SOUL.md``).
* :class:`~prismal.agents.kokoro.judge.KokoroJudgeAgent` — the orchestrator
  and final judge ("the whole"): renders a :class:`Verdict` and optionally
  executes one gated action.

Quick start::

    from prismal.agents.kokoro import SoulAgent
    from prismal.souls import SoulsManager

    spirit = SoulAgent(SoulsManager().load("spirit"))
    position = await spirit.position("Should we ship this refactor now?")
"""

from __future__ import annotations

from prismal.agents.kokoro.deliberation import AgreementFn, DeliberationResult, deliberate
from prismal.agents.kokoro.judge import (
    JudgeFn,
    KokoroAction,
    KokoroJudgeAgent,
    ToolExecutor,
    Verdict,
)
from prismal.agents.kokoro.soul_agent import PersonaGenerateFn, SoulAgent

__all__ = [
    "AgreementFn",
    "DeliberationResult",
    "JudgeFn",
    "KokoroAction",
    "KokoroJudgeAgent",
    "PersonaGenerateFn",
    "SoulAgent",
    "ToolExecutor",
    "Verdict",
    "deliberate",
]
