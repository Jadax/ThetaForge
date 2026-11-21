"""Market direction filter: trade with the market, not against it.

Oct 26 - Nov 20 2026, SPY fell 9.9% (QQQ -14.6%) while the engine, built
long, kept buying momentum stocks and selling puts: 17 stock longs were
stopped out for -$866, two-thirds of all losses. Trend followers and
O'Neil's "M" (market direction) rule handle this the same way: when the
index is below its 50-day average, new longs and bullish option structures
wait; the book sits mostly in cash and only bearish/neutral premium is sold.

One read of SPY vs its 50-day SMA, computed once per scan and shared (the
Event-Loop Rule: market-wide inputs are fetched once, never per symbol).
Missing data fails OPEN -- no read means no veto -- like every soft
enrichment in this codebase; it never fabricates a downtrend.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MARKET_SYMBOL = "SPY"
SMA_DAYS = 50
CACHE_TTL_SECONDS = 1800

# Structures that need the market to hold up. Covered calls are written
# against shares already owned, so they stay allowed (they reduce delta).
BULLISH_STRATEGIES = {
    "cash_secured_put", "csp", "wheel", "wheel_csp",
    "bull_put", "bull_put_credit",
    "call_debit", "call_debit_spread", "bull_call_spread",
    "long_call", "leaps",
}

DOWNTREND = "downtrend"
UPTREND = "uptrend"


def market_trend(closes: List[float]) -> Optional[Dict[str, Any]]:
    """SPY's last close against its 50-day simple moving average."""
    values = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(values) < SMA_DAYS:
        return None
    sma = sum(values[-SMA_DAYS:]) / SMA_DAYS
    last = values[-1]
    return {
        "symbol": MARKET_SYMBOL,
        "state": UPTREND if last >= sma else DOWNTREND,
        "close": round(last, 2),
        "sma50": round(sma, 2),
        "pct_vs_sma50": round((last / sma - 1) * 100, 2),
    }


def trend_state(trend: Any) -> Optional[str]:
    """Accept a read dict or a bare state string."""
    if isinstance(trend, dict):
        return trend.get("state")
    return trend if trend in (UPTREND, DOWNTREND) else None


def blocks(strategy: Any, trend: Any) -> bool:
    """True when this strategy fights a market downtrend."""
    return trend_state(trend) == DOWNTREND and str(strategy or "").lower() in BULLISH_STRATEGIES


_CACHE: Dict[str, Any] = {"at": 0.0, "trend": None}


async def get_market_trend(provider) -> Optional[Dict[str, Any]]:
    """Cached market read (one SPY history fetch per CACHE_TTL_SECONDS)."""
    if time.time() - _CACHE["at"] < CACHE_TTL_SECONDS:
        return _CACHE["trend"]
    trend = None
    try:
        hist = await provider.get_historical_prices(MARKET_SYMBOL, period="6mo")
        closes = hist["Close"].tolist() if hist is not None and hasattr(hist, "Close") else []
        trend = market_trend(closes)
    except Exception:
        logger.warning("SPY history unavailable; market direction filter off this cycle")
    _CACHE["at"] = time.time()
    _CACHE["trend"] = trend
    return trend
