"""
Risk and Sentiment Analyst agent node for the financial_analyst subgraph.

Computes volatility, Sharpe ratio, max drawdown, VaR and market sentiment.
Stores a :class:`~lightagent.agents.subgraphs.financial.artifacts.RiskSentimentReport`
under ``state["metadata"]["financial_analyst"]["risk_sentiment_report"]``.

Sentiment sources (LunarCrush, MT Newswires) are optional — the agent
degrades gracefully when MCP tools are not connected.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import RiskSentimentReport
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.risk_sentiment_analyst")
otel = OTelManager()

_SYSTEM = (
    "You are a Risk and Sentiment Analysis agent. Evaluate the risk profile "
    "and market sentiment for the requested asset.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "symbol": "AAPL",\n'
    '  "volatility_annual": 0.25,\n'
    '  "sharpe_ratio": 1.3,\n'
    '  "max_drawdown": 0.18,\n'
    '  "var_95": 0.021,\n'
    '  "sentiment_score": 0.65,\n'
    '  "sentiment_sources": ["news", "social"],\n'
    '  "correlation_assets": {"SPY": 0.82, "QQQ": 0.91},\n'
    '  "risk_level": "medium"\n'
    "}\n"
    "risk_level must be one of: low, medium, high, very_high\n"
    "sentiment_score: 0.0 = very bearish, 1.0 = very bullish\n"
    "volatility_annual: annualised std dev of daily returns (decimal)\n"
    "max_drawdown: max peak-to-trough loss [0.0-1.0]\n"
    "var_95: 1-day 95% VaR (decimal fraction)"
)


async def risk_sentiment_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Compute risk metrics and market sentiment for the asset.

    Args:
        state: Current agent state (must contain market_snapshot in metadata).

    Returns:
        Partial state update with ``RiskSentimentReport`` in
        ``metadata["financial_analyst"]["risk_sentiment_report"]``.
    """
    with otel.start_span("financial_analyst.risk_sentiment_analyst") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "risk_sentiment_analyst")

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
            report = RiskSentimentReport.model_validate(data)
        except Exception:
            report = RiskSentimentReport(symbol=symbol)

        fin["risk_sentiment_report"] = report.model_dump()

        logger.info(
            "risk_sentiment_analyst.report_complete",
            symbol=report.symbol,
            risk_level=report.risk_level,
            sentiment=report.sentiment_score,
            volatility=report.volatility_annual,
        )
        span.set_attribute("lightagent.financial.risk_level", report.risk_level)
        span.set_attribute("lightagent.financial.sentiment", report.sentiment_score)

        return {
            "current_agent": "risk_sentiment_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"Risk/Sentiment analysis for {report.symbol}: "
                        f"risk={report.risk_level}, "
                        f"volatility={report.volatility_annual:.1%}, "
                        f"sentiment={report.sentiment_score:.2f}, "
                        f"Sharpe={report.sharpe_ratio}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
