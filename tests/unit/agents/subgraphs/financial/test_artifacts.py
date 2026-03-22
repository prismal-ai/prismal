"""Unit tests for financial analyst typed Pydantic v2 artifacts."""

from __future__ import annotations

import pytest
from lightagent.agents.subgraphs.financial.artifacts import (
    FundamentalAnalysis,
    FinancialReport,
    MarketSnapshot,
    RiskSentimentReport,
    TechnicalAnalysis,
)


def test_market_snapshot_defaults() -> None:
    """Test MarketSnapshot default field values."""
    snap = MarketSnapshot(symbol="AAPL", asset_type="equity")
    assert snap.symbol == "AAPL"
    assert snap.asset_type == "equity"
    assert snap.current_price == 0.0
    assert snap.ohlcv_path is None


def test_market_snapshot_invalid_asset_type() -> None:
    """Test MarketSnapshot rejects invalid asset_type values."""
    with pytest.raises(Exception):
        MarketSnapshot(symbol="X", asset_type="invalid")  # type: ignore[arg-type]


def test_technical_analysis_defaults() -> None:
    """Test TechnicalAnalysis default field values."""
    ta = TechnicalAnalysis(symbol="BTC-USD")
    assert ta.symbol == "BTC-USD"
    assert ta.indicators == {}
    assert ta.signals == []
    assert ta.chart_paths == []


def test_fundamental_analysis_defaults() -> None:
    """Test FundamentalAnalysis default field values."""
    fa = FundamentalAnalysis(symbol="AAPL", asset_type="equity")
    assert fa.asset_type == "equity"
    assert fa.metrics == {}


def test_risk_sentiment_report_primary_score_bounds() -> None:
    """Test RiskSentimentReport sentiment_score is within [0.0, 1.0]."""
    r = RiskSentimentReport(symbol="ETH")
    assert 0.0 <= r.sentiment_score <= 1.0


def test_financial_report_contains_disclaimer() -> None:
    """Test FinancialReport accepts and stores a custom disclaimer."""
    report = FinancialReport(
        symbol="AAPL",
        report_mode="single_asset",
        executive_summary="Strong buy signal",
        disclaimer="This analysis is for informational purposes only and does not constitute financial advice.",
    )
    assert "informational purposes only" in report.disclaimer


def test_financial_report_default_disclaimer_present() -> None:
    """FinancialReport default disclaimer is always set."""
    report = FinancialReport(symbol="TEST", report_mode="single_asset")
    assert "informational purposes only" in report.disclaimer


def test_all_5_artifacts_importable() -> None:
    """Test all 5 artifact classes are importable from the financial package."""
    from lightagent.agents.subgraphs.financial.artifacts import (
        FundamentalAnalysis,
        FinancialReport,
        MarketSnapshot,
        RiskSentimentReport,
        TechnicalAnalysis,
    )

    assert all([MarketSnapshot, TechnicalAnalysis, FundamentalAnalysis, RiskSentimentReport, FinancialReport])