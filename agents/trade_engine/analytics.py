"""
Options Analytics Calculators.
Stolen from: OptionStrat (max pain), Barchart (expected move),
ORATS (NVRP), Tradier (Greeks), Thinkorswim (probability zone).

Implements:
- Max Pain (strike price where most options expire worthless)
- Expected Move (based on ATM straddle price)
- Net Volatility Risk Premium (NVRP) - ORATS edge signal
- Probability of Touch
- Support/Resistance from options OI
"""
import math
import statistics
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict


class OptionsAnalytics:
    """
    Core options analytics - max pain, expected move, NVRP.
    These are the mathematical foundations that drive trade selection.
    """

    def max_pain(self, option_chain: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate Max Pain - the strike price where the most options
        (both calls and puts) expire worthless.
        Used by: OptionStrat, Tradier, Thinkorswim, many traders.
        
        Market makers hedge toward max pain → price tends to gravitate there
        near expiration.
        """
        if not option_chain:
            return {"max_pain_strike": 0, "total_pain": 0, "oi_distribution": {}}

        # Aggregate OI by strike
        call_oi_by_strike = defaultdict(float)
        put_oi_by_strike = defaultdict(float)

        for option in option_chain:
            strike = option.get("strike", 0)
            oi = option.get("open_interest", 0)
            opt_type = option.get("option_type", "").upper()

            if opt_type == "CALL":
                call_oi_by_strike[strike] += oi
            elif opt_type == "PUT":
                put_oi_by_strike[strike] += oi

        all_strikes = sorted(set(list(call_oi_by_strike.keys()) + list(put_oi_by_strike.keys())))
        if not all_strikes:
            return {"max_pain_strike": 0, "total_pain": 0, "oi_distribution": {}}

        # For each potential settlement price, calculate total "pain"
        pain_curve = {}
        for settle_price in all_strikes:
            total_pain = 0
            for strike in all_strikes:
                # Call holders exercise when strike < settlement
                if strike < settle_price:
                    total_pain += (settle_price - strike) * call_oi_by_strike[strike] * 100
                # Put holders exercise when strike > settlement
                if strike > settle_price:
                    total_pain += (strike - settle_price) * put_oi_by_strike[strike] * 100
            pain_curve[settle_price] = total_pain

        # Max Pain is where total pain is minimized
        max_pain_strike = min(pain_curve, key=pain_curve.get)

        # Build OI distribution for visualization
        oi_distribution = {}
        for strike in all_strikes:
            oi_distribution[strike] = {
                "call_oi": call_oi_by_strike[strike],
                "put_oi": put_oi_by_strike[strike],
                "total_oi": call_oi_by_strike[strike] + put_oi_by_strike[strike],
                "pain": pain_curve.get(strike, 0),
            }

        # OI walls (highest concentration)
        call_wall = max(call_oi_by_strike, key=call_oi_by_strike.get) if call_oi_by_strike else 0
        put_floor = max(put_oi_by_strike, key=put_oi_by_strike.get) if put_oi_by_strike else 0

        return {
            "max_pain_strike": max_pain_strike,
            "total_pain": pain_curve.get(max_pain_strike, 0),
            "call_wall": call_wall,
            "put_floor": put_floor,
            "oi_distribution": oi_distribution,
        }

    def expected_move(
        self,
        stock_price: float,
        iv: float,
        dte: int,
        atm_straddle_price: float = None,
    ) -> Dict[str, float]:
        """
        Calculate Expected Move using two methods:
        1. IV-based: Stock Price * IV * sqrt(DTE/365)
        2. Straddle-based: ATM Straddle Price (more accurate)
        
        Used by: Every options trader, Barchart, OptionStrat.
        The expected move defines the 1-standard-deviation range.
        """
        if dte <= 0 or stock_price <= 0:
            return {"expected_move": 0, "upper": stock_price, "lower": stock_price}

        # Method 1: IV-based
        iv_move = stock_price * iv * math.sqrt(dte / 365)

        # Method 2: Straddle-based (if available, more accurate)
        straddle_move = atm_straddle_price if atm_straddle_price else iv_move

        # Use straddle if available, otherwise IV
        em = straddle_move if atm_straddle_price else iv_move

        upper = round(stock_price + em, 2)
        lower = round(stock_price - em, 2)
        upper_2sd = round(stock_price + 2 * em, 2)
        lower_2sd = round(stock_price - 2 * em, 2)

        return {
            "expected_move_1sd": round(em, 2),
            "expected_move_pct": round((em / stock_price) * 100, 2),
            "upper_1sd": upper,
            "lower_1sd": lower,
            "upper_2sd": upper_2sd,
            "lower_2sd": lower_2sd,
            "implied_range_low": lower,
            "implied_range_high": upper,
            "method": "straddle" if atm_straddle_price else "iv",
        }

    def net_volatility_risk_premium(
        self,
        iv: float,
        hv_20: float,
        hv_30: float = None,
        hv_60: float = None,
    ) -> Dict[str, float]:
        """
        Net Volatility Risk Premium (NVRP) - ORATS proprietary concept.
        
        NVRP = IV - Realized Volatility
        
        When NVRP > 0: IV > HV → selling options has edge
        When NVRP < 0: IV < HV → buying options has edge
        
        ORATS found that selling 30-45 DTE options when NVRP > 0
        produces significantly higher risk-adjusted returns.
        """
        # Use 20-day HV as primary
        hv = hv_20
        if hv_30 and not hv_60:
            hv = (hv_20 * 0.5 + hv_30 * 0.3 + (hv_30 or hv_20) * 0.2)
        elif hv_60:
            hv = (hv_20 * 0.4 + hv_30 * 0.35 + hv_60 * 0.25) if hv_30 else (hv_20 * 0.6 + hv_60 * 0.4)

        nvrp = iv - hv if hv > 0 else iv
        nvrp_pct = (nvrp / iv * 100) if iv > 0 else 0

        # Regime classification
        # IV and realized volatility are decimal values (for example 0.25),
        # so the regime thresholds must be percentage *points* expressed as
        # decimals, not whole percentages.
        if nvrp > 0.05:
            regime = "strong_sell_vol"      # Strong edge for selling
        elif nvrp > 0.02:
            regime = "sell_vol"             # Moderate edge for selling
        elif nvrp > -0.02:
            regime = "neutral"              # No clear edge
        elif nvrp > -0.05:
            regime = "buy_vol"              # Moderate edge for buying
        else:
            regime = "strong_buy_vol"       # Strong edge for buying

        return {
            "nvrp": round(nvrp, 2),
            "nvrp_pct": round(nvrp_pct, 2),
            "iv": round(iv, 2),
            "hv_20": round(hv_20, 2),
            "hv_30": round(hv_30, 2) if hv_30 else None,
            "hv_60": round(hv_60, 2) if hv_60 else None,
            "regime": regime,
            "recommendation": "sell_premium" if nvrp > 0 else "buy_premium",
        }

    def probability_of_touch(
        self,
        stock_price: float,
        target_price: float,
        iv: float,
        dte: int,
    ) -> float:
        """
        Probability that the stock touches a price level before expiration.
        More useful than probability of profit because it accounts for path.
        """
        if dte <= 0 or stock_price <= 0:
            return 0.0

        distance_pct = abs(target_price - stock_price) / stock_price
        expected_move = stock_price * iv * math.sqrt(dte / 365)
        if expected_move <= 0:
            return 0.0

        z = distance_pct * stock_price / expected_move
        # Probability of touch ≈ 2 * N(-|z|) where N is standard normal CDF
        # Approximation using error function
        prob_touch = (1 - math.erf(z / math.sqrt(2))) * 100
        return round(min(prob_touch, 100), 1)

    def support_resistance_from_oi(
        self, option_chain: List[Dict[str, Any]], stock_price: float
    ) -> Dict[str, Any]:
        """
        Find support/resistance levels from options open interest.
        High OI at a strike = potential support (puts) or resistance (calls).
        """
        if not option_chain:
            return {"support": [], "resistance": []}

        call_oi = defaultdict(float)
        put_oi = defaultdict(float)

        for opt in option_chain:
            strike = opt.get("strike", 0)
            oi = opt.get("open_interest", 0)
            if opt.get("option_type", "").upper() == "CALL":
                call_oi[strike] += oi
            else:
                put_oi[strike] += oi

        # Resistance: high call OI above current price
        resistance = [
            {"strike": s, "oi": o, "strength": "strong" if o > 10000 else "moderate" if o > 5000 else "weak"}
            for s, o in sorted(call_oi.items(), key=lambda x: x[1], reverse=True)
            if s > stock_price
        ][:5]

        # Support: high put OI below current price
        support = [
            {"strike": s, "oi": o, "strength": "strong" if o > 10000 else "moderate" if o > 5000 else "weak"}
            for s, o in sorted(put_oi.items(), key=lambda x: x[1], reverse=True)
            if s < stock_price
        ][:5]

        return {"support": support, "resistance": resistance}

    def implied_move_at_expiration(
        self,
        stock_price: float,
        iv: float,
        dte: int,
    ) -> Dict[str, Any]:
        """
        Calculate where price will be at expiration with probabilities.
        Similar to Thinkorswim's probability analysis.
        """
        em = self.expected_move(stock_price, iv, dte)
        expected_move_1sd = em["expected_move_1sd"]

        return {
            "current_price": stock_price,
            "expected_move_1sd": expected_move_1sd,
            "range_68pct": (round(stock_price - expected_move_1sd, 2), round(stock_price + expected_move_1sd, 2)),
            "range_95pct": (em["lower_2sd"], em["upper_2sd"]),
            "probability_above_current": 50.0,
            "probability_below_current": 50.0,
            "dte": dte,
            "iv": iv,
        }
