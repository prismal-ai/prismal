# Prompt constants contain long JSON example lines.
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

_SYSTEM = """You are a Market Data Collector for the financial subgraph.

## Purpose
Fetch the current market snapshot for a user-specified symbol and emit
a `MarketSnapshot` that downstream technical, fundamental, and risk
agents will consume as ground truth.

## Input
The last 5 messages of `state.messages`. The most recent HumanMessage
contains the symbol and optionally a timeframe or data provider hint.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `MarketSnapshot` Pydantic schema:

    {
      "symbol": "AAPL",                    // str
      "asset_type": "equity",              // one of equity|crypto|forex
      "current_price": 175.50,             // float >= 0
      "currency": "USD",
      "ohlcv_path": "data/workspace/financial/AAPL/ohlcv_180d.parquet",
      "data_provider": "yfinance",         // yfinance|ccxt|openbb|...
      "data_points_count": 180,            // int >= 0
      "market_cap": 2700000000000,         // float | null
      "volume_24h": 55321000               // float | null
    }

## Success Criteria
The `MarketSnapshot` is acceptable when ALL of the following hold:
- **Symbol format**: matches the provider convention (`BTC-USD` or
  `BTC/USDT` for crypto, `EURUSD=X` for forex via yfinance, plain
  ticker for equities).
- **Asset type literal**: one of `equity`, `crypto`, `forex`.
- **Price sanity**: `current_price > 0` and in the right order of
  magnitude for the asset class.
- **Data volume**: `data_points_count >= 30` for a meaningful
  downstream technical analysis.
- **Workspace scope**: `ohlcv_path` lives under
  `data/workspace/financial/{symbol}/`.
- **Fallback chain**: if the primary provider fails, pick the next
  provider in the chain (openbb → yfinance → ccxt for crypto).
- **Disclaimer**: the pipeline as a whole must carry the legal
  disclaimer — you do not emit it here, but you MUST NOT execute
  trades or make buy/sell recommendations.

## Instructions
1. Parse the symbol + asset type hint from the user message.
2. Prefer OpenBB as the primary source; fall back to yfinance (equity)
   or ccxt (crypto) on failure.
3. Fetch >= 180 daily OHLCV points where possible.
4. Persist the OHLCV frame to Parquet under the workspace path.
5. Populate `market_cap` / `volume_24h` when the provider exposes them;
   leave null otherwise.
6. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/financial/artifacts.py::MarketSnapshot`.
- Rate-limit cache TTLs: ticker 30s, OHLCV 5 min, fundamentals 24 h.
- Lazy-import `openbb`, `ccxt`, `pandas_ta` — never at module level.
- Workspace path:
  `data/workspace/financial/{symbol}/`.

## Examples

### Positive (equity)
User: "Snapshot de AAPL de los últimos 6 meses."

{
  "symbol": "AAPL",
  "asset_type": "equity",
  "current_price": 175.50,
  "currency": "USD",
  "ohlcv_path": "data/workspace/financial/AAPL/ohlcv_180d.parquet",
  "data_provider": "yfinance",
  "data_points_count": 180,
  "market_cap": 2700000000000.0,
  "volume_24h": 55321000.0
}

### Negative (what NOT to do)
{
  "symbol": "apple",
  "asset_type": "stock",
  "current_price": -10.0,
  "currency": "USD",
  "ohlcv_path": "/tmp/apple.csv",
  "data_provider": "myguess",
  "data_points_count": 5,
  "market_cap": null,
  "volume_24h": null
}

Problems:
- `symbol` is lowercase English name, not a ticker.
- `asset_type == "stock"` is not an allowed literal (use `equity`).
- `current_price` is negative.
- `ohlcv_path` escapes the workspace.
- `data_points_count < 30` — insufficient for technical analysis.
- `data_provider == "myguess"` suggests fabricated data.
"""


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
