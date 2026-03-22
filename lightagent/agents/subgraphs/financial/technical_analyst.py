"""
Technical Analyst agent node for the financial_analyst subgraph.

Computes RSI, MACD, Bollinger Bands, SMA/EMA, Stochastic, ADX. Stores a
:class:`~lightagent.agents.subgraphs.financial.artifacts.TechnicalAnalysis`
under ``state["metadata"]["financial_analyst"]["technical_analysis"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import TechnicalAnalysis
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.technical_analyst")
otel = OTelManager()

_SYSTEM = (
    "You are a Technical Analysis agent. Based on the market data snapshot, "
    "compute and interpret technical indicators.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "symbol": "AAPL",\n'
    '  "indicators": {"RSI": 62.5, "MACD": 0.42, "MACD_signal": 0.35, '
    '"BB_upper": 180.0, "BB_lower": 165.0, "SMA_20": 172.1, "EMA_20": 173.5},\n'
    '  "signals": ["RSI neutral", "MACD bullish crossover"],\n'
    '  "chart_paths": [],\n'
    '  "trend": "bullish",\n'
    '  "support_level": 168.0,\n'
    '  "resistance_level": 182.0\n'
    "}\n"
    "trend must be one of: bullish, bearish, neutral, unknown\n"
    "Include at least 5 indicators in the output."
)


async def technical_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Run technical analysis on the collected market data.

    Args:
        state: Current agent state (must contain market_snapshot in metadata).

    Returns:
        Partial state update with ``TechnicalAnalysis`` in
        ``metadata["financial_analyst"]["technical_analysis"]``.
    """
    with otel.start_span("financial_analyst.technical_analyst") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "technical_analyst")

        fin: dict[str, Any] = dict(
            state.get("metadata", {}).get("financial_analyst", {})
        )
        snapshot = fin.get("market_snapshot", {})
        symbol = snapshot.get("symbol", "UNKNOWN")

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
            analysis = TechnicalAnalysis.model_validate(data)
        except Exception:
            analysis = TechnicalAnalysis(symbol=symbol)

        fin["technical_analysis"] = analysis.model_dump()

        logger.info(
            "technical_analyst.analysis_complete",
            symbol=analysis.symbol,
            trend=analysis.trend,
            indicator_count=len(analysis.indicators),
            signal_count=len(analysis.signals),
        )
        span.set_attribute("lightagent.financial.symbol", analysis.symbol)
        span.set_attribute("lightagent.financial.trend", analysis.trend)

        return {
            "current_agent": "technical_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"Technical analysis for {analysis.symbol}: "
                        f"trend={analysis.trend}, "
                        f"{len(analysis.indicators)} indicators computed, "
                        f"signals: {', '.join(analysis.signals[:3]) or 'none'}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
