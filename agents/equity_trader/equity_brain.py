"""
EquityBrain — the general (stock/ETF) trader's decision layer.

A rule-based, fail-closed brain for long equity/ETF momentum-trend trades.
It mirrors the options Brain's contract (a score plus a single best strategy
plus a no-trade reason) but its inputs are daily OHLCV and market breadth, not
option chains and IV. Every hard gate must pass before a symbol is tradeable,
and any missing input disables the relevant gate rather than fabricating a
pass -- a quiet market must never look like unavailable data or vice versa.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agents.backtest.advanced_backtest import SignalEngine
from agents.equity_trader.equity_signals import (
    atr,
    rate_of_change,
    sma,
    volume_ratio,
    broke_recent_high,
)

# A candidate must clear this score floor to be actionable; ranking alone is
# never enough (mirrors MIN_COMPOSITE_SCORE in the options recommender).
BUY_SCORE_FLOOR = 62.0
# ADX minimum: only commit capital to established trends, filter chop.
MIN_ADX = 20
# RSI band for momentum longs: confirmed-but-not-exhausted. > 80 is a chase
# warning, < 45 means momentum is not actually present.
RSI_MIN = 50
RSI_WARN_HIGH = 80
# 6-month (126-bar) return must be positive for an absolute-momentum entry.
MIN_6M_RETURN = 0.0
# Volume confirmation floor (latest bar vs 20-day average).
MIN_VOLUME_RATIO = 0.8
# A symbol within this % of its 52-week high counts as "near highs".
NEAR_52W_HIGH_PCT = 5.0


@dataclass
class EquityRead:
    """Gated read of one stock/ETF; never a fabricated trade signal."""
    symbol: str
    signal: str                      # "buy" | "no_trade"
    score: float                     # 0-100
    strategy: str                    # "equity_momentum" | "etf_rotation" | ...
    reasoning: str
    no_trade_reason: Optional[str] = None
    price: Optional[float] = None
    rsi_14: Optional[float] = None
    adx: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    above_50d: Optional[bool] = None
    above_200d: Optional[bool] = None
    momentum_1m: Optional[float] = None
    momentum_3m: Optional[float] = None
    momentum_6m: Optional[float] = None
    relative_strength: Optional[float] = None
    volume_ratio: Optional[float] = None
    percent_off_52w_high: Optional[float] = None
    atr_value: Optional[float] = None
    atr_pct: Optional[float] = None
    breakout_20d: bool = False
    market_risk_tilt: Optional[str] = None
    days_to_earnings: Optional[int] = None
    days_to_macro: Optional[int] = None
    extra: Dict[str, object] = field(default_factory=dict)


class EquityBrain:
    """Pure scoring/gating for one symbol. No I/O -- callers gather data."""

    # Strategy selection mirrors the Brain: a single best-strategy label with
    # a readable reason. Momentum-trend is the core; high-beta leaders that
    # clear every gate keep it; ETFs use the rotation label.
    STRATEGY_MOMENTUM = "equity_momentum"
    STRATEGY_ROTATION = "etf_rotation"

    def analyze(
        self,
        symbol: str,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
        benchmark_return_6m: Optional[float] = None,
        market_risk_tilt: Optional[str] = None,
        days_to_earnings: Optional[int] = None,
        days_to_macro: Optional[int] = None,
        is_etf: bool = False,
    ) -> EquityRead:
        highs = highs or closes
        lows = lows or closes
        volumes = volumes or [0.0] * len(closes)

        if len(closes) < 60:
            return self._reject(symbol, "history_unavailable",
                                "Fewer than 60 bars of usable history; no equity read.")
        price = closes[-1]
        if not price or price <= 0:
            return self._reject(symbol, "price_unavailable", "No usable latest price.")

        # ── signal inputs (all degrade to neutral when missing) ─────────
        sma_50 = sma(closes, 50)
        sma_200 = sma(closes, 200)
        rsi_values = SignalEngine.rsi(closes)
        rsi_14 = rsi_values[-1] if rsi_values else None
        adx_values = SignalEngine.adx(highs, lows, closes)
        adx = adx_values[-1] if adx_values else None
        vol_ratio = volume_ratio(volumes)
        atr_value = atr(highs, lows, closes)
        atr_pct = (atr_value / price * 100) if atr_value and price else None
        mom_1m = rate_of_change(closes, 21)
        mom_3m = rate_of_change(closes, 63)
        mom_6m = rate_of_change(closes, 126)
        breakout = broke_recent_high(closes)
        year_high = max(closes)
        off_52w = (price / year_high - 1) * 100 if year_high else None
        relative = None
        if mom_6m is not None:
            relative = mom_6m - (benchmark_return_6m or 0.0) if benchmark_return_6m is not None else mom_6m

        above_50d = bool(sma_50 and price > sma_50)
        above_200d = bool(sma_200 and price > sma_200)

        # ── hard gates (fail-closed; missing data disables, never passes) ──
        if market_risk_tilt == "risk_off":
            return self._reject(symbol, "market_risk_off",
                                "Broad tape is risk-off; no new equity longs.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Macro proximity: within the FOMC/CPI/NFP blackout window the options
        # engine refuses new premium; equities are cash-bought longs so the
        # risk is a gap against the trend rather than undefined loss. Stand
        # aside when a print lands within 2 trading days -- a surprise can gap
        # right through an ATR stop.
        if days_to_macro is not None and days_to_macro <= 2:
            return self._reject(symbol, "macro_proximity",
                                f"Major macro print in {days_to_macro}d; standing aside.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Earnings proximity: a long into an earnings print is a one-event
        # gamble on a stock. Stand aside within 3 trading days.
        if days_to_earnings is not None and days_to_earnings <= 3:
            return self._reject(symbol, "pre_earnings",
                                f"Earnings in {days_to_earnings}d; standing aside.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Absolute trend: price must be above the 200d (or 50d when the 200d
        # history is not yet available) and the 50d must be above the 200d.
        if sma_200 is not None:
            if not above_200d or not (sma_50 is not None and sma_50 > sma_200):
                return self._reject(symbol, "trend_filter",
                                    "Price/50d below the 200d (no confirmed uptrend).",
                                    read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                    above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                    relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                    breakout, market_risk_tilt, days_to_earnings,
                                                    days_to_macro))
        elif not above_50d:
            return self._reject(symbol, "trend_filter",
                                "Price below the 50d with no 200d history available.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Absolute momentum: the 6-month return must be positive. Missing data
        # degrades to neutral (no veto, no pass).
        if mom_6m is not None and mom_6m < MIN_6M_RETURN:
            return self._reject(symbol, "absolute_momentum",
                                "6-month return is negative; no absolute momentum.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Trend strength: only commit to established trends (ADX). Missing ADX
        # is neutral.
        if adx is not None and adx < MIN_ADX:
            return self._reject(symbol, "weak_trend",
                                f"ADX {adx:.0f} below {MIN_ADX}; trend too weak.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # RSI: momentum confirmation, not exhaustion. RSI > 80 is a chase
        # warning even in a trend.
        if rsi_14 is not None and rsi_14 > RSI_WARN_HIGH:
            return self._reject(symbol, "overbought",
                                f"RSI {rsi_14:.0f} above {RSI_WARN_HIGH}; no chase entries.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # Relative strength vs the market: laggards are not buy candidates even
        # when their own trend is up (IBD "L" rule).
        if relative is not None and relative < -0.05:
            return self._reject(symbol, "relative_strength",
                                "Underperforming the market by more than 5% over 6 months.",
                                read=self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                                                above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                                                relative, vol_ratio, off_52w, atr_value, atr_pct,
                                                breakout, market_risk_tilt, days_to_earnings,
                                                days_to_macro))

        # ── score ──────────────────────────────────────────────────────
        score = 0.0
        reasons = []
        if above_200d:
            score += 15
            reasons.append("above 200d")
        if sma_50 is not None and sma_50 > sma_200:
            score += 15
            reasons.append("50d > 200d")
        if adx is not None and adx >= 25:
            score += 15
            reasons.append(f"ADX {adx:.0f}")
        elif adx is not None and adx >= MIN_ADX:
            score += 8
        if rsi_14 is not None and RSI_MIN <= rsi_14 <= 75:
            score += 10
            reasons.append(f"RSI {rsi_14:.0f}")
        if mom_1m is not None and mom_1m > 0.02:
            score += 10
            reasons.append("1m positive")
        if mom_3m is not None and mom_3m > 0.05:
            score += 10
        if mom_6m is not None and mom_6m > 0.10:
            score += 10
            reasons.append("6m strong")
        if breakout:
            score += 10
            reasons.append("20d breakout")
        if off_52w is not None and off_52w > -NEAR_52W_HIGH_PCT:
            score += 5
            reasons.append("near 52w high")
        if vol_ratio is not None and vol_ratio >= MIN_VOLUME_RATIO:
            score += 5
            reasons.append("volume confirmed")
        score = round(min(score, 100.0), 1)

        read = self._read(symbol, price, rsi_14, adx, sma_50, sma_200,
                          above_50d, above_200d, mom_1m, mom_3m, mom_6m,
                          relative, vol_ratio, off_52w, atr_value, atr_pct,
                          breakout, market_risk_tilt, days_to_earnings, days_to_macro)
        if score < BUY_SCORE_FLOOR:
            read.signal = "no_trade"
            read.score = score
            read.no_trade_reason = "low_score"
            read.reasoning = f"Score {score} below the {BUY_SCORE_FLOOR} floor."
            return read

        read.signal = "buy"
        read.score = score
        read.strategy = self.STRATEGY_ROTATION if is_etf else self.STRATEGY_MOMENTUM
        read.reasoning = "Momentum-trend long: " + "; ".join(reasons) + "."
        return read

    # ── helpers ─────────────────────────────────────────────────────────

    def _reject(self, symbol: str, reason: str, message: str,
                read: Optional[EquityRead] = None) -> EquityRead:
        if read is None:
            read = EquityRead(symbol=symbol, signal="no_trade", score=0.0,
                              strategy=self.STRATEGY_MOMENTUM, reasoning=message,
                              no_trade_reason=reason)
        else:
            read.signal = "no_trade"
            read.score = 0.0
            read.no_trade_reason = reason
            read.reasoning = message
        return read

    @staticmethod
    def _read(symbol: str, price: float, rsi_14, adx, sma_50, sma_200,
              above_50d, above_200d, mom_1m, mom_3m, mom_6m, relative,
              vol_ratio, off_52w, atr_value, atr_pct, breakout, risk_tilt,
              days_to_earnings, days_to_macro) -> EquityRead:
        return EquityRead(
            symbol=symbol, signal="no_trade", score=0.0,
            strategy="equity_momentum", reasoning="", no_trade_reason=None,
            price=round(price, 2),
            rsi_14=round(rsi_14, 1) if rsi_14 is not None else None,
            adx=round(adx, 1) if adx is not None else None,
            sma_50=round(sma_50, 2) if sma_50 is not None else None,
            sma_200=round(sma_200, 2) if sma_200 is not None else None,
            above_50d=above_50d, above_200d=above_200d,
            momentum_1m=round(mom_1m * 100, 2) if mom_1m is not None else None,
            momentum_3m=round(mom_3m * 100, 2) if mom_3m is not None else None,
            momentum_6m=round(mom_6m * 100, 2) if mom_6m is not None else None,
            relative_strength=round(relative * 100, 2) if relative is not None else None,
            volume_ratio=round(vol_ratio, 2) if vol_ratio is not None else None,
            percent_off_52w_high=round(off_52w, 2) if off_52w is not None else None,
            atr_value=round(atr_value, 4) if atr_value is not None else None,
            atr_pct=round(atr_pct, 2) if atr_pct is not None else None,
            breakout_20d=breakout,
            market_risk_tilt=risk_tilt,
            days_to_earnings=days_to_earnings,
            days_to_macro=days_to_macro,
        )
