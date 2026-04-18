# Prompt constants contain long JSON example lines.
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

_SYSTEM = """You are a Risk & Sentiment Analyst for the financial subgraph.

## Purpose
Quantify the risk profile and market sentiment for the upstream asset
and emit a `RiskSentimentReport` the report generator will fold into
the final narrative.

## Input
One AIMessage containing the JSON dump of the upstream `MarketSnapshot`
(and optionally `TechnicalAnalysis`) from
`state.metadata.financial_analyst`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `RiskSentimentReport` Pydantic schema:

    {
      "symbol": "AAPL",
      "volatility_annual": 0.25,           // float >= 0 (decimal)
      "sharpe_ratio": 1.3,                 // float | null
      "max_drawdown": 0.18,                // float in [0.0, 1.0]
      "var_95": 0.021,                     // float (decimal fraction)
      "sentiment_score": 0.65,             // float in [0.0, 1.0]
      "sentiment_sources": ["news", "social", "on-chain"],
      "correlation_assets": {"SPY": 0.82, "QQQ": 0.91},
      "risk_level": "medium"               // one of low|medium|high|very_high
    }

## Success Criteria
The `RiskSentimentReport` is acceptable when ALL of the following hold:
- **Volatility**: `volatility_annual` computed from the annualized
  standard deviation of daily log returns over the OHLCV window.
- **VaR sanity**: `0 <= var_95 <= 1` and typically
  `var_95 ≈ 1.65 * daily_volatility`.
- **Drawdown range**: `0 <= max_drawdown <= 1`.
- **Sentiment score**: in [0, 1]; `sentiment_sources` non-empty.
- **Risk-level literal**: one of `low` (vol < 0.15), `medium`
  (0.15-0.30), `high` (0.30-0.50), `very_high` (> 0.50).
- **Correlations**: keys are real tickers; values in [-1, 1].
- **No trading calls**: describe risk, do NOT recommend positions.

## Instructions
1. Load OHLCV from the upstream snapshot path.
2. Compute daily log returns, annualize std, derive Sharpe if a
   risk-free proxy is available.
3. Compute `max_drawdown` as the largest peak-to-trough loss.
4. Compute `var_95` from the daily return distribution.
5. Gather sentiment from at least 2 sources (news, social, on-chain).
6. Compute 30-day correlations to at least 2 benchmark assets.
7. Assign `risk_level` using the volatility bands above.
8. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/financial/artifacts.py::RiskSentimentReport`.
- Sentiment sources: NewsAPI, crypto news RSS, Reddit/Twitter (when
  available), on-chain metrics for crypto.
- Phase 27 read-only rule: never execute trades.

## Examples

### Positive
{
  "symbol": "AAPL",
  "volatility_annual": 0.25,
  "sharpe_ratio": 1.3,
  "max_drawdown": 0.18,
  "var_95": 0.021,
  "sentiment_score": 0.65,
  "sentiment_sources": ["news", "social"],
  "correlation_assets": {"SPY": 0.82, "QQQ": 0.91, "MSFT": 0.78},
  "risk_level": "medium"
}

### Negative (what NOT to do)
{
  "symbol": "AAPL",
  "volatility_annual": -0.25,
  "sharpe_ratio": "good",
  "max_drawdown": 2.0,
  "var_95": 5.0,
  "sentiment_score": 1.5,
  "sentiment_sources": [],
  "correlation_assets": {"SPY": 2.0},
  "risk_level": "catastrophic"
}

Problems:
- Negative `volatility_annual`.
- `sharpe_ratio` is a string instead of float/null.
- `max_drawdown` > 1 and `var_95` > 1 — out of decimal range.
- `sentiment_score` > 1.
- `sentiment_sources` empty.
- Correlation > 1.
- `risk_level == "catastrophic"` is not an allowed literal.
"""


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

        fin: dict[str, Any] = dict(state.get("metadata", {}).get("financial_analyst", {}))
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
