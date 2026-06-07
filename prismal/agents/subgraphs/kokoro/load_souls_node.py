"""load_souls node — resolve the triad before any LLM call (SPEC-KOK-SG-001).

Fail-fast stage: a missing/invalid soul surfaces here as an error state (no
crash, no LLM call), and every downstream node passes through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.kokoro._helpers import merge_kokoro
from prismal.core.exceptions import KokoroError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.souls.manager import SoulsManager

logger = get_logger("prismal.subgraphs.kokoro.load_souls")


def make_load_souls_node(
    souls_manager: SoulsManager,
    soul_ids: list[str] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async node that loads the soul triad into Kokoro state.

    Args:
        souls_manager: The manager used to resolve and validate the triad.
        soul_ids: Soul ids to convene; ``None`` uses ``settings.kokoro_souls``.

    Returns:
        Async node writing ``souls`` / ``soul_ids`` (or ``error``) under
        ``state["metadata"]["kokoro"]``.
    """

    async def load_souls_node(state: dict[str, Any]) -> dict[str, Any]:
        otel = OTelManager()
        with otel.start_span("kokoro.load_souls") as span:
            try:
                souls = souls_manager.load_triad(soul_ids)
            except KokoroError as exc:
                logger.warning("kokoro_load_souls_error", error=str(exc))
                span.set_attribute("prismal.kokoro.load_error", True)
                return merge_kokoro(state, error=str(exc))

            ids = [soul.metadata.name for soul in souls]
            span.set_attribute("prismal.kokoro.souls", ",".join(ids))
            logger.info("kokoro_souls_loaded", souls=ids)
            return merge_kokoro(state, souls=souls, soul_ids=ids)

    return load_souls_node


__all__ = ["make_load_souls_node"]
