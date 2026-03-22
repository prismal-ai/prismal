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

_SYSTEM = (
    "You are a Financial Report Generator. Consolidate all analyses into a "
    "comprehensive executive report.\n"
    "You MUST include the legal disclaimer in every report.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "symbol": "AAPL",\n'
    '  "report_mode": "single_asset",\n'
    '  "executive_summary": "Brief 2-3 sentence summary",\n'
    '  "sections": {\n'
    '    "market_data": "Current price and volume analysis...",\n'
    '    "technical": "Technical indicators and signals...",\n'
    '    "fundamental": "Valuation and financial health...",\n'
    '    "risk_sentiment": "Risk profile and market sentiment..."\n'
    '  },\n'
    '  "chart_paths": [],\n'
    '  "disclaimer": "This analysis is for informational purposes only'
    ' and does not constitute financial advice."\n'
    "}\n"
    "report_mode must be one of: single_asset, portfolio, market_overview"
)


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

        fin: dict[str, Any] = dict(
            state.get("metadata", {}).get("financial_analyst", {})
        )
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
                f"Technical: trend={ta.get('trend')}, "
                f"signals={ta.get('signals', [])[:3]}"
            )
        if fa := fin.get("fundamental_analysis"):
            context_parts.append(
                f"Fundamental: score={fa.get('fundamental_score')}"
            )
        if rs := fin.get("risk_sentiment_report"):
            context_parts.append(
                f"Risk: level={rs.get('risk_level')}, "
                f"sentiment={rs.get('sentiment_score')}"
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
