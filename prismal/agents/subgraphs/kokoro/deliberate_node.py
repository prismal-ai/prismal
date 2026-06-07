"""deliberate node — bounded multi-soul rounds (SPEC-KOK-SG-001).

Builds one :class:`SoulAgent` per loaded soul and runs
:func:`~prismal.agents.kokoro.deliberation.deliberate` (which owns the
``kokoro.deliberate`` OTel span).  A failure becomes an error state — the
graph never crashes (ARCHITECTURE §5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.kokoro.deliberation import deliberate
from prismal.agents.kokoro.soul_agent import SoulAgent
from prismal.agents.subgraphs.kokoro._helpers import get_kokoro, last_query, merge_kokoro
from prismal.core.exceptions import KokoroError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.kokoro.deliberation import AgreementFn
    from prismal.agents.kokoro.soul_agent import PersonaGenerateFn
    from prismal.core.config import Settings
    from prismal.souls.base import Soul

logger = get_logger("prismal.subgraphs.kokoro.deliberate")


def make_deliberate_node(
    *,
    generate_fn: PersonaGenerateFn | None = None,
    agreement_fn: AgreementFn | None = None,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async node that runs the soul deliberation.

    Args:
        generate_fn: Injected persona backend shared by the three souls;
            ``None`` lets each :class:`SoulAgent` wire its lazy default.
        agreement_fn: Injected agreement metric (default ``pairwise_jaccard``).
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.

    Returns:
        Async node writing ``deliberation`` (or ``error``) under
        ``state["metadata"]["kokoro"]``.
    """

    async def deliberate_node(state: dict[str, Any]) -> dict[str, Any]:
        kokoro = get_kokoro(state)
        if kokoro.get("error"):
            return {}

        souls: list[Soul] = list(kokoro.get("souls") or [])
        if not souls:
            return merge_kokoro(state, error="kokoro: no souls loaded before deliberation")

        query = last_query(state)
        if not query:
            return merge_kokoro(state, error="kokoro: no query message to deliberate on")

        agents = [SoulAgent(soul, generate_fn=generate_fn, settings=settings) for soul in souls]
        try:
            result = await deliberate(
                query,
                agents,
                agreement_fn=agreement_fn,
                settings=settings,
            )
        except KokoroError as exc:
            logger.warning("kokoro_deliberate_error", error=str(exc))
            return merge_kokoro(state, error=str(exc))

        return merge_kokoro(state, deliberation=result)

    return deliberate_node


__all__ = ["make_deliberate_node"]
