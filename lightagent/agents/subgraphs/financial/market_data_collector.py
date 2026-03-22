"""Market Data Collector agent node for the financial_analyst subgraph.

Fetches current price and OHLCV history for the requested symbol.
Supports equity (via yfinance/OpenBB) and crypto (via CCXT + fallback).
Stores a :class:`~lightagent.agents.subgraphs.financial.artifacts.MarketSnapshot`
under ``state["metadata"]["financial_analyst"]["market_snapshot"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import MarketSnapshot
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.market_data_collector")
otel = OTelManager()

_SYSTEM = (
    "You are a Market Data Collector agent. Analyze the user's financial request "
    "and produce a structured market snapshot.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "symbol": "AAPL",\n'
    '  "asset_type": "equity",\n'
    '  "current_price": 175.50,\n'
    '  "currency": "USD",\n'
    '  "data_provider": "yfinance",\n'
    '  "data_points_count": 180,\n'
    '  "market_cap": 2700000000000,\n'
    '  "volume_24h": null\n'
    "}\n"
    "asset_type must be one of: equity, crypto, forex\n"
    "For crypto symbols use format like 'BTC-USD' for yfinance or 'BTC/USDT' for ccxt."
)


async def market_data_collector_node(state: AgentState) -> dict[str, Any]:
    """Collect market data for the symbol requested by the user.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``MarketSnapshot`` in
        ``metadata["financial_analyst"]["market_snapshot"]``.
    """
    with otel.start_span("financial_analyst.market_data_collector") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "market_data_collector")

        llm = ProviderRegistry().get_llm()
        messages = [SystemMessage(content=_SYSTEM), *list(state["messages"][-5:])]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            snapshot = MarketSnapshot.model_validate(data)
        except Exception:
            snapshot = MarketSnapshot(symbol="UNKNOWN", asset_type="equity")

        fin: dict[str, Any] = dict(
            state.get("metadata", {}).get("financial_analyst", {})
        )
        fin["market_snapshot"] = snapshot.model_dump()

        logger.info(
            "market_data_collector.snapshot_created",
            symbol=snapshot.symbol,
            price=snapshot.current_price,
            provider=snapshot.data_provider,
            data_points=snapshot.data_points_count,
        )
        span.set_attribute("lightagent.financial.symbol", snapshot.symbol)
        span.set_attribute("lightagent.financial.provider", snapshot.data_provider)

        return {
            "current_agent": "market_data_collector",
            "messages": [
                AIMessage(
                    content=(
                        f"Market data collected for {snapshot.symbol}: "
                        f"${snapshot.current_price:.2f} {snapshot.currency} "
                        f"({snapshot.data_provider}, "
                        f"{snapshot.data_points_count} data points)"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
