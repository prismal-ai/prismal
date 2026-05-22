"""Financial analysis tools for the financial_analyst subgraph.

All external library imports (yfinance, ccxt, openbb, pandas_ta, numpy) are
lazy — guarded by try/except ImportError — so the base package works without
the [finance] extra installed.

NEVER import these libraries at module level outside of
prismal/agents/subgraphs/financial/.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import structlog

logger = structlog.get_logger("prismal.subgraphs.financial.tools")

_DISCLAIMER = (
    "\n\n---\n*This analysis is for informational purposes only and does not "
    "constitute financial advice.*"
)


def fetch_price_history(symbol: str, days: int = 180) -> dict[str, Any]:
    """Fetch OHLCV price history for a symbol via yfinance (primary) or stub.

    Args:
        symbol: Ticker symbol (e.g. 'AAPL', 'BTC-USD').
        days: Number of calendar days of history to fetch.

    Returns:
        Dict with keys: symbol, current_price, data_points_count, provider, currency.
    """
    try:
        import yfinance as yf  # lazy import — finance extra only

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=f"{days}d")
        if hist.empty:
            return _stub_price(symbol, "yfinance")
        current_price = float(hist["Close"].iloc[-1])
        info = ticker.fast_info
        currency = getattr(info, "currency", "USD") or "USD"
        return {
            "symbol": symbol,
            "current_price": current_price,
            "data_points_count": len(hist),
            "provider": "yfinance",
            "currency": currency,
            "market_cap": getattr(info, "market_cap", None),
        }
    except ImportError:
        logger.warning("tools_finance.yfinance_not_installed")
        return _stub_price(symbol, "stub")
    except Exception as exc:
        logger.warning("tools_finance.fetch_failed", symbol=symbol, error=str(exc))
        return _stub_price(symbol, "stub")


def fetch_crypto_ohlcv(symbol: str, exchange: str = "binance") -> dict[str, Any]:
    """Fetch real-time crypto OHLCV via CCXT.

    API keys are read from environment variables only (BINANCE_API_KEY, etc.).
    Never stored in config files or logs.

    Args:
        symbol: CCXT symbol format (e.g. 'BTC/USDT').
        exchange: CCXT exchange id (default: binance).

    Returns:
        Dict with current price and data_points_count.
    """
    try:
        import ccxt  # lazy import — optional

        api_key = os.environ.get(f"{exchange.upper()}_API_KEY")
        api_secret = os.environ.get(f"{exchange.upper()}_SECRET")
        ex = getattr(ccxt, exchange)({"apiKey": api_key, "secret": api_secret})
        ticker = ex.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "current_price": float(ticker.get("last", 0.0)),
            "data_points_count": 1,
            "provider": f"ccxt/{exchange}",
            "currency": "USDT",
            "volume_24h": float(ticker.get("quoteVolume", 0.0)),
        }
    except ImportError:
        logger.warning("tools_finance.ccxt_not_installed")
        return _stub_price(symbol, "stub")
    except Exception as exc:
        logger.warning("tools_finance.ccxt_failed", symbol=symbol, error=str(exc))
        return _stub_price(symbol, "stub")


def compute_indicators(symbol: str, days: int = 180) -> dict[str, float]:
    """Compute RSI, MACD, Bollinger Bands, SMA20, EMA20 via pandas-ta.

    Args:
        symbol: Ticker symbol.
        days: History window for indicator computation.

    Returns:
        Dict of indicator name to latest value. Empty dict if libs unavailable.
    """
    try:
        import pandas_ta as ta  # lazy import — optional
        import yfinance as yf  # lazy import — optional

        hist = yf.Ticker(symbol).history(period=f"{days}d")
        if hist.empty or len(hist) < 14:
            return {}
        close = hist["Close"]
        indicators: dict[str, float] = {}
        rsi = ta.rsi(close, length=14)
        if rsi is not None and not rsi.empty:
            indicators["RSI"] = float(rsi.iloc[-1])
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            indicators["MACD"] = float(macd_df.iloc[-1, 0])
            indicators["MACD_signal"] = float(macd_df.iloc[-1, 2])
        bb_df = ta.bbands(close, length=20, std=2.0)
        if bb_df is not None and not bb_df.empty:
            indicators["BB_upper"] = float(bb_df.iloc[-1, 0])
            indicators["BB_lower"] = float(bb_df.iloc[-1, 2])
            indicators["BB_mid"] = float(bb_df.iloc[-1, 1])
        sma = ta.sma(close, length=20)
        if sma is not None:
            indicators["SMA_20"] = float(sma.iloc[-1])
        ema = ta.ema(close, length=20)
        if ema is not None:
            indicators["EMA_20"] = float(ema.iloc[-1])
        return indicators
    except ImportError:
        logger.warning("tools_finance.pandas_ta_not_installed")
        return {}
    except Exception as exc:
        logger.warning("tools_finance.indicators_failed", symbol=symbol, error=str(exc))
        return {}


def fetch_fundamentals(symbol: str, asset_type: str = "equity") -> dict[str, float]:
    """Fetch fundamental metrics via yfinance (equity) or return empty (crypto).

    Args:
        symbol: Ticker symbol.
        asset_type: 'equity' or 'crypto'.

    Returns:
        Dict of metric name to float value. Empty for non-equity assets.
    """
    if asset_type != "equity":
        return {}
    try:
        import yfinance as yf  # lazy import — optional

        info = yf.Ticker(symbol).info
        metrics: dict[str, float] = {}
        for key in (
            "trailingPE",
            "priceToBook",
            "revenueGrowth",
            "earningsGrowth",
            "returnOnEquity",
            "debtToEquity",
            "currentRatio",
        ):
            val = info.get(key)
            if val is not None:
                with contextlib.suppress(TypeError, ValueError):
                    metrics[key] = float(val)
        return metrics
    except ImportError:
        logger.warning("tools_finance.yfinance_not_installed")
        return {}
    except Exception as exc:
        logger.warning("tools_finance.fundamentals_failed", symbol=symbol, error=str(exc))
        return {}


def compute_risk_metrics(symbol: str, days: int = 252) -> dict[str, float]:
    """Compute volatility, Sharpe ratio, max drawdown, and 95% VaR.

    Args:
        symbol: Ticker symbol.
        days: Trading days of history (default 252 = ~1 year).

    Returns:
        Dict with volatility_annual, sharpe_ratio, max_drawdown, var_95.
    """
    try:
        import numpy as np  # lazy import — optional
        import yfinance as yf  # lazy import — optional

        hist = yf.Ticker(symbol).history(period=f"{days}d")
        if hist.empty or len(hist) < 5:
            return {}
        close = hist["Close"]
        returns = close.pct_change().dropna()
        vol = float(returns.std() * np.sqrt(252))
        mean_ret = float(returns.mean() * 252)
        sharpe = (mean_ret / vol) if vol > 0 else 0.0
        cumulative = (1 + returns).cumprod()
        peak = cumulative.cummax()
        drawdown = (cumulative - peak) / peak
        max_dd = float(abs(drawdown.min()))
        var_95 = float(abs(returns.quantile(0.05)))
        return {
            "volatility_annual": vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": min(max_dd, 1.0),
            "var_95": var_95,
        }
    except ImportError:
        logger.warning("tools_finance.numpy_not_installed")
        return {}
    except Exception as exc:
        logger.warning("tools_finance.risk_failed", symbol=symbol, error=str(exc))
        return {}


def _stub_price(symbol: str, provider: str) -> dict[str, Any]:
    """Return a zero-value stub when real data is unavailable.

    Args:
        symbol: Ticker symbol.
        provider: Provider name to record in the stub.

    Returns:
        Dict with zeroed-out price fields.
    """
    return {
        "symbol": symbol,
        "current_price": 0.0,
        "data_points_count": 0,
        "provider": provider,
        "currency": "USD",
    }


FINANCIAL_TOOLS_NAMES = [
    "fetch_price_history",
    "fetch_crypto_ohlcv",
    "compute_indicators",
    "fetch_fundamentals",
    "compute_risk_metrics",
]

__all__ = [*FINANCIAL_TOOLS_NAMES, "_DISCLAIMER", "_stub_price"]
