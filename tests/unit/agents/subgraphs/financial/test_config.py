"""Unit tests for financial analyst configuration."""

from __future__ import annotations


def test_financial_settings_accessible() -> None:
    """Financial settings block is accessible from core config."""
    from prismal.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "financial_default_provider")
    assert hasattr(settings, "financial_cache_ttl_ticker")
    assert hasattr(settings, "financial_cache_ttl_ohlcv")
    assert hasattr(settings, "financial_cache_ttl_fundamentals")
    assert hasattr(settings, "financial_workspace_path")
    assert hasattr(settings, "financial_trade_execution_enabled")


def test_financial_trade_execution_disabled_by_default() -> None:
    """Trade execution must be disabled by default (Phase 27 read-only rule)."""
    from prismal.core.config import get_settings

    assert get_settings().financial_trade_execution_enabled is False


def test_financial_cache_ttl_defaults() -> None:
    """Cache TTL defaults match spec (ticker: 30s, ohlcv: 300s, fundamentals: 86400s)."""
    from prismal.core.config import get_settings

    s = get_settings()
    assert s.financial_cache_ttl_ticker == 30
    assert s.financial_cache_ttl_ohlcv == 300
    assert s.financial_cache_ttl_fundamentals == 86400
