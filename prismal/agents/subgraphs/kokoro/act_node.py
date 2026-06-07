"""act node — execute the verdict's action when enabled (SPEC-KOK-SG-001).

Pass-through unless ``settings.kokoro_execute_actions`` is on and the verdict
carries an action.  Delegates to :meth:`KokoroJudgeAgent.act`, which gates the
action behind the :class:`ActionInterceptor` and audits it hash-first
(DD-KOK-006); a denial surfaces as ``action.blocked_reason``, never an
exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.kokoro._helpers import get_kokoro, merge_kokoro
from prismal.core.exceptions import KokoroError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.kokoro.judge import KokoroJudgeAgent, Verdict

logger = get_logger("prismal.subgraphs.kokoro.act")


def make_act_node(
    judge_agent: KokoroJudgeAgent,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async node that (optionally) executes the verdict action.

    Args:
        judge_agent: The (injected) judge whose ``act`` owns the security gate.

    Returns:
        Async node updating ``verdict`` under ``state["metadata"]["kokoro"]``,
        or a pure pass-through when there is nothing to execute.
    """

    async def act_node(state: dict[str, Any]) -> dict[str, Any]:
        kokoro = get_kokoro(state)
        if kokoro.get("error"):
            return {}

        verdict: Verdict | None = kokoro.get("verdict")
        if verdict is None:
            return {}

        try:
            updated = await judge_agent.act(verdict)
        except KokoroError as exc:
            logger.warning("kokoro_act_node_error", error=str(exc))
            return merge_kokoro(state, error=str(exc))

        if updated is verdict:
            return {}  # action mode off or no action — pure pass-through
        return merge_kokoro(state, verdict=updated)

    return act_node


__all__ = ["make_act_node"]
