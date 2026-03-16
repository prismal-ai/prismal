"""Unit tests for financial analyst agent nodes."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


@pytest.fixture
def base_state() -> dict[str, Any]:
    """Minimal AgentState with a financial request."""
    return {
        "messages": [HumanMessage(content="Analyze AAPL stock")],
        "session_id": "test-financial",
        "current_agent": "",
        "next_agent": None,
        "metadata": {},
    }


def _ai(content: str) -> AsyncMock:
    """Return a coroutine mock that resolves to an AIMessage."""
    return AsyncMock(return_value=AIMessage(content=content))


@pytest.mark.asyncio
async def test_market_data_collector_produces_snapshot(base_state: dict[str, Any]) -> None:
    """market_data_collector stores MarketSnapshot in metadata."""
    from lightagent.agents.subgraphs.financial.market_data_collector import (
        market_data_collector_node,
    )

    snapshot_json = json.dumps({
        "symbol": "AAPL",
        "asset_type": "equity",
        "current_price": 175.50,
        "currency": "USD",
        "data_provider": "yfinance",
        "data_points_count": 180,
    })
    with patch(
        "lightagent.agents.subgraphs.financial.market_data_collector.ProviderRegistry.get_llm",
        return_value=type("LLM", (), {"ainvoke": _ai(snapshot_json)})(),
    ):
        result = await market_data_collector_node(base_state)

    fin = result["metadata"]["financial_analyst"]
    assert fin["market_snapshot"]["symbol"] == "AAPL"
    assert fin["market_snapshot"]["current_price"] == 175.50
    assert result["current_agent"] == "market_data_collector"


@pytest.mark.asyncio
async def test_market_data_collector_graceful_fallback(base_state: dict[str, Any]) -> None:
    """market_data_collector falls back to unknown snapshot on bad LLM JSON."""
    from lightagent.agents.subgraphs.financial.market_data_collector import (
        market_data_collector_node,
    )

    with patch(
        "lightagent.agents.subgraphs.financial.market_data_collector.ProviderRegistry.get_llm",
        return_value=type("LLM", (), {"ainvoke": _ai("not valid json")})(),
    ):
        result = await market_data_collector_node(base_state)

    fin = result["metadata"]["financial_analyst"]
    assert "market_snapshot" in fin
    assert result["current_agent"] == "market_data_collector"


@pytest.mark.asyncio
async def test_technical_analyst_produces_analysis(base_state: dict[str, Any]) -> None:
    """technical_analyst stores TechnicalAnalysis in metadata."""
    from lightagent.agents.subgraphs.financial.technical_analyst import (
        technical_analyst_node,
    )

    state = dict(base_state)
    state["metadata"] = {
        "financial_analyst": {
            "market_snapshot": {"symbol": "AAPL", "asset_type": "equity", "current_price": 175.0}
        }
    }
    ta_json = json.dumps({
        "symbol": "AAPL",
        "indicators": {"RSI": 62.5, "MACD": 0.42, "SMA_20": 172.1},
        "signals": ["RSI neutral", "MACD bullish crossover"],
        "chart_paths": [],
        "trend": "bullish",
    })
    with patch(
        "lightagent.agents.subgraphs.financial.technical_analyst.ProviderRegistry.get_llm",
        return_value=type("LLM", (), {"ainvoke": _ai(ta_json)})(),
    ):
        result = await technical_analyst_node(state)

    fin = result["metadata"]["financial_analyst"]
    assert "technical_analysis" in fin
    assert fin["technical_analysis"]["trend"] == "bullish"
    assert "RSI" in fin["technical_analysis"]["indicators"]
    assert result["current_agent"] == "technical_analyst"


@pytest.mark.asyncio
async def test_fundamental_analyst_produces_analysis(base_state: dict[str, Any]) -> None:
    """fundamental_analyst stores FundamentalAnalysis in metadata."""
    from lightagent.agents.subgraphs.financial.fundamental_analyst import (
        fundamental_analyst_node,
    )
    state = dict(base_state)
    state["metadata"] = {"financial_analyst": {"market_snapshot": {"symbol": "AAPL", "asset_type": "equity"}}}
    fa_json = json.dumps({
        "symbol": "AAPL",
        "asset_type": "equity",
        "metrics": {"trailingPE": 28.5, "priceToBook": 42.1, "revenueGrowth": 0.09},
        "peer_comparison": {},
        "fundamental_score": 0.72,
        "data_source": "yfinance",
    })
    with patch(
        "lightagent.agents.subgraphs.financial.fundamental_analyst.ProviderRegistry.get_llm",
        return_value=type("LLM", (), {"ainvoke": _ai(fa_json)})(),
    ):
        result = await fundamental_analyst_node(state)

    fin = result["metadata"]["financial_analyst"]
    assert "fundamental_analysis" in fin
    assert fin["fundamental_analysis"]["fundamental_score"] == 0.72
    assert result["current_agent"] == "fundamental_analyst"
