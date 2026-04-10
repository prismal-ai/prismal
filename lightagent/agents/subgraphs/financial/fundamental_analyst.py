# Prompt constants contain long JSON example lines.
"""
Fundamental Analyst agent node for the financial_analyst subgraph.

Fetches valuation metrics, earnings data, and peer comparisons. Stores a
:class:`~lightagent.agents.subgraphs.financial.artifacts.FundamentalAnalysis`
under ``state["metadata"]["financial_analyst"]["fundamental_analysis"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.financial.artifacts import FundamentalAnalysis
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.fundamental_analyst")
otel = OTelManager()

_SYSTEM = """You are a Fundamental Analyst for the financial subgraph.

## Purpose
Measure the fundamental health of the upstream asset, compare it to
peers, and emit a `FundamentalAnalysis` with a composite score the
report generator will narrate.

## Input
One AIMessage containing the JSON dump of the upstream `MarketSnapshot`
from `state.metadata.financial_analyst.market_snapshot`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `FundamentalAnalysis` Pydantic schema:

    {
      "symbol": "AAPL",
      "asset_type": "equity",              // one of equity|crypto|forex
      "metrics": {
        "trailingPE": 28.5, "priceToBook": 42.1,
        "revenueGrowth": 0.09, "earningsGrowth": 0.07, "ROE": 1.47
      },
      "peer_comparison": {
        "MSFT": {"trailingPE": 35.2, "ROE": 0.38},
        "GOOGL": {"trailingPE": 26.1, "ROE": 0.29}
      },
      "fundamental_score": 0.72,           // float in [0.0, 1.0]
      "data_source": "yfinance"
    }

## Success Criteria
The `FundamentalAnalysis` is acceptable when ALL of the following hold:
- **Asset-type literal**: one of `equity`, `crypto`, `forex`.
- **Required metrics per asset type**:
    - equity: `trailingPE`, `priceToBook`, `revenueGrowth`,
      `earningsGrowth`, `ROE` (at least 4 of 5).
    - crypto: `market_cap`, `active_addresses`, `TVL`,
      `protocol_revenue` (at least 2 of 4 where available).
    - forex: `interest_rate_differential`, `inflation_differential`
      (at least 1).
- **Peer comparison**: at least 2 peers for equity/crypto; none for
  forex.
- **Score semantics**: `fundamental_score` in [0, 1], higher =
  stronger fundamentals.
- **Source traceability**: `data_source` names a real provider
  (yfinance, openbb, alphavantage, coingecko, …).

## Instructions
1. Parse the `MarketSnapshot`.
2. Fetch fundamentals via the matching provider (lazy-import).
3. Select peers from the same sector/category.
4. Compute `fundamental_score` as a normalized composite of the
   metrics relative to peers (e.g. median-normalized z-scores then
   sigmoid to [0, 1]).
5. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/financial/artifacts.py::FundamentalAnalysis`.
- Lazy-import `openbb`, `yfinance`.
- Fundamentals TTL cache: 24 h.

## Examples

### Positive
{
  "symbol": "AAPL",
  "asset_type": "equity",
  "metrics": {
    "trailingPE": 28.5, "priceToBook": 42.1,
    "revenueGrowth": 0.09, "earningsGrowth": 0.07, "ROE": 1.47
  },
  "peer_comparison": {
    "MSFT": {"trailingPE": 35.2, "ROE": 0.38},
    "GOOGL": {"trailingPE": 26.1, "ROE": 0.29}
  },
  "fundamental_score": 0.72,
  "data_source": "yfinance"
}

### Negative (what NOT to do)
{
  "symbol": "AAPL",
  "asset_type": "stock",
  "metrics": {"vibe": 10.0},
  "peer_comparison": {},
  "fundamental_score": 9.9,
  "data_source": "my gut"
}

Problems:
- `asset_type == "stock"` is not an allowed literal.
- `metrics` contains a made-up "vibe" field, none of the required
  equity metrics.
- `peer_comparison` is empty for an equity asset.
- `fundamental_score == 9.9` is outside [0, 1].
- `data_source == "my gut"` is not a real provider.
"""


async def fundamental_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Perform fundamental analysis on the collected asset data.

    Args:
        state: Current agent state (must contain market_snapshot in metadata).

    Returns:
        Partial state update with ``FundamentalAnalysis`` in
        ``metadata["financial_analyst"]["fundamental_analysis"]``.
    """
    with otel.start_span("financial_analyst.fundamental_analyst") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "fundamental_analyst")

        fin: dict[str, Any] = dict(
            state.get("metadata", {}).get("financial_analyst", {})
        )
        snapshot = fin.get("market_snapshot", {})
        symbol = snapshot.get("symbol", "UNKNOWN")
        asset_type: str = snapshot.get("asset_type", "equity")

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
            analysis = FundamentalAnalysis.model_validate(data)
        except Exception:
            analysis = FundamentalAnalysis(
                symbol=symbol,
                asset_type=asset_type,  # type: ignore[arg-type]
            )

        fin["fundamental_analysis"] = analysis.model_dump()

        logger.info(
            "fundamental_analyst.analysis_complete",
            symbol=analysis.symbol,
            score=analysis.fundamental_score,
            metrics_count=len(analysis.metrics),
        )
        span.set_attribute(
            "lightagent.financial.fundamental_score", analysis.fundamental_score
        )

        return {
            "current_agent": "fundamental_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"Fundamental analysis for {analysis.symbol}: "
                        f"score={analysis.fundamental_score:.2f}, "
                        f"{len(analysis.metrics)} metrics, "
                        f"{len(analysis.peer_comparison)} peers compared"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
