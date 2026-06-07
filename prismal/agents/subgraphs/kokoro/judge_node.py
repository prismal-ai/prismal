"""judge node — render the Verdict from the deliberation (SPEC-KOK-SG-001).

Delegates to :meth:`KokoroJudgeAgent.judge` (which owns the ``kokoro.judge``
OTel span and the hash-first audit record).  A failure becomes an error state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.kokoro._helpers import get_kokoro, last_query, merge_kokoro
from prismal.core.exceptions import KokoroError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.kokoro.deliberation import DeliberationResult
    from prismal.agents.kokoro.judge import KokoroJudgeAgent

logger = get_logger("prismal.subgraphs.kokoro.judge")


def make_judge_node(
    judge_agent: KokoroJudgeAgent,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async node that renders the judge's verdict.

    Args:
        judge_agent: The (injected) judge.

    Returns:
        Async node writing ``verdict`` (or ``error``) under
        ``state["metadata"]["kokoro"]``.
    """

    async def judge_node(state: dict[str, Any]) -> dict[str, Any]:
        kokoro = get_kokoro(state)
        if kokoro.get("error"):
            return {}

        deliberation: DeliberationResult | None = kokoro.get("deliberation")
        if deliberation is None:
            return merge_kokoro(state, error="kokoro: no deliberation result to judge")

        try:
            verdict = await judge_agent.judge(last_query(state), deliberation)
        except KokoroError as exc:
            logger.warning("kokoro_judge_node_error", error=str(exc))
            return merge_kokoro(state, error=str(exc))

        return merge_kokoro(state, verdict=verdict)

    return judge_node


__all__ = ["make_judge_node"]
