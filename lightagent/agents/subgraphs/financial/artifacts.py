"""Typed Pydantic v2 artifact models for the financial analyst subgraph.

Each artifact represents structured data produced by a financial_analyst agent node
and stored in ``AgentState.metadata["financial_analyst"]``.  Agents must never
pass raw dicts between nodes — use these models and call ``.model_dump()``
when persisting to metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_DISCLAIMER = (
    "This analysis is for informational purposes only and does not "
    "constitute financial advice."
)


class MarketSnapshot(BaseModel):
    """Market data collector artifact: current price and OHLCV metadata."""

    symbol: str = Field(..., description="Ticker symbol (e.g. 'AAPL', 'BTC-USD')")
    asset_type: Literal["equity", "crypto", "forex"] = Field(
        ..., description="Asset class"
    )
    current_price: float = Field(
        default=0.0, ge=0.0, description="Latest closing price"
    )
    currency: str = Field(default="USD", description="Price currency")
    ohlcv_path: str | None = Field(
        default=None, description="Path to saved OHLCV CSV/Parquet"
    )
    data_provider: str = Field(
        default="yfinance", description="Data source used (yfinance, ccxt, openbb)"
    )
    data_points_count: int = Field(
        default=0, ge=0, description="Number of OHLCV data points fetched"
    )
    market_cap: float | None = Field(
        default=None, description="Market capitalisation USD"
    )
    volume_24h: float | None = Field(
        default=None, description="24-hour trading volume"
    )


class TechnicalAnalysis(BaseModel):
    """Technical analyst artifact: computed indicators and trading signals."""

    symbol: str = Field(..., description="Ticker symbol")
    indicators: dict[str, float] = Field(
        default_factory=dict,
        description="Indicator name to latest value (e.g. RSI=68.3, MACD=0.42)",
    )
    signals: list[str] = Field(
        default_factory=list,
        description=(
            "Detected signals (e.g. 'RSI overbought', 'MACD bullish crossover')"
        ),
    )
    chart_paths: list[str] = Field(
        default_factory=list,
        description="Paths to generated indicator charts",
    )
    trend: Literal["bullish", "bearish", "neutral", "unknown"] = Field(
        default="unknown", description="Overall trend assessment"
    )
    support_level: float | None = Field(
        default=None, description="Nearest support price"
    )
    resistance_level: float | None = Field(
        default=None, description="Nearest resistance price"
    )


class FundamentalAnalysis(BaseModel):
    """Fundamental analyst artifact: valuation and financial health metrics."""

    symbol: str = Field(..., description="Ticker symbol")
    asset_type: Literal["equity", "crypto", "forex"] = Field(
        ..., description="Asset class"
    )
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Metric name to value"
            " (P/E, P/B, EPS, revenue_growth, TVL, active_addresses...)"
        ),
    )
    peer_comparison: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Peer symbol to their key metrics for comparison",
    )
    fundamental_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Composite fundamental score [0.0-1.0]",
    )
    data_source: str = Field(
        default="yfinance", description="Primary data source used"
    )


class RiskSentimentReport(BaseModel):
    """Risk and sentiment analyst artifact: risk metrics and market sentiment."""

    symbol: str = Field(..., description="Ticker symbol")
    volatility_annual: float = Field(
        default=0.0, ge=0.0, description="Annualised volatility (decimal, e.g. 0.25)"
    )
    sharpe_ratio: float | None = Field(
        default=None, description="Sharpe ratio (risk-adjusted return)"
    )
    max_drawdown: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Maximum drawdown fraction [0.0-1.0]",
    )
    var_95: float = Field(
        default=0.0, description="95% Value-at-Risk (1-day, decimal fraction)"
    )
    sentiment_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Market sentiment [0.0 bearish - 1.0 bullish]",
    )
    sentiment_sources: list[str] = Field(
        default_factory=list,
        description="Sources used for sentiment (news, social, on-chain)",
    )
    correlation_assets: dict[str, float] = Field(
        default_factory=dict,
        description="Asset symbol to 30-day correlation coefficient",
    )
    risk_level: Literal["low", "medium", "high", "very_high"] = Field(
        default="medium", description="Overall risk assessment"
    )


class FinancialReport(BaseModel):
    """Report generator artifact: consolidated financial analysis report."""

    symbol: str = Field(..., description="Primary ticker symbol")
    report_mode: Literal["single_asset", "portfolio", "market_overview"] = Field(
        default="single_asset", description="Report type"
    )
    executive_summary: str = Field(
        default="", description="AI-generated executive summary"
    )
    sections: dict[str, str] = Field(
        default_factory=dict,
        description="Section name to Markdown content",
    )
    chart_paths: list[str] = Field(
        default_factory=list, description="Paths to all charts included in the report"
    )
    report_path: str | None = Field(
        default=None, description="Path to saved full Markdown report"
    )
    disclaimer: str = Field(
        default=_DISCLAIMER,
        description="Mandatory legal disclaimer — always present",
    )


__all__ = [
    "_DISCLAIMER",
    "FinancialReport",
    "FundamentalAnalysis",
    "MarketSnapshot",
    "RiskSentimentReport",
    "TechnicalAnalysis",
]
