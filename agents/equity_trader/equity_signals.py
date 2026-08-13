"""
Equity signal math for the general (stock/ETF) trader.

Pure, self-contained indicators computed from daily OHLCV plus momentum /
relative-strength helpers, reusing ``SignalEngine`` for RSI/ADX/MACD so the
technical math stays in exactly one place. These functions never hit the
network and never decide anything by themselves -- they produce numbers the
EquityBrain turns into a gated recommendation.
"""
from typing import List, Optional


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder's Average True Range for the latest bar, or None when unusable."""
    if not highs or len(highs) != len(lows) or len(lows) != len(closes):
        return None
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    value = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value if value == value and value > 0 else None


def sma(closes: List[float], period: int) -> Optional[float]:
    """Simple moving average over the latest *period* closes."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def rate_of_change(closes: List[float], lookback: int) -> Optional[float]:
    """Return over *lookback* bars as a fraction, or None when insufficient."""
    if len(closes) < lookback + 1:
        return None
    start = closes[-lookback - 1]
    if not start:
        return None
    return closes[-1] / start - 1


def volume_ratio(volumes: List[float], lookback: int = 20) -> Optional[float]:
    """Latest bar volume divided by the prior *lookback* average."""
    if len(volumes) < lookback + 1:
        return None
    prior = volumes[-lookback - 1: -1]
    average = sum(prior) / len(prior)
    if not average:
        return None
    return volumes[-1] / average


def highest_high(highs: List[float], lookback: int) -> Optional[float]:
    if not highs or len(highs) < lookback:
        return None
    return max(highs[-lookback:])


def broke_recent_high(closes: List[float], lookback: int = 20, window: int = 3) -> bool:
    """True when the close broke the prior *lookback*-bar high within *window*
    bars (a breakout momentum read), never false on thin data."""
    if len(closes) < lookback + window + 1:
        return False
    prior = max(closes[-(lookback + window): -window])
    return any(close > prior for close in closes[-window:])


def chandelier_stop(highest_high: float, atr_value: float, multiplier: float = 2.0) -> Optional[float]:
    """Trailing stop a fixed ATR multiple below the highest high since entry."""
    if not atr_value or atr_value <= 0:
        return None
    return highest_high - multiplier * atr_value


def relative_strength(symbol_return: Optional[float], benchmark_return: Optional[float]) -> Optional[float]:
    """Symbol return minus benchmark return; None when either side is missing."""
    if symbol_return is None or benchmark_return is None:
        return None
    return symbol_return - benchmark_return
