"""Unit tests for tools_finance.py — all external API calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_fetch_price_history_graceful_on_api_error() -> None:
    """Returns stub dict when yfinance raises an exception."""
    from lightagent.agents.subgraphs.financial.tools_finance import fetch_price_history

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.side_effect = RuntimeError("API error")

    with patch.dict("sys.modules", {"yfinance": mock_yf}):
        result = fetch_price_history("AAPL")

    assert result["symbol"] == "AAPL"
    assert result["current_price"] == 0.0


def test_fetch_price_history_returns_stub_on_empty_history() -> None:
    """Returns stub dict when yfinance returns empty DataFrame."""
    import pandas as pd

    from lightagent.agents.subgraphs.financial.tools_finance import fetch_price_history

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = pd.DataFrame()

    with patch.dict("sys.modules", {"yfinance": mock_yf}):
        result = fetch_price_history("AAPL")

    assert result["symbol"] == "AAPL"
    assert result["current_price"] == 0.0


def test_fetch_crypto_ohlcv_graceful_on_ccxt_error() -> None:
    """Returns stub dict when ccxt raises an exception."""
    from lightagent.agents.subgraphs.financial.tools_finance import fetch_crypto_ohlcv

    mock_ccxt = MagicMock()
    mock_ccxt.binance.return_value.fetch_ticker.side_effect = RuntimeError("conn error")

    with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
        result = fetch_crypto_ohlcv("BTC/USDT")

    assert result["symbol"] == "BTC/USDT"
    assert result["current_price"] == 0.0


def test_compute_indicators_returns_empty_on_exception() -> None:
    """Returns empty dict when yfinance raises exception in indicator computation."""
    from lightagent.agents.subgraphs.financial.tools_finance import compute_indicators

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.side_effect = RuntimeError("network error")
    mock_ta = MagicMock()

    with patch.dict("sys.modules", {"yfinance": mock_yf, "pandas_ta": mock_ta}):
        result = compute_indicators("AAPL")

    assert result == {}


def test_fetch_fundamentals_returns_empty_for_crypto() -> None:
    """Crypto fundamentals not available via yfinance — returns empty."""
    from lightagent.agents.subgraphs.financial.tools_finance import fetch_fundamentals

    result = fetch_fundamentals("BTC-USD", asset_type="crypto")
    assert result == {}


def test_compute_risk_metrics_graceful_on_error() -> None:
    """Returns empty dict when yfinance raises exception."""
    from lightagent.agents.subgraphs.financial.tools_finance import compute_risk_metrics

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.side_effect = RuntimeError("network error")
    mock_np = MagicMock()

    with patch.dict("sys.modules", {"yfinance": mock_yf, "numpy": mock_np}):
        result = compute_risk_metrics("AAPL")

    assert result == {}


def test_disclaimer_constant_in_tools_finance() -> None:
    """The legal disclaimer constant is accessible from tools_finance."""
    from lightagent.agents.subgraphs.financial.tools_finance import _DISCLAIMER

    assert "informational purposes only" in _DISCLAIMER


def test_stub_price_returns_zeros() -> None:
    """_stub_price returns zero-value dict."""
    from lightagent.agents.subgraphs.financial.tools_finance import _stub_price

    result = _stub_price("TEST", "stub")
    assert result["current_price"] == 0.0
    assert result["symbol"] == "TEST"
