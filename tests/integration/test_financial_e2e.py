"""Integration test — complete financial_analyst pipeline (5 agents, mocked LLM).

Verifies that all 5 agents run sequentially and produce all 5 typed artifacts
in ``state["metadata"]["financial_analyst"]`` without raising exceptions.
No real API calls — all LLM responses are mocked.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


@pytest.fixture
def base_state() -> dict[str, Any]:
    """Minimal AgentState with a financial analysis request."""
    return {
        "messages": [HumanMessage(content="Analyze AAPL stock")],
        "session_id": "test-financial-e2e",
        "current_agent": "",
        "next_agent": None,
        "metadata": {},
    }


def _ai(content: str) -> AsyncMock:
    """Return a coroutine mock resolving to an AIMessage."""
    return AsyncMock(return_value=AIMessage(content=content))


@pytest.mark.asyncio
async def test_full_pipeline_produces_all_artifacts(base_state: dict[str, Any]) -> None:
    """Full 5-agent pipeline produces all 5 artifacts in metadata."""
    from prismal.agents.subgraphs.financial.fundamental_analyst import (
        fundamental_analyst_node,
    )
    from prismal.agents.subgraphs.financial.market_data_collector import (
        market_data_collector_node,
    )
    from prismal.agents.subgraphs.financial.report_generator import (
        report_generator_node,
    )
    from prismal.agents.subgraphs.financial.risk_sentiment_analyst import (
        risk_sentiment_analyst_node,
    )
    from prismal.agents.subgraphs.financial.technical_analyst import (
        technical_analyst_node,
    )

    snapshot_json = json.dumps(
        {
            "symbol": "AAPL",
            "asset_type": "equity",
            "current_price": 175.0,
            "currency": "USD",
            "data_provider": "yfinance",
            "data_points_count": 180,
        }
    )
    ta_json = json.dumps(
        {
            "symbol": "AAPL",
            "indicators": {"RSI": 62.5, "MACD": 0.42},
            "signals": ["MACD bullish crossover"],
            "chart_paths": [],
            "trend": "bullish",
        }
    )
    fa_json = json.dumps(
        {
            "symbol": "AAPL",
            "asset_type": "equity",
            "metrics": {"trailingPE": 28.5},
            "peer_comparison": {},
            "fundamental_score": 0.72,
            "data_source": "yfinance",
        }
    )
    rs_json = json.dumps(
        {
            "symbol": "AAPL",
            "volatility_annual": 0.25,
            "sharpe_ratio": 1.3,
            "max_drawdown": 0.18,
            "var_95": 0.021,
            "sentiment_score": 0.65,
            "sentiment_sources": [],
            "correlation_assets": {},
            "risk_level": "medium",
        }
    )
    report_json = json.dumps(
        {
            "symbol": "AAPL",
            "report_mode": "single_asset",
            "executive_summary": "AAPL shows bullish momentum.",
            "sections": {"technical": "RSI 62.5", "fundamental": "P/E 28.5"},
            "chart_paths": [],
            "disclaimer": (
                "This analysis is for informational purposes only"
                " and does not constitute financial advice."
            ),
        }
    )

    def make_llm(resp: str) -> object:
        """Return a minimal fake LLM."""
        return type("LLM", (), {"ainvoke": _ai(resp)})()

    state = dict(base_state)
    nodes = [
        (market_data_collector_node, "market_data_collector", snapshot_json),
        (technical_analyst_node, "technical_analyst", ta_json),
        (fundamental_analyst_node, "fundamental_analyst", fa_json),
        (risk_sentiment_analyst_node, "risk_sentiment_analyst", rs_json),
        (report_generator_node, "report_generator", report_json),
    ]
    for node_fn, mod_name, resp in nodes:
        mod_path = f"lightagent.agents.subgraphs.financial.{mod_name}.ProviderRegistry.get_llm"
        with patch(mod_path, return_value=make_llm(resp)):
            update = await node_fn(state)  # type: ignore[arg-type]
        state.update(update)
        state["messages"] = list(state.get("messages", [])) + list(update.get("messages", []))

    fin = state["metadata"]["financial_analyst"]
    assert "market_snapshot" in fin
    assert "technical_analysis" in fin
    assert "fundamental_analysis" in fin
    assert "risk_sentiment_report" in fin
    assert "financial_report" in fin
    # Disclaimer always present
    assert "informational purposes only" in fin["financial_report"]["disclaimer"]
    assert fin["market_snapshot"]["symbol"] == "AAPL"
    assert fin["technical_analysis"]["trend"] == "bullish"
    assert fin["fundamental_analysis"]["fundamental_score"] == 0.72
    assert fin["risk_sentiment_report"]["risk_level"] == "medium"
