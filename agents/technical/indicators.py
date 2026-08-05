"""
Technical Analysis Indicators for options strategy confirmation.
Uses the 'ta' library (free) and custom implementations.
Adapted from general technical analysis frameworks used by
Simpler Trading, TheoTrade, and institutional quantitative desks.
"""
import logging
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalEngine:
    """
    Calculates technical indicators for underlying price confirmation.
    Used as Layer 4 in the multi-layer scanner pipeline.
    """

    @staticmethod
    def calculate_all_indicators(df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate a comprehensive set of technical indicators."""
        if df.empty or len(df) < 20:
            return {"error": "Insufficient data"}

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        indicators = {}
        indicators["trend"] = TechnicalEngine._get_trend(close)
        indicators["rsi"] = TechnicalEngine._rsi(close, 14)
        indicators["macd"] = TechnicalEngine._macd(close)
        indicators["bollinger"] = TechnicalEngine._bollinger_bands(close, 20, 2)
        indicators["atr"] = TechnicalEngine._atr(high, low, close, 14)
        indicators["sma_20"] = float(close.rolling(20).mean().iloc[-1])
        indicators["sma_50"] = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        indicators["sma_200"] = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
        indicators["current_price"] = float(close.iloc[-1])
        indicators["volume_avg_20"] = float(volume.rolling(20).mean().iloc[-1])
        indicators["volume_ratio"] = float(volume.iloc[-1] / max(indicators["volume_avg_20"], 1))

        # Support/Resistance from recent pivots
        indicators["support"], indicators["resistance"] = TechnicalEngine._pivot_points(
            high, low, close
        )

        return indicators

    @staticmethod
    def _get_trend(close: pd.Series) -> str:
        """Determine trend using moving average alignment."""
        sma_20 = close.rolling(20).mean()
        sma_50 = close.rolling(50).mean()

        if len(close) < 50:
            return "NEUTRAL"

        current = close.iloc[-1]
        s20 = sma_20.iloc[-1]
        s50 = sma_50.iloc[-1]

        if current > s20 > s50:
            return "STRONG_BULLISH"
        elif current > s20:
            return "BULLISH"
        elif current < s20 < s50:
            return "STRONG_BEARISH"
        elif current < s20:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float:
        """Calculate RSI."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi.iloc[-1]), 2)

    @staticmethod
    def _macd(close: pd.Series) -> Dict[str, float]:
        """Calculate MACD."""
        ema_12 = close.ewm(span=12).mean()
        ema_26 = close.ewm(span=26).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal_line

        return {
            "macd": round(float(macd_line.iloc[-1]), 4),
            "signal": round(float(signal_line.iloc[-1]), 4),
            "histogram": round(float(histogram.iloc[-1]), 4),
            "bullish": float(histogram.iloc[-1]) > 0,
        }

    @staticmethod
    def _bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2) -> Dict[str, float]:
        """Calculate Bollinger Bands."""
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)

        current = close.iloc[-1]
        bb_position = (current - lower.iloc[-1]) / max(upper.iloc[-1] - lower.iloc[-1], 0.01)

        return {
            "upper": round(float(upper.iloc[-1]), 2),
            "middle": round(float(sma.iloc[-1]), 2),
            "lower": round(float(lower.iloc[-1]), 2),
            "position": round(float(bb_position), 3),
            "at_upper": bb_position > 0.95,
            "at_lower": bb_position < 0.05,
        }

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        """Calculate Average True Range."""
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        return round(float(atr.iloc[-1]), 2)

    @staticmethod
    def _pivot_points(
        high: pd.Series, low: pd.Series, close: pd.Series
    ) -> tuple:
        """Calculate support and resistance from recent price pivots."""
        recent_high = float(high.tail(20).max())
        recent_low = float(low.tail(20).min())
        current = float(close.iloc[-1])

        # Simple pivot-based S/R
        pivot = (recent_high + recent_low + current) / 3
        r1 = 2 * pivot - recent_low
        s1 = 2 * pivot - recent_high

        support = [round(s1, 2), round(recent_low, 2)]
        resistance = [round(r1, 2), round(recent_high, 2)]

        return support, resistance

