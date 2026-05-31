"""Graph visualization helpers (V1).

Render any graph-based prismal architecture — the compiled supervisor graph or
any :class:`~prismal.agents.subgraphs.registry.SubgraphDefinition` (dev, ml,
financial, customer_service, document_generation, data_etl, code_review,
debate_consensus, multimodal_pipeline, …) and builds from
:class:`~prismal.agents.extension.builder.PrismalStateGraphBuilder` — to Mermaid
text or a PNG image.

``to_mermaid`` is offline and always works. ``to_mermaid_png`` needs the
LangGraph renderer (mermaid.ink network call or a local engine); ``visualize``
wraps it with a graceful fallback to the Mermaid text so it never hard-fails.

Reasoning patterns (ToT, debate, LATS, …) and modal agents are plain classes
with no LangGraph topology, so they are not graph-visualizable — passing one
raises ``TypeError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from langgraph.graph.state import CompiledStateGraph

logger = get_logger("prismal.agents.visualization")


def _as_compiled(obj: Any) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Coerce a graph-like object into a ``CompiledStateGraph``.

    Accepts a ``CompiledStateGraph`` (returned as-is), a ``SubgraphDefinition``
    (assembled and compiled without a checkpointer), or a
    ``PrismalStateGraphBuilder`` (compiled to a definition first).

    Raises:
        TypeError: If *obj* is not a graph-based architecture.
    """
    # Already a compiled LangGraph (the main supervisor graph or compile_raw()).
    if hasattr(obj, "get_graph") and callable(obj.get_graph):
        return cast("CompiledStateGraph[Any, Any, Any, Any]", obj)

    from prismal.agents.extension.builder import PrismalStateGraphBuilder
    from prismal.agents.subgraphs.registry import SubgraphDefinition

    # A fluent builder → compile to a SubgraphDefinition, then fall through.
    if isinstance(obj, PrismalStateGraphBuilder):
        obj = obj.compile()

    if isinstance(obj, SubgraphDefinition):
        from prismal.agents.subgraphs.factory import assemble_state_graph

        # Compile without a checkpointer — visualization needs only the topology.
        return assemble_state_graph(obj).compile()

    raise TypeError(
        f"cannot visualize {type(obj).__name__!r}: expected a CompiledStateGraph, "
        "SubgraphDefinition, or PrismalStateGraphBuilder (reasoning patterns and "
        "modal agents are not graphs)"
    )


def to_mermaid(obj: Any) -> str:
    """Return the Mermaid source for a graph-based architecture (offline).

    Args:
        obj: A ``CompiledStateGraph``, ``SubgraphDefinition`` or
            ``PrismalStateGraphBuilder``.

    Returns:
        Mermaid diagram text.

    Raises:
        TypeError: If *obj* is not graph-based.
    """
    return str(_as_compiled(obj).get_graph().draw_mermaid())


def to_mermaid_png(obj: Any) -> bytes:
    """Return a PNG rendering of the graph.

    Requires the LangGraph Mermaid renderer (a mermaid.ink network call or a
    local engine). Use :func:`visualize` for a fallback-safe variant.

    Raises:
        TypeError: If *obj* is not graph-based.
    """
    png: bytes = _as_compiled(obj).get_graph().draw_mermaid_png()
    return png


def save_graph_image(obj: Any, path: str | Path) -> None:
    """Render *obj* to PNG and write it to *path* (for CI / non-notebook use)."""
    from pathlib import Path as _Path

    target = _Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(to_mermaid_png(obj))
    logger.info("graph_image_saved", path=str(target))


def visualize(obj: Any) -> None:
    """Display the graph as a PNG in a notebook, falling back to Mermaid text.

    Mirrors the canonical snippet: tries an inline PNG via ``IPython.display``
    and, if anything goes wrong (no IPython, no renderer, no network), prints
    the Mermaid source instead. Never raises for a valid graph; raises
    ``TypeError`` only when *obj* is not graph-based.
    """
    graph = _as_compiled(obj).get_graph()
    try:
        from IPython.display import Image, display

        display(Image(graph.draw_mermaid_png()))
        print("✅ Grafo visualizado")
    except Exception as exc:
        print(f"Visualización no disponible: {exc}")
        print("Mermaid del grafo:")
        print(graph.draw_mermaid())


__all__ = [
    "save_graph_image",
    "to_mermaid",
    "to_mermaid_png",
    "visualize",
]
