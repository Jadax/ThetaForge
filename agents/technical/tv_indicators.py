"""
TradingView-Style Indicators for Options Trading.
Stolen from: TradingView Pine Script indicators, CPR, Pivot Points,
TastyTrade mechanical rules, MarketChameleon IV analysis.

Converts popular Pine Script indicators to Python.
"""
import math
from typing import List, Dict, Any, Optional, Tuple
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


@dataclass
class PivotData:
    """Complete pivot point data."""
    standard: CPRData = None
    fibonacci: CPRData = None
    woodie: CPRData = None
    camarilla: CPRData = None


class TradingViewIndicators:
    """
    TradingView Pine Script indicators converted to Python.
    Stolen from TradingView community scripts.
    """

    # =================================================================
    # CPR - Central Pivot Range
    # Stolen from TradingView: "CPR by KivancOzbilgic"
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
    def calculate_weekly_cpr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
    ) -> CPRData:
        """Calculate weekly CPR from daily data."""
        if len(highs) < 5:
            return CPRData()
        # Use last 5 trading days
        h = max(highs[-5:])
        l = min(lows[-5:])
        c = closes[-1]
        return TradingViewIndicators.calculate_cpr(h, l, c)

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
    # Pivot Points (Standard, Fibonacci, Woodie, Camarilla)
    # Stolen from TradingView "Pivot Points" indicator
    # =================================================================

    @staticmethod
    def calculate_pivot_points(
        high: float,
        low: float,
        close: float,
    ) -> PivotData:
        """Calculate all 4 pivot point types."""
        pp = (high + low + close) / 3

        # Standard
        standard = CPRData(
            pivot=pp,
            r1=2 * pp - low,
            r2=pp + (high - low),
            r3=high + 2 * (pp - low),
            s1=2 * pp - high,
            s2=pp - (high - low),
            s3=low - 2 * (high - pp),
        )

        # Fibonacci
        r3_fib = high + 2 * (pp - low)
        r2_fib = pp + 0.618 * (high - low)
        r1_fib = pp + 0.382 * (high - low)
        s1_fib = pp - 0.382 * (high - low)
        s2_fib = pp - 0.618 * (high - low)
        s3_fib = low - 2 * (high - pp)
        fibonacci = CPRData(pivot=pp, r1=r1_fib, r2=r2_fib, r3=r3_fib, s1=s1_fib, s2=s2_fib, s3=s3_fib)

        # Woodie
        pp_woodie = (high + low + 2 * close) / 4
        r1_woodie = 2 * pp_woodie - low
        s1_woodie = 2 * pp_woodie - high
        r2_woodie = pp_woodie + (high - low)
        s2_woodie = pp_woodie - (high - low)
        woodie = CPRData(pivot=pp_woodie, r1=r1_woodie, r2=r2_woodie, s1=s1_woodie, s2=s2_woodie)

        # Camarilla
        r4_cama = close + (high - low) * 1.1 / 2
        r3_cama = close + (high - low) * 1.1 / 4
        r2_cama = close + (high - low) * 1.1 / 6
        r1_cama = close + (high - low) * 1.1 / 12
        s1_cama = close - (high - low) * 1.1 / 12
        s2_cama = close - (high - low) * 1.1 / 6
        s3_cama = close - (high - low) * 1.1 / 4
        s4_cama = close - (high - low) * 1.1 / 2
        camarilla = CPRData(pivot=pp, r1=r1_cama, r2=r2_cama, r3=r3_cama, r4=r4_cama,
                           s1=s1_cama, s2=s2_cama, s3=s3_cama, s4=s4_cama)

        return PivotData(standard=standard, fibonacci=fibonacci, woodie=woodie, camarilla=camarilla)

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
        
        Stolen from TastyTrade/Thinkorswim.
        """
        if iv_52w_high <= iv_52w_low:
            return 50.0
        return max(0, min(100, (current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low) * 100))

    @staticmethod
    def iv_percentile(
        current_iv: float,
        historical_ivs: List[float],
    ) -> float:
        """
        IV Percentile = (Days below current IV / Total days) × 100
        
        More robust than IV Rank (not affected by outliers).
        Stolen from Thinkorswim.
        """
        if not historical_ivs:
            return 50.0
        below = sum(1 for iv in historical_ivs if iv < current_iv)
        return below / len(historical_ivs) * 100

    @staticmethod
    def iv_hv_ratio(iv: float, hv_20: float) -> Dict[str, Any]:
        """
        IV/HV Ratio - measures if options are cheap or expensive.
        Stolen from MarketChameleon.
        """
        if hv_20 <= 0:
            return {"ratio": 1.0, "signal": "neutral", "reasoning": "No HV data"}
        ratio = iv / hv_20
        if ratio > 1.25:
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
        Stolen from CBOE research (statistically significant).
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
    # Stolen from TradingView "Sideways Detector" indicator
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

    # =================================================================
    # 0DTE Gamma Detection
    # Stolen from TradingView "0DTE Strategy" indicators
    # =================================================================

    @staticmethod
    def zero_dte_signal(
        current_price: float,
        expected_move: float,
        time_remaining_hours: float,
        gamma: float,
        theta: float,
    ) -> Dict[str, Any]:
        """
        0DTE signal: is the gamma risk worth the theta reward?
        
        Green zone: theta > delta * expected_move (safe to sell)
        Red zone: gamma > theta decay benefit (dangerous to sell)
        """
        theta_daily = theta * 24 / max(time_remaining_hours, 0.5)
        gamma_risk = gamma * expected_move * current_price

        if theta_daily > 0 and gamma_risk > 0:
            ratio = gamma_risk / abs(theta_daily)
        else:
            ratio = 1.0

        if ratio < 0.5:
            signal = "sell"
            zone = "green"
            reasoning = f"Theta decay ({abs(theta_daily):.2f}) > gamma risk ({gamma_risk:.2f}) → safe to sell"
        elif ratio < 1.0:
            signal = "cautious_sell"
            zone = "yellow"
            reasoning = f"Theta and gamma roughly balanced → reduce size, tighten stops"
        else:
            signal = "avoid_selling"
            zone = "red"
            reasoning = f"Gamma risk ({gamma_risk:.2f}) > theta decay ({abs(theta_daily):.2f}) → avoid selling"

        return {
            "signal": signal,
            "zone": zone,
            "gamma_risk": round(gamma_risk, 4),
            "theta_daily": round(theta_daily, 4),
            "ratio": round(ratio, 3),
            "reasoning": reasoning,
        }

    # =================================================================
    # Expected Move (Thinkorswim style)
    # =================================================================

    @staticmethod
    def expected_move(
        stock_price: float,
        iv: float,
        dte: int,
    ) -> Dict[str, Any]:
        """
        Expected Move calculation.
        Stolen from Thinkorswim.
        
        Daily 1SD = (IV / sqrt(252)) × Price
        Weekly 1SD = (IV / sqrt(52)) × Price
        Monthly 1SD = (IV / sqrt(12)) × Price
        """
        daily_em = stock_price * iv / math.sqrt(252)
        weekly_em = stock_price * iv / math.sqrt(52)
        monthly_em = stock_price * iv / math.sqrt(12)

        # DTE-specific
        dte_em = stock_price * iv * math.sqrt(dte / 365)

        return {
            "daily_1sd": round(daily_em, 2),
            "daily_1sd_pct": round(daily_em / stock_price * 100, 2),
            "weekly_1sd": round(weekly_em, 2),
            "weekly_1sd_pct": round(weekly_em / stock_price * 100, 2),
            "monthly_1sd": round(monthly_em, 2),
            "monthly_1sd_pct": round(monthly_em / stock_price * 100, 2),
            "dte_1sd": round(dte_em, 2),
            "dte_1sd_pct": round(dte_em / stock_price * 100, 2),
            "upper_68": round(stock_price + dte_em, 2),
            "lower_68": round(stock_price - dte_em, 2),
            "upper_95": round(stock_price + 2 * dte_em, 2),
            "lower_95": round(stock_price - 2 * dte_em, 2),
        }

    # =================================================================
    # TastyTrade Mechanical Rules
    # =================================================================

    @staticmethod
    def tastytrade_rules(
        iv_rank: float,
        dte: int,
        delta: float,
        premium_pct: float,
    ) -> Dict[str, Any]:
        """
        TastyTrade mechanical trading rules.
        Stolen from TastyTrade research (314 occurrences studied).
        
        Rules:
        1. IVR >= 50 for premium selling
        2. Enter at 45 DTE
        3. Sell 16 delta
        4. Close at 50% profit
        5. Close at 21 DTE if not at profit target
        6. Never roll for a loss
        """
        signals = []
        warnings = []

        if iv_rank >= 50:
            signals.append(f"IVR {iv_rank:.0f} >= 50 → premium selling approved")
        elif iv_rank >= 30:
            signals.append(f"IVR {iv_rank:.0f} 30-50 → acceptable but reduced size")
        else:
            warnings.append(f"IVR {iv_rank:.0f} < 30 → avoid premium selling")

        if 30 <= dte <= 50:
            signals.append(f"DTE {dte} in 30-50 range → optimal entry window")
        elif dte > 50:
            signals.append(f"DTE {dte} > 50 → consider entering closer to 45 DTE")
        else:
            warnings.append(f"DTE {dte} < 30 → higher gamma risk")

        if abs(delta) <= 0.20:
            signals.append(f"Delta {delta:.2f} within 16-delta target → good strike selection")
        else:
            warnings.append(f"Delta {delta:.2f} > 0.20 → too much directional risk")

        return {
            "approved": iv_rank >= 30 and len(warnings) == 0,
            "signals": signals,
            "warnings": warnings,
            "profit_target": "50% of credit received",
            "stop_loss": "2x credit received",
            "time_exit": "Close at 21 DTE",
            "expected_win_rate": "80-90%" if iv_rank >= 50 else "65-75%",
        }
