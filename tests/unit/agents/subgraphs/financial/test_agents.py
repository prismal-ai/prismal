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
