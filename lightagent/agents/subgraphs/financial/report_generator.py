# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""
Report Generator agent node for the financial_analyst subgraph.

Consolidates all prior analyses (market, technical, fundamental, risk/sentiment)
into an executive financial report. The legal disclaimer is ALWAYS present —
even if the LLM fails to include it, it is injected automatically (Phase 27 rule 2).

Stores a :class:`~lightagent.agents.subgraphs.financial.artifacts.FinancialReport`
under ``state["metadata"]["financial_analyst"]["financial_report"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import (
    _DISCLAIMER,
    FinancialReport,
)
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.report_generator")
otel = OTelManager()

_SYSTEM = """You are a Financial Report Generator for the financial subgraph.

## Purpose
Consolidate the four upstream analyses (MarketSnapshot,
TechnicalAnalysis, FundamentalAnalysis, RiskSentimentReport) into a
single `FinancialReport` Markdown narrative with the legally required
disclaimer.

## Input
One AIMessage containing the JSON dumps of all four upstream artifacts
from `state.metadata.financial_analyst`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `FinancialReport` Pydantic schema:

    {
      "symbol": "AAPL",
      "report_mode": "single_asset",       // one of single_asset|portfolio|market_overview
      "executive_summary": "2-4 sentence summary grounded in upstream metrics",
      "sections": {
        "market_data": "...",
        "technical": "...",
        "fundamental": "...",
        "risk_sentiment": "..."
      },
      "chart_paths": [
        "data/workspace/financial/AAPL/technical/rsi_macd.png"
      ],
      "report_path": "data/workspace/financial/AAPL/report.md",
      "disclaimer": "This analysis is for informational purposes only and does not constitute financial advice."
    }

## Success Criteria
The `FinancialReport` is acceptable when ALL of the following hold:
- **Mode literal**: `report_mode` is one of `single_asset`,
  `portfolio`, `market_overview`.
- **All 4 sections populated**: `sections` contains non-empty entries
  for `market_data`, `technical`, `fundamental`, `risk_sentiment`.
- **Grounded narrative**: every claim in `executive_summary` and
  `sections` cites a value from the upstream artifacts (price,
  indicator, metric, or risk score). Do NOT introduce new numbers.
- **Disclaimer literal**: `disclaimer` exactly equals
  `"This analysis is for informational purposes only and does not
  constitute financial advice."` The runtime also re-injects this
  value, but you MUST still include it yourself.
- **No trading calls**: never use imperatives like "buy", "sell",
  "long", "short". Use "may indicate", "suggests", "historically
  correlates with".
- **Workspace scope**: `report_path` under
  `data/workspace/financial/{symbol}/`.

## Instructions
1. Parse all four upstream artifacts.
2. Write an executive summary that references at least one value from
   each upstream artifact.
3. Fill each of the four sections with 2-6 sentences grounded in the
   matching artifact's fields.
4. Copy chart paths from upstream TechnicalAnalysis and any
   RiskSentimentReport visualisations.
5. Set `report_path` and persist the Markdown via the runtime helper.
6. Include the exact disclaimer string.
7. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/financial/artifacts.py::FinancialReport`.
- `_DISCLAIMER` constant lives in the same module; the runtime
  re-injects it on the parsed artifact as a safety net.
- Workspace:
  `data/workspace/financial/{symbol}/`.

## Examples

### Positive
{
  "symbol": "AAPL",
  "report_mode": "single_asset",
  "executive_summary": "AAPL closed at 175.50 USD with a bullish short-term trend (RSI 62.5, MACD crossover). Fundamentals remain strong (fundamental_score 0.72, trailingPE 28.5) and risk is moderate (annualised volatility 0.25, max drawdown 0.18).",
  "sections": {
    "market_data": "Last close 175.50 USD on yfinance (180 daily points). Market cap 2.7T USD, 24h volume 55.3M shares.",
    "technical": "RSI 62.5 (neutral), MACD 0.42 above signal 0.35 (bullish crossover). Price above SMA_20 (172.1) and EMA_20 (173.5). Support 168.0, resistance 182.0.",
    "fundamental": "trailingPE 28.5 vs MSFT 35.2 and GOOGL 26.1. priceToBook 42.1, ROE 1.47, revenueGrowth 9%. Composite fundamental score 0.72.",
    "risk_sentiment": "Annualised volatility 0.25 (medium risk band), Sharpe 1.3, max drawdown 0.18, 1-day VaR 2.1%. Sentiment 0.65 from news and social sources. 30-day correlation to SPY 0.82."
  },
  "chart_paths": [
    "data/workspace/financial/AAPL/technical/rsi_macd.png",
    "data/workspace/financial/AAPL/technical/bollinger.png"
  ],
  "report_path": "data/workspace/financial/AAPL/report.md",
  "disclaimer": "This analysis is for informational purposes only and does not constitute financial advice."
}

### Negative (what NOT to do)
{
  "symbol": "AAPL",
  "report_mode": "buy_signal",
  "executive_summary": "AAPL is a great buy at 175. Load up.",
  "sections": {"market_data": "It's going up."},
  "chart_paths": [],
  "report_path": "/tmp/aapl.md",
  "disclaimer": ""
}

Problems:
- `report_mode == "buy_signal"` is not an allowed literal.
- `executive_summary` is a buy recommendation (violates Phase 27).
- Only one of the four required sections is populated.
- `report_path` escapes the workspace.
- `disclaimer` is empty.
"""


async def report_generator_node(state: AgentState) -> dict[str, Any]:
    """
    Generate a consolidated financial report from all prior analyses.

    The disclaimer is always injected regardless of LLM output (CLAUDE.md
    Phase 27, rule 2: every output must include the legal disclaimer).

    Args:
        state: Current agent state (should contain all 4 prior analyses).

    Returns:
        Partial state update with ``FinancialReport`` in
        ``metadata["financial_analyst"]["financial_report"]``.
    """
    with otel.start_span("financial_analyst.report_generator") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "report_generator")

        fin: dict[str, Any] = dict(state.get("metadata", {}).get("financial_analyst", {}))
        snapshot = fin.get("market_snapshot", {})
        symbol = snapshot.get("symbol", "UNKNOWN")

        # Build context from metadata only — never log raw financial data
        context_parts = [
            f"Market snapshot: symbol={symbol}, "
            f"asset_type={snapshot.get('asset_type', 'equity')}, "
            f"price={snapshot.get('current_price', 0)}"
        ]
        if ta := fin.get("technical_analysis"):
            context_parts.append(
                f"Technical: trend={ta.get('trend')}, signals={ta.get('signals', [])[:3]}"
            )
        if fa := fin.get("fundamental_analysis"):
            context_parts.append(f"Fundamental: score={fa.get('fundamental_score')}")
        if rs := fin.get("risk_sentiment_report"):
            context_parts.append(
                f"Risk: level={rs.get('risk_level')}, sentiment={rs.get('sentiment_score')}"
            )

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            *list(state["messages"][-4:]),
            SystemMessage(content="\n".join(context_parts)),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            report = FinancialReport.model_validate(data)
        except Exception:
            report = FinancialReport(symbol=symbol, report_mode="single_asset")

        # HARD REQUIREMENT (CLAUDE.md Phase 27 rule 2): disclaimer always present
        missing = not report.disclaimer
        wrong = "informational purposes only" not in report.disclaimer
        if missing or wrong:
            report = report.model_copy(update={"disclaimer": _DISCLAIMER})

        fin["financial_report"] = report.model_dump()

        logger.info(
            "report_generator.report_complete",
            symbol=report.symbol,
            mode=report.report_mode,
            sections=list(report.sections.keys()),
        )
        span.set_attribute("lightagent.financial.symbol", report.symbol)

        return {
            "current_agent": "report_generator",
            "messages": [
                AIMessage(
                    content=(
                        f"Financial analysis report for {report.symbol} complete.\n\n"
                        f"**Executive Summary:** {report.executive_summary}\n\n"
                        f"_{report.disclaimer}_"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
