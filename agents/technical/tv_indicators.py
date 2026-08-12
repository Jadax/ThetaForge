"""
Technical Indicators for Options Trading.

Python implementations of widely-used indicator math for options context:
Central Pivot Range, IV rank/percentile, IV/HV ratio, put-call-ratio
sentiment, and sideways-market detection.
"""
import math
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class CPRData:
    """Central Pivot Range data for a single period."""
    pivot: float = 0.0
    top_central_pivot: float = 0.0
    bottom_central_pivot: float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    r3: float = 0.0
    s1: float = 0.0
    s2: float = 0.0
    s3: float = 0.0


class TradingViewIndicators:
    """Shared indicator math used by the AI Brain and scanner."""

    # =================================================================
    # CPR - Central Pivot Range
    # =================================================================

    @staticmethod
    def calculate_cpr(
        high: float,
        low: float,
        close: float,
        prev_high: float = None,
        prev_low: float = None,
        prev_close: float = None,
    ) -> CPRData:
        """
        Calculate Central Pivot Range (CPR).
        
        CPR = TC (Top Central) and BC (Bottom Central) define the range.
        - Price ABOVE CPR = bullish (sell puts above BC)
        - Price BELOW CPR = bearish (sell calls below TC)
        - Price INSIDE CPR = neutral (straddle/strangle)
        
        Pine Script equivalent:
        PP = (High + Low + Close) / 3
        TC = (High + Low) / 2
        BC = 2 * PP - TC
        """
        # Standard Pivot
        pp = (high + low + close) / 3
        tc = (high + low) / 2
        bc = 2 * pp - tc

        # If previous day data available, use it for width calculation
        if prev_high and prev_low and prev_close:
            prev_pp = (prev_high + prev_low + prev_close) / 3
            prev_tc = (prev_high + prev_low) / 2
            prev_bc = 2 * prev_pp - prev_tc
            # Width = |TC - BC|, wider = more volatile
            width = abs(tc - bc)

        r1 = 2 * pp - low
        r2 = pp + (high - low)
        r3 = high + 2 * (pp - low)
        s1 = 2 * pp - high
        s2 = pp - (high - low)
        s3 = low - 2 * (high - pp)

        return CPRData(
            pivot=round(pp, 2),
            top_central_pivot=round(tc, 2),
            bottom_central_pivot=round(bc, 2),
            r1=round(r1, 2), r2=round(r2, 2), r3=round(r3, 2),
            s1=round(s1, 2), s2=round(s2, 2), s3=round(s3, 2),
        )

    @staticmethod
    def cpr_option_signal(
        current_price: float,
        cpr: CPRData,
    ) -> Dict[str, Any]:
        """
        CPR-based option selling signal.
        
        Rules:
        - Price above CPR (bullish): Sell puts at/below BC
        - Price below CPR (bearish): Sell calls at/above TC
        - Price in CPR (neutral): Iron condor or strangle
        - CPR width narrow (<1% of price): Tight range, good for condors
        - CPR width wide (>2% of price): High vol, be cautious
        """
        width = cpr.top_central_pivot - cpr.bottom_central_pivot
        width_pct = (width / current_price * 100) if current_price > 0 else 0

        if current_price > cpr.top_central_pivot:
            bias = "bullish"
            strategy = "sell_puts"
            target = cpr.bottom_central_pivot
            reasoning = f"Price above CPR top ({cpr.top_central_pivot}). Sell puts at/below BC ({cpr.bottom_central_pivot})"
        elif current_price < cpr.bottom_central_pivot:
            bias = "bearish"
            strategy = "sell_calls"
            target = cpr.top_central_pivot
            reasoning = f"Price below CPR bottom ({cpr.bottom_central_pivot}). Sell calls at/above TC ({cpr.top_central_pivot})"
        else:
            bias = "neutral"
            strategy = "iron_condor"
            target = cpr.pivot
            reasoning = f"Price inside CPR ({cpr.bottom_central_pivot}-{cpr.top_central_pivot}). Iron condor around pivot"

        return {
            "bias": bias,
            "strategy": strategy,
            "target_strike": round(target, 2),
            "cpr_top": cpr.top_central_pivot,
            "cpr_bottom": cpr.bottom_central_pivot,
            "cpr_pivot": cpr.pivot,
            "cpr_width": round(width, 2),
            "cpr_width_pct": round(width_pct, 2),
            "reasoning": reasoning,
        }

    # =================================================================
    # IV Rank / IV Percentile (Thinkorswim/TastyTrade style)
    # =================================================================

    @staticmethod
    def iv_rank(
        current_iv: float,
        iv_52w_high: float,
        iv_52w_low: float,
    ) -> float:
        """
        IV Rank = (Current IV - 52w Low) / (52w High - 52w Low) × 100
        """
        if iv_52w_high <= iv_52w_low:
            return 50.0
        return max(0, min(100, (current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low) * 100))

    @staticmethod
    def iv_hv_ratio(iv: float, hv_20: float) -> Dict[str, Any]:
        """
        IV/HV Ratio - measures if options are cheap or expensive.
        """
        if hv_20 <= 0:
            return {"ratio": 1.0, "signal": "neutral", "reasoning": "No HV data"}
        ratio = iv / hv_20
        # FlashAlpha's published sell checklist uses IV/RV > 1.15 as the rich
        # threshold (the recommender's execution gate already only requires
        # iv > hv, so 1.25 here was stricter than the rest of the pipeline).
        if ratio > 1.15:
            signal = "sell_premium"
            reasoning = f"IV ({iv:.1%}) significantly above HV ({hv_20:.1%}) → options expensive, sell premium"
        elif ratio < 0.85:
            signal = "buy_premium"
            reasoning = f"IV ({iv:.1%}) below HV ({hv_20:.1%}) → options cheap, buy premium"
        else:
            signal = "neutral"
            reasoning = f"IV/HV ratio normal ({ratio:.2f})"
        return {"ratio": round(ratio, 3), "signal": signal, "reasoning": reasoning}

    # =================================================================
    # Put-Call Ratio Sentiment (CBOE Z-Score method)
    # =================================================================

    @staticmethod
    def put_call_ratio_sentiment(
        current_pcr: float,
        historical_pcrs: List[float],
    ) -> Dict[str, Any]:
        """
        Put-Call Ratio with Z-Score enhancement.
        """
        if not historical_pcrs or len(historical_pcrs) < 20:
            # Basic PCR interpretation
            if current_pcr > 1.3:
                return {"signal": "contrarian_bullish", "confidence": 70,
                        "reasoning": f"PCR {current_pcr:.2f} > 1.3 → extreme fear → contrarian bullish"}
            elif current_pcr < 0.6:
                return {"signal": "contrarian_bearish", "confidence": 70,
                        "reasoning": f"PCR {current_pcr:.2f} < 0.6 → extreme greed → contrarian bearish"}
            return {"signal": "neutral", "confidence": 30,
                    "reasoning": f"PCR {current_pcr:.2f} in neutral range"}

        mean_pcr = sum(historical_pcrs) / len(historical_pcrs)
        std_pcr = math.sqrt(sum((p - mean_pcr) ** 2 for p in historical_pcrs) / len(historical_pcrs))

        if std_pcr <= 0:
            return {"signal": "neutral", "confidence": 30, "z_score": 0}

        z_score = (current_pcr - mean_pcr) / std_pcr

        if z_score >= 2:
            signal = "contrarian_bullish"
            confidence = min(90, 50 + abs(z_score) * 20)
            reasoning = f"PCR Z-score {z_score:.2f} ≥ 2 → statistically significant bullish signal (p<0.05)"
        elif z_score <= -2:
            signal = "contrarian_bearish"
            confidence = min(90, 50 + abs(z_score) * 20)
            reasoning = f"PCR Z-score {z_score:.2f} ≤ -2 → statistically significant bearish signal"
        elif z_score > 1:
            signal = "moderately_bullish"
            confidence = 55
            reasoning = f"PCR Z-score {z_score:.2f} → moderately bullish"
        elif z_score < -1:
            signal = "moderately_bearish"
            confidence = 55
            reasoning = f"PCR Z-score {z_score:.2f} → moderately bearish"
        else:
            signal = "neutral"
            confidence = 30
            reasoning = f"PCR Z-score {z_score:.2f} → neutral"

        return {
            "signal": signal,
            "confidence": round(confidence, 1),
            "z_score": round(z_score, 2),
            "current_pcr": current_pcr,
            "mean_pcr": round(mean_pcr, 3),
            "reasoning": reasoning,
        }

    # =================================================================
    # Sideways Detection (for iron condor/strangle decisions)
    # =================================================================

    @staticmethod
    def detect_sideways(
        closes: List[float],
        highs: List[float] = None,
        lows: List[float] = None,
        rsi_period: int = 14,
        adx_period: int = 14,
        bb_period: int = 20,
    ) -> Dict[str, Any]:
        """
        Detect if market is sideways (range-bound).
        Used to decide: sell options when sideways, buy when trending.
        
        Criteria from TradingView scripts:
        - RSI between 40-60 (no momentum)
        - ADX < 25 (weak trend)
        - Bollinger Band width < threshold (low volatility)
        """
        from agents.backtest.advanced_backtest import SignalEngine

        if len(closes) < max(rsi_period, adx_period, bb_period) + 5:
            return {"is_sideways": False, "confidence": 0, "reasoning": "Insufficient data"}

        # RSI check
        rsi = SignalEngine.rsi(closes, rsi_period)
        current_rsi = rsi[-1] if rsi else 50
        rsi_sideways = 40 <= current_rsi <= 60

        # ADX check (need highs/lows)
        adx_sideways = True
        if highs and lows and len(highs) >= adx_period + 5:
            adx = SignalEngine.adx(highs, lows, closes, adx_period)
            current_adx = adx[-1] if adx else 25
            adx_sideways = current_adx < 25
        else:
            current_adx = 25

        # Bollinger Band width
        bb_upper, bb_mid, bb_lower = SignalEngine.bollinger_bands(closes, bb_period)
        bb_width = (bb_upper[-1] - bb_lower[-1]) / max(bb_mid[-1], 1) * 100 if bb_mid[-1] > 0 else 0
        bb_squeeze = bb_width < 5  # Less than 5% width

        # Consecutive small moves
        if len(closes) >= 10:
            recent_returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(-10, 0)]
            small_moves = sum(1 for r in recent_returns if abs(r) < 1)
            consecutive_small = small_moves >= 7
        else:
            consecutive_small = False

        is_sideways = rsi_sideways and adx_sideways and (bb_squeeze or consecutive_small)
        score = sum([rsi_sideways, adx_sideways, bb_squeeze, consecutive_small]) / 4 * 100

        return {
            "is_sideways": is_sideways,
            "confidence": round(score, 1),
            "rsi": round(current_rsi, 1),
            "rsi_sideways": rsi_sideways,
            "adx": round(current_adx, 1),
            "adx_sideways": adx_sideways,
            "bb_width_pct": round(bb_width, 2),
            "bb_squeeze": bb_squeeze,
            "consecutive_small_moves": consecutive_small,
            "reasoning": f"RSI={current_rsi:.0f}({'ok' if rsi_sideways else 'no'}), ADX={current_adx:.0f}({'ok' if adx_sideways else 'no'}), BB_width={bb_width:.1f}%({'ok' if bb_squeeze else 'no'})",
        }
