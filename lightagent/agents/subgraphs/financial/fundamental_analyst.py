"""
Fundamental Analyst agent node for the financial_analyst subgraph.

Fetches valuation metrics, earnings data, and peer comparisons. Stores a
:class:`~lightagent.agents.subgraphs.financial.artifacts.FundamentalAnalysis`
under ``state["metadata"]["financial_analyst"]["fundamental_analysis"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import FundamentalAnalysis
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.fundamental_analyst")
otel = OTelManager()

_SYSTEM = (
    "You are a Fundamental Analysis agent. Analyze the financial fundamentals "
    "of the requested asset.\n"
    "For equity: provide P/E ratio, P/B ratio, revenue growth, earnings growth, ROE.\n"
    "For crypto: provide market cap, active addresses, protocol revenue if available.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "symbol": "AAPL",\n'
    '  "asset_type": "equity",\n'
    '  "metrics": {"trailingPE": 28.5, "priceToBook": 42.1, "revenueGrowth": 0.09},\n'
    '  "peer_comparison": {"MSFT": {"trailingPE": 35.2}},\n'
    '  "fundamental_score": 0.72,\n'
    '  "data_source": "yfinance"\n'
    "}\n"
    "fundamental_score must be 0.0-1.0 (higher = stronger fundamentals)\n"
    "asset_type must be one of: equity, crypto, forex"
)


async def fundamental_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Perform fundamental analysis on the collected asset data.

    Args:
        state: Current agent state (must contain market_snapshot in metadata).

    Returns:
        Partial state update with ``FundamentalAnalysis`` in
        ``metadata["financial_analyst"]["fundamental_analysis"]``.
    """
    with otel.start_span("financial_analyst.fundamental_analyst") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "fundamental_analyst")

        fin: dict[str, Any] = dict(
            state.get("metadata", {}).get("financial_analyst", {})
        )
        snapshot = fin.get("market_snapshot", {})
        symbol = snapshot.get("symbol", "UNKNOWN")
        asset_type: str = snapshot.get("asset_type", "equity")

        llm = ProviderRegistry().get_llm()
        context = f"Market snapshot: {json.dumps(snapshot)}"
        messages = [
            SystemMessage(content=_SYSTEM),
            *list(state["messages"][-4:]),
            SystemMessage(content=context),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            analysis = FundamentalAnalysis.model_validate(data)
        except Exception:
            analysis = FundamentalAnalysis(
                symbol=symbol,
                asset_type=asset_type,  # type: ignore[arg-type]
            )

        fin["fundamental_analysis"] = analysis.model_dump()

        logger.info(
            "fundamental_analyst.analysis_complete",
            symbol=analysis.symbol,
            score=analysis.fundamental_score,
            metrics_count=len(analysis.metrics),
        )
        span.set_attribute(
            "lightagent.financial.fundamental_score", analysis.fundamental_score
        )

        return {
            "current_agent": "fundamental_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"Fundamental analysis for {analysis.symbol}: "
                        f"score={analysis.fundamental_score:.2f}, "
                        f"{len(analysis.metrics)} metrics, "
                        f"{len(analysis.peer_comparison)} peers compared"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
