"""Builder for the Financial Analyst subgraph.

Assembles the 5-agent financial pipeline:

    market_data_collector -> technical_analyst -> fundamental_analyst
                           -> risk_sentiment_analyst -> report_generator

Phase 39 / SPEC-041 added three quality gates to the original linear
pipeline so downstream agents are never asked to analyse empty or
unreliable data:

1. **Data-availability gate** after ``market_data_collector``. Routes to
   a dedicated ``insufficient_data_report`` node (then END) when the
   snapshot is both incomplete (``missing_fields`` non-empty) AND
   uncertain (``data_confidence < financial_min_confidence``). Matches
   AC-041-2.

2. **Technical-confidence gate** after ``technical_analyst``. When the
   indicator set is too noisy (``TechnicalAnalysis.data_confidence <
   financial_technical_min_confidence``) the pipeline skips fundamental
   analysis and jumps to ``risk_sentiment_analyst`` via a
   ``limited_technical_disclaimer`` passthrough that flags the state for
   the report generator. Matches AC-041-3.

3. **Optional HITL gate** after ``report_generator``. Activated by
   ``LIGHTAGENT_FINANCIAL_HITL_ENABLED=true``. Reuses the Phase 35 HITL
   primitives (``seed_hitl_metadata`` + ``human_approval_node`` +
   ``hitl_gate``). Matches AC-041-4.

The pipeline uses an isolated checkpointer at
``data/db/checkpoints_subgraph_financial_analyst.db``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage

from lightagent.agents.subgraphs.factory import SubgraphFactory
from lightagent.agents.subgraphs.financial.artifacts import (
    _DISCLAIMER,
    FinancialReport,
)
from lightagent.agents.subgraphs.financial.fundamental_analyst import (
    fundamental_analyst_node,
)
from lightagent.agents.subgraphs.financial.market_data_collector import (
    market_data_collector_node,
)
from lightagent.agents.subgraphs.financial.report_generator import (
    report_generator_node,
)
from lightagent.agents.subgraphs.financial.risk_sentiment_analyst import (
    risk_sentiment_analyst_node,
)
from lightagent.agents.subgraphs.financial.technical_analyst import (
    technical_analyst_node,
)
from lightagent.agents.subgraphs.gates import (
    _get_nested,
    hitl_gate,
    human_approval_node,
    seed_hitl_metadata,
)
from lightagent.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from lightagent.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger("lightagent.subgraphs.financial.builder")

_NAME = "financial_analyst"
_DESCRIPTION = (
    "Financial Analysis Pipeline: "
    "Market Data Collector -> Technical Analyst -> Fundamental Analyst "
    "-> Risk/Sentiment Analyst -> Report Generator"
)

# Module-level cache for compiled graphs (accessed by graph.py via
# get_compiled_financial_analyst)
_COMPILED_GRAPHS: dict[str, object] = {}

#: Dotted path into ``state["metadata"]`` where the error / limited-data
#: flags are kept. Keeping them in a single known location lets tests
#: and the report_generator discover them without hard-coding the
#: individual field names.
_FINANCIAL_METADATA_KEY = "financial_analyst"


# ---------------------------------------------------------------------------
# Gate factories (inline so the builder stays self-contained)
# ---------------------------------------------------------------------------


def _market_data_availability_gate(
    *,
    on_pass: str,
    on_fail: str,
    min_confidence: float | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Build the data-availability gate for ``market_data_collector``.

    The gate routes to ``on_fail`` only when BOTH conditions hold
    simultaneously (AC-041-2):

    * ``missing_fields`` is a non-empty list, i.e. the collector could
      not populate every expected field.
    * ``data_confidence`` is strictly below the configured threshold.

    When either condition is missing — e.g. a snapshot with missing
    fields but strong confidence, or low confidence but a complete field
    list — the pipeline is allowed to continue. This is intentional: the
    two signals are complementary and a single trip wire would be too
    trigger-happy. The threshold defaults to
    :attr:`Settings.financial_min_confidence` but can be overridden at
    build time for tests.

    Args:
        on_pass: Node to route to when data quality is acceptable.
        on_fail: Node to route to when the snapshot is unusable.
        min_confidence: Optional override for the confidence threshold.
            When ``None`` the setting value is read at gate-evaluation
            time so runtime settings changes are honoured.

    Returns:
        A routing callable ``(state) -> str``.
    """

    def gate(state: dict[str, Any]) -> str:
        threshold = (
            min_confidence
            if min_confidence is not None
            else get_settings().financial_min_confidence
        )
        meta = state.get("metadata", {})
        snapshot = _get_nested(
            meta, f"{_FINANCIAL_METADATA_KEY}.market_snapshot"
        )
        if not isinstance(snapshot, dict):
            # No snapshot at all → treat as unusable.
            logger.warning(
                "financial.market_gate_missing_snapshot",
                routing_to=on_fail,
            )
            return on_fail

        missing_fields = snapshot.get("missing_fields") or []
        confidence = snapshot.get("data_confidence", 0.0)
        try:
            confidence_f = float(confidence)
        except (TypeError, ValueError):
            confidence_f = 0.0

        unusable = bool(missing_fields) and confidence_f < threshold
        if unusable:
            logger.warning(
                "financial.market_gate_blocked",
                missing_fields=missing_fields,
                data_confidence=confidence_f,
                threshold=threshold,
            )
            return on_fail
        return on_pass

    gate.__name__ = "financial_market_data_gate"
    return gate


def _technical_confidence_gate(
    *,
    on_full: str,
    on_limited: str,
    min_confidence: float | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Build the technical-analysis confidence gate (AC-041-3).

    Routes to ``on_full`` when ``TechnicalAnalysis.data_confidence >=
    financial_technical_min_confidence``; otherwise to ``on_limited``.
    The ``on_limited`` path is expected to wire through a passthrough
    that flags the state so the report generator includes the disclaimer
    *"Technical analysis based on limited data"*.
    """

    def gate(state: dict[str, Any]) -> str:
        threshold = (
            min_confidence
            if min_confidence is not None
            else get_settings().financial_technical_min_confidence
        )
        meta = state.get("metadata", {})
        tech = _get_nested(
            meta, f"{_FINANCIAL_METADATA_KEY}.technical_analysis"
        )
        if not isinstance(tech, dict):
            logger.warning(
                "financial.technical_gate_missing_artifact",
                routing_to=on_limited,
            )
            return on_limited

        try:
            confidence_f = float(tech.get("data_confidence", 0.0))
        except (TypeError, ValueError):
            confidence_f = 0.0

        if confidence_f < threshold:
            logger.info(
                "financial.technical_gate_limited",
                data_confidence=confidence_f,
                threshold=threshold,
            )
            return on_limited
        return on_full

    gate.__name__ = "financial_technical_confidence_gate"
    return gate


# ---------------------------------------------------------------------------
# Error / disclaimer nodes
# ---------------------------------------------------------------------------


_INSUFFICIENT_DATA_MESSAGE = (
    "Insufficient market data to generate analysis. "
    "The snapshot is missing required fields and the available data "
    "confidence is below the minimum threshold."
)

_LIMITED_DATA_DISCLAIMER = "Technical analysis based on limited data."


async def _insufficient_data_report_node(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Produce an error ``FinancialReport`` when the data gate blocks.

    Runs as a dead-end node before routing to ``__end__`` so the user
    still receives a structured, disclaimer-stamped response explaining
    why no analysis was performed. The error report is written to
    ``state["metadata"]["financial_analyst"]["financial_report"]`` using
    ``FinancialReport.model_dump()`` exactly like the normal
    ``report_generator`` output, so downstream consumers (dashboards,
    SDK clients) do not need a separate code path.
    """
    meta = dict(state.get("metadata", {}))
    fin: dict[str, Any] = dict(meta.get(_FINANCIAL_METADATA_KEY, {}))
    snapshot = fin.get("market_snapshot", {}) or {}
    symbol = snapshot.get("symbol", "UNKNOWN")

    report = FinancialReport(
        symbol=symbol,
        executive_summary=_INSUFFICIENT_DATA_MESSAGE,
        sections={
            "Error": _INSUFFICIENT_DATA_MESSAGE,
            "Missing Fields": ", ".join(snapshot.get("missing_fields", []))
            or "(unknown)",
        },
        disclaimer=_DISCLAIMER,
    )
    fin["financial_report"] = report.model_dump()
    fin["insufficient_data"] = True
    meta[_FINANCIAL_METADATA_KEY] = fin

    logger.warning(
        "financial.insufficient_data_report_emitted",
        symbol=symbol,
        missing_fields=snapshot.get("missing_fields", []),
    )

    return {
        "current_agent": "insufficient_data_report",
        "messages": [
            AIMessage(
                content=f"{_INSUFFICIENT_DATA_MESSAGE} ({_DISCLAIMER})"
            )
        ],
        "metadata": meta,
    }


async def _limited_technical_disclaimer_node(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Passthrough that flags the state for a limited-data disclaimer.

    Writes ``metadata["financial_analyst"]["limited_data_disclaimer"] =
    True`` and appends the canonical disclaimer string under
    ``limited_data_note``. The report generator picks these up to prefix
    its final output with the AC-041-3 disclaimer.
    """
    meta = dict(state.get("metadata", {}))
    fin: dict[str, Any] = dict(meta.get(_FINANCIAL_METADATA_KEY, {}))
    fin["limited_data_disclaimer"] = True
    fin["limited_data_note"] = _LIMITED_DATA_DISCLAIMER
    meta[_FINANCIAL_METADATA_KEY] = fin

    logger.info("financial.limited_technical_disclaimer_flagged")

    return {
        "current_agent": "limited_technical_disclaimer",
        "metadata": meta,
    }


# ---------------------------------------------------------------------------
# Definition assembly
# ---------------------------------------------------------------------------


def _make_definition() -> SubgraphDefinition:
    """Build the :class:`SubgraphDefinition` for the financial pipeline.

    The topology is assembled in two steps: (1) a base definition with
    the linear pipeline, then (2) conditional edges that splice the
    quality gates between the original nodes. When
    ``LIGHTAGENT_FINANCIAL_HITL_ENABLED=true`` an extra pair of nodes
    (``hitl_seed`` + ``human_approval``) is inserted between
    ``report_generator`` and END.

    Returns:
        A fully configured :class:`SubgraphDefinition`.
    """
    settings = get_settings()

    nodes: dict[str, Any] = {
        "market_data_collector": market_data_collector_node,
        "insufficient_data_report": _insufficient_data_report_node,
        "technical_analyst": technical_analyst_node,
        "limited_technical_disclaimer": _limited_technical_disclaimer_node,
        "fundamental_analyst": fundamental_analyst_node,
        "risk_sentiment_analyst": risk_sentiment_analyst_node,
        "report_generator": report_generator_node,
    }

    # Linear edges for the nodes that don't branch.
    edges: list[tuple[str, str]] = [
        ("insufficient_data_report", "__end__"),
        ("limited_technical_disclaimer", "risk_sentiment_analyst"),
        ("fundamental_analyst", "risk_sentiment_analyst"),
        ("risk_sentiment_analyst", "report_generator"),
    ]

    # Conditional edges for the three quality gates.
    conditional_edges: dict[str, Callable[[dict[str, Any]], str]] = {
        # AC-041-2: Data-availability gate.
        "market_data_collector": _market_data_availability_gate(
            on_pass="technical_analyst",  # noqa: S106 -- not a password
            on_fail="insufficient_data_report",
        ),
        # AC-041-3: Technical confidence gate.
        "technical_analyst": _technical_confidence_gate(
            on_full="fundamental_analyst",
            on_limited="limited_technical_disclaimer",
        ),
    }

    # AC-041-4: Optional HITL gate after the report is produced. The
    # default (``financial_hitl_enabled=False``) wires ``report_generator``
    # straight to END. When HITL is on we insert the Phase 35 approval
    # nodes so the user must explicitly approve the report before it is
    # delivered.
    if settings.financial_hitl_enabled:
        nodes["financial_report_approval_seed"] = seed_hitl_metadata(
            artifact_field=(
                f"{_FINANCIAL_METADATA_KEY}.financial_report"
            ),
            risk_level="MEDIUM",
        )
        nodes["financial_report_human_approval"] = human_approval_node
        edges.append(("report_generator", "financial_report_approval_seed"))
        edges.append(
            (
                "financial_report_approval_seed",
                "financial_report_human_approval",
            )
        )
        conditional_edges["financial_report_human_approval"] = hitl_gate(
            artifact_field=(
                f"{_FINANCIAL_METADATA_KEY}.financial_report"
            ),
            on_approve="__end__",
            on_reject="__end__",
            risk_level="MEDIUM",
            bypass_condition=lambda _s: (
                not get_settings().financial_hitl_enabled
            ),
        )
    else:
        edges.append(("report_generator", "__end__"))

    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="market_data_collector",
        nodes=nodes,
        edges=edges,
        conditional_edges=conditional_edges,
    )


async def register_financial_analyst(
    checkpointer_path: str = "data/db/checkpoints_subgraph_financial_analyst.db",
) -> None:
    """Build and register the financial_analyst subgraph.

    Idempotent -- skips registration if already registered.

    Args:
        checkpointer_path: SQLite file path for checkpointing.
            Use ``":memory:"`` in tests.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("financial_analyst.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("financial_analyst.registered")


async def get_compiled_financial_analyst(
    checkpointer_path: str = ":memory:",
) -> object:
    """Return the compiled financial_analyst graph (building it if needed).

    Args:
        checkpointer_path: SQLite path used only on first build.

    Returns:
        Compiled ``CompiledStateGraph``.
    """
    if _NAME not in _COMPILED_GRAPHS:
        definition = _make_definition()
        factory = SubgraphFactory()
        _COMPILED_GRAPHS[_NAME] = await factory.build(
            definition, checkpointer_path=checkpointer_path
        )
    return _COMPILED_GRAPHS[_NAME]


__all__ = [
    "_limited_technical_disclaimer_node",
    "_make_definition",
    "_market_data_availability_gate",
    "_technical_confidence_gate",
    "get_compiled_financial_analyst",
    "register_financial_analyst",
]
