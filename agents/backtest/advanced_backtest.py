"""
Shared technical-signal implementations.

Conservative, self-contained indicator calculations (RSI, MACD, Bollinger
Bands, ADX) that the AI Brain and the technical indicators module rely on.
"""
import math
from typing import List, Tuple


class SignalEngine:
    """Dominant technical-signal calculations shared across the analysis path."""

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> List[float]:
        """Relative Strength Index."""
        if len(prices) < period + 1:
            return [50.0] * len(prices)

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [max(d, 0) for d in deltas]
        losses = [-min(d, 0) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi = [50.0] * (period + 1)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi.append(100)
            else:
                rs = avg_gain / avg_loss
                rsi.append(100 - 100 / (1 + rs))
        return rsi

    @staticmethod
    def macd(
        prices: List[float],
        fast: int = 12,
        slow: int = 26,
        signal_period: int = 9,
    ) -> Tuple[List[float], List[float], List[float]]:
        """MACD line, signal line, histogram."""
        ema_fast = SignalEngine._ema(prices, fast)
        ema_slow = SignalEngine._ema(prices, slow)

        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = SignalEngine._ema(macd_line, signal_period)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]

        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        prices: List[float],
        period: int = 20,
        num_std: float = 2.0,
    ) -> Tuple[List[float], List[float], List[float]]:
        """Upper, middle, lower Bollinger Bands."""
        if len(prices) < period:
            mid = prices[-1] if prices else 0
            return [mid] * len(prices), [mid] * len(prices), [mid] * len(prices)

        upper, middle, lower = [], [], []
        for i in range(len(prices)):
            if i < period - 1:
                window = prices[:i + 1]
            else:
                window = prices[i - period + 1: i + 1]

            mean = sum(window) / len(window)
            var = sum((p - mean) ** 2 for p in window) / len(window)
            std = math.sqrt(var)

            middle.append(mean)
            upper.append(mean + num_std * std)
            lower.append(mean - num_std * std)

        return upper, middle, lower

    @staticmethod
    def _ema(data: List[float], period: int) -> List[float]:
        """Exponential moving average."""
        if not data:
            return []
        k = 2 / (period + 1)
        ema = [data[0]]
        for i in range(1, len(data)):
            ema.append(data[i] * k + ema[-1] * (1 - k))
        return ema

    @staticmethod
    def adx(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> List[float]:
        """Average Directional Index."""
        if len(highs) < period + 1:
            return [25.0] * len(highs)

        plus_dm = []
        minus_dm = []
        tr_list = []

        for i in range(1, len(highs)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(max(up, 0) if up > down else 0)
            minus_dm.append(max(down, 0) if down > up else 0)
            tr_list.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

        atr = sum(tr_list[:period]) / period
        plus_di = sum(plus_dm[:period]) / period
        minus_di = sum(minus_dm[:period]) / period

        adx_vals = [25.0] * period
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
            plus_di = (plus_di * (period - 1) + plus_dm[i]) / period
            minus_di = (minus_di * (period - 1) + minus_dm[i]) / period

            if atr > 0:
                plus_di_pct = plus_di / atr * 100
                minus_di_pct = minus_di / atr * 100
            else:
                plus_di_pct = minus_di_pct = 0

            di_sum = plus_di_pct + minus_di_pct
            if di_sum > 0:
                dx = abs(plus_di_pct - minus_di_pct) / di_sum * 100
            else:
                dx = 0
            adx_vals.append((adx_vals[-1] * (period - 1) + dx) / period)

        return adx_vals if len(adx_vals) >= len(highs) else adx_vals + [25.0] * (len(highs) - len(adx_vals))
