"""Formatter node for document_generation subgraph.

Turns the (edited) document into the requested output format and appends an
``AIMessage`` so the final artefact is visible in the conversation.

Supported formats:

- ``markdown`` (default): prepends a top-level title derived from the first
  outline entry (or topic) and passes through the edited text.
- ``plain``: strips any leading markdown heading markers.
- ``html``: minimal conversion (newlines → ``<br>``), suitable for simple
  display; not a full markdown→HTML renderer.

Falls back to ``draft`` if ``edited`` is missing (editor step was skipped).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import AIMessage

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.document_generation.formatter")

_VALID_FORMATS: frozenset[str] = frozenset({"markdown", "plain", "html"})


def make_formatter_node(
    format: Literal["markdown", "plain", "html"] = "markdown",
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that formats the final document."""
    if format not in _VALID_FORMATS:
        raise ValueError(f"format={format!r} not in {sorted(_VALID_FORMATS)}")

    async def formatter_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("document_generation") or {}
        body = existing.get("edited") or existing.get("draft") or ""
        if not body:
            return {}

        if format == "markdown":
            final = body  # pragma: no cover (straight-through)
        elif format == "plain":
            # Strip leading markdown headings.
            lines = [
                line.lstrip("# ").rstrip() if line.lstrip().startswith("#") else line
                for line in body.splitlines()
            ]
            final = "\n".join(lines)
        else:  # html
            final = "<br>\n".join(body.splitlines())

        logger.info("document_formatted", format=format, length=len(final))
        updated = {**existing, "final": final, "format": format}
        return {
            "messages": [AIMessage(content=final)],
            "metadata": {
                **(state.get("metadata") or {}),
                "document_generation": updated,
            },
        }

    return formatter_node


__all__ = ["make_formatter_node"]
