# Prompt constants contain long JSON example lines.
"""
Technical Analyst agent node for the financial_analyst subgraph.

Computes RSI, MACD, Bollinger Bands, SMA/EMA, Stochastic, ADX. Stores a
:class:`~lightagent.agents.subgraphs.financial.artifacts.TechnicalAnalysis`
under ``state["metadata"]["financial_analyst"]["technical_analysis"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from prismal.agents.subgraphs.financial.artifacts import TechnicalAnalysis
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.financial.technical_analyst")
otel = OTelManager()

# Phase 39 / SPEC-041 AC-041-1: the seven canonical indicator families
# used to compute ``TechnicalAnalysis.data_confidence``. Each tuple is a
# group of substrings that all match the same family — we count a
# family as "present" when ANY of its substrings is found as a prefix
# of an indicator key (case-insensitive). This tolerates the LLM
# emitting ``BB_upper``/``BB_lower`` (two entries, one family) or
# ``MACD``/``MACD_signal`` without inflating the score.
_TECHNICAL_INDICATOR_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("rsi",),
    ("macd",),
    ("bb", "bollinger"),
    ("sma",),
    ("ema",),
    ("stoch",),
    ("adx",),
)


def _score_technical_indicators(indicators: dict[str, float]) -> float:
    """Return ``data_confidence`` as the fraction of indicator families present.

    The denominator is fixed at 7 — one per family in
    :data:`_TECHNICAL_INDICATOR_FAMILIES` — so an empty ``indicators``
    dict scores 0.0 and a dict covering all seven families scores 1.0.
    Extra indicators beyond the canonical set do not increase the
    score; the goal is to reward *breadth*, not quantity.

    Args:
        indicators: The ``TechnicalAnalysis.indicators`` mapping just
            produced by the LLM.

    Returns:
        A confidence score in ``[0.0, 1.0]``.
    """
    if not indicators:
        return 0.0

    lowered_keys = [key.lower() for key in indicators]
    present_families = 0
    for family_aliases in _TECHNICAL_INDICATOR_FAMILIES:
        for alias in family_aliases:
            if any(key.startswith(alias) for key in lowered_keys):
                present_families += 1
                break

    return present_families / len(_TECHNICAL_INDICATOR_FAMILIES)


_SYSTEM = """You are a Technical Analyst for the financial subgraph.

## Purpose
Compute and interpret standard technical indicators on the upstream
`MarketSnapshot` OHLCV data and emit a `TechnicalAnalysis` with
signals, trend, and support/resistance levels.

## Input
One AIMessage containing the JSON dump of the upstream `MarketSnapshot`
from `state.metadata.financial_analyst.market_snapshot`, plus the OHLCV
frame already persisted by the collector.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `TechnicalAnalysis` Pydantic schema:

    {
      "symbol": "AAPL",
      "indicators": {
        "RSI": 62.5,
        "MACD": 0.42,
        "MACD_signal": 0.35,
        "BB_upper": 180.0,
        "BB_lower": 165.0,
        "SMA_20": 172.1,
        "EMA_20": 173.5
      },
      "signals": ["RSI neutral (30-70 band)", "MACD bullish crossover"],
      "chart_paths": ["data/workspace/financial/AAPL/technical/rsi.png"],
      "trend": "bullish",                  // one of bullish|bearish|neutral|unknown
      "support_level": 168.0,
      "resistance_level": 182.0
    }

## Success Criteria
The `TechnicalAnalysis` is acceptable when ALL of the following hold:
- **Minimum indicators**: `indicators` has at least 5 entries drawn
  from {RSI, MACD, MACD_signal, BB_upper, BB_lower, SMA_20, EMA_20,
  ATR, OBV, Stoch_K}.
- **Trend literal**: one of `bullish`, `bearish`, `neutral`,
  `unknown`.
- **Signal grounding**: every entry in `signals` corresponds to a
  value in `indicators` (e.g. "RSI overbought" requires RSI > 70).
- **Support < current < resistance** when all three are set.
- **Chart paths valid**: under
  `data/workspace/financial/{symbol}/technical/`.
- **No trading calls**: never produce buy/sell recommendations —
  describe signals only.

## Instructions
1. Load OHLCV from the path in the upstream MarketSnapshot.
2. Compute indicators (lazy-import `pandas_ta`).
3. Generate plain-language signals grounded in the indicator values.
4. Classify the trend using SMA_20 vs price + MACD sign.
5. Compute naive support/resistance (recent swing low/high).
6. Persist at least one chart under the workspace path.
7. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/financial/artifacts.py::TechnicalAnalysis`.
- Lazy-import `pandas_ta`, `matplotlib`.
- Pipeline is read-only analysis — never execute trades.

## Examples

### Positive
{
  "symbol": "AAPL",
  "indicators": {
    "RSI": 62.5, "MACD": 0.42, "MACD_signal": 0.35,
    "BB_upper": 180.0, "BB_lower": 165.0,
    "SMA_20": 172.1, "EMA_20": 173.5, "ATR": 2.3
  },
  "signals": [
    "RSI at 62.5 — neutral, not yet overbought",
    "MACD 0.42 above signal 0.35 — bullish crossover",
    "Price above SMA_20 and EMA_20 — short-term uptrend"
  ],
  "chart_paths": [
    "data/workspace/financial/AAPL/technical/rsi_macd.png",
    "data/workspace/financial/AAPL/technical/bollinger.png"
  ],
  "trend": "bullish",
  "support_level": 168.0,
  "resistance_level": 182.0
}

### Negative (what NOT to do)
{
  "symbol": "AAPL",
  "indicators": {"RSI": 62.5},
  "signals": ["BUY NOW", "sell everything"],
  "chart_paths": ["/tmp/chart.png"],
  "trend": "moon",
  "support_level": 200.0,
  "resistance_level": 150.0
}

Problems:
- Only 1 indicator — below the minimum of 5.
- Signals are buy/sell recommendations — forbidden by Phase 27 rules.
- `trend == "moon"` is not an allowed literal.
- `support_level > resistance_level` — logically impossible.
- `chart_paths` escapes the workspace.
"""


async def technical_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Run technical analysis on the collected market data.

    Args:
        state: Current agent state (must contain market_snapshot in metadata).

    Returns:
        Partial state update with ``TechnicalAnalysis`` in
        ``metadata["financial_analyst"]["technical_analysis"]``.
    """
    with otel.start_span("financial_analyst.technical_analyst") as span:
        span.set_attribute("lightagent.subgraph", "financial_analyst")
        span.set_attribute("lightagent.agent", "technical_analyst")

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
            analysis = TechnicalAnalysis.model_validate(data)
        except Exception:
            analysis = TechnicalAnalysis(symbol=symbol)

        # Phase 39 / SPEC-041 AC-041-1: stamp the analysis with
        # ``data_confidence`` so the builder's technical-confidence
        # gate can decide whether to run fundamental analysis or skip
        # straight to risk/sentiment with a limited-data disclaimer.
        confidence = _score_technical_indicators(analysis.indicators)
        analysis = analysis.model_copy(update={"data_confidence": confidence})

        fin["technical_analysis"] = analysis.model_dump()

        logger.info(
            "technical_analyst.analysis_complete",
            symbol=analysis.symbol,
            trend=analysis.trend,
            indicator_count=len(analysis.indicators),
            signal_count=len(analysis.signals),
            data_confidence=confidence,
        )
        span.set_attribute("lightagent.financial.symbol", analysis.symbol)
        span.set_attribute("lightagent.financial.trend", analysis.trend)

        return {
            "current_agent": "technical_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"Technical analysis for {analysis.symbol}: "
                        f"trend={analysis.trend}, "
                        f"{len(analysis.indicators)} indicators computed, "
                        f"signals: {', '.join(analysis.signals[:3]) or 'none'}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "financial_analyst": fin},
        }
