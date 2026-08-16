"""
Gamma Exposure (GEX) / Dealer Positioning Engine.
Calculates net gamma exposure from free option chain data.
Adapted from institutional GEX frameworks (SpotGamma, Squeezemetrics concepts).

Key concepts:
- Net GEX: Call GEX + Put GEX (puts are negative gamma for holders, positive for sellers)
- Dealers are typically SHORT options (negative gamma)
- High positive GEX = price pinned (dealers hedge by selling rallies, buying dips)
- High negative GEX = price amplification (dealers must chase momentum)
- Zero GEX level = most volatile price zone
"""
import logging
from typing import Dict, Any, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class GEXEngine:
    """
    Calculates Gamma Exposure and dealer positioning from option chain data.
    Uses Black-Scholes gamma approximation: Gamma = N'(d1) / (S * sigma * sqrt(T))
    """

    def __init__(self, underlying_price: float = 0.0, risk_free_rate: float = 0.05):
        self.underlying_price = underlying_price
        self.risk_free_rate = risk_free_rate

    def calculate_chain_gex(
        self, option_chain: List[Dict[str, Any]], underlying_price: float = None
    ) -> Dict[str, Any]:
        """
        Calculate GEX for entire option chain.

        Formula per contract:
          Call GEX = Gamma * OI * Price * 100
          Put GEX  = -1 * Gamma * OI * Price * 100 (puts have negative gamma for holders)

        Dealer GEX is inverted (dealers are short options):
          Dealer GEX = -1 * Total GEX
        """
        S = underlying_price or self.underlying_price
        if S <= 0:
            return {"error": "Invalid underlying price"}

        total_call_gex = 0.0
        total_put_gex = 0.0
        strike_gex = {}

        for opt in option_chain:
            strike = opt.get("strike", 0)
            oi = opt.get("open_interest", 0)
            price = opt.get("last", 0) or opt.get("ask", 0)
            iv = opt.get("implied_volatility", 0.2)
            opt_type = opt.get("option_type", "CALL")
            expiry = opt.get("expiry", "")

            # Prefer the provider-computed days-to-expiry when the chain row
            # carries it (the CBOE/free chains always do) -- it avoids a
            # strptime per row and is exact on the provider's own calendar.
            if opt.get("dte"):
                try:
                    T = float(opt["dte"]) / 365.0
                except (TypeError, ValueError):
                    T = self._days_to_expiry(expiry) / 365.0
            else:
                T = self._days_to_expiry(expiry) / 365.0
            if T <= 0 or T > 2.0:
                continue

            gamma = self._approx_gamma(S, strike, T, iv)

            # GEX per contract (multiply by OI and price, scale by 100 shares)
            gex = gamma * oi * S * 100

            if opt_type == "CALL":
                total_call_gex += gex
            else:
                total_put_gex -= gex  # Puts contribute negative GEX to holders

            if strike not in strike_gex:
                strike_gex[strike] = 0.0
            strike_gex[strike] += gex if opt_type == "CALL" else -gex

        net_gex = total_call_gex + total_put_gex
        dealer_gex = -net_gex  # Dealers are typically short options

        # Find zero gamma strike (where GEX flips sign)
        zero_gamma_strike = self._find_zero_gamma(strike_gex, S)

        return {
            "underlying": S,
            "total_call_gex": round(total_call_gex / 1e6, 2),  # In millions
            "total_put_gex": round(total_put_gex / 1e6, 2),
            "net_gex": round(net_gex / 1e6, 2),
            "dealer_gex": round(dealer_gex / 1e6, 2),
            "gex_regime": self._classify_gex_regime(net_gex / 1e6),
            "zero_gamma_strike": zero_gamma_strike,
            "strike_gex": {k: round(v / 1e6, 4) for k, v in sorted(strike_gex.items())},
        }

    def get_gex_trading_signals(self, gex_data: Dict[str, Any]) -> List[str]:
        """Generate trading signals based on GEX regime."""
        signals = []
        regime = gex_data.get("gex_regime", "NEUTRAL")
        net_gex = gex_data.get("net_gex", 0)

        if regime == "HIGH_POSITIVE_GEX":
            signals.append("Price pinning likely. Favor selling premium (iron condors, credit spreads).")
            signals.append("Expect mean reversion. Sell strikes near zero gamma level.")
        elif regime == "HIGH_NEGATIVE_GEX":
            signals.append("Gamma squeeze risk. Expect amplified moves.")
            signals.append("Favor directional trades. Avoid short premium near ATM.")
        elif regime == "FLIP_ZONE":
            signals.append("Zero gamma zone. Maximum volatility expected here.")
            signals.append("Reduce position sizes. Tighten stops.")

        return signals

    def gex_heatmap(self, gex_data: Dict[str, Any]) -> Dict[str, Any]:
        """Per-strike gamma heatmap rows (Flowasis GEX-heatmap pattern).

        Turns the aggregate GEX result into an ordered table the dashboard can
        render as a heat strip: each strike carries its net dealer GEX (in
        millions), percent distance from spot, and a heat bucket. Walls (the
        strikes with the largest positive and negative GEX) are surfaced
        explicitly along with the zero-gamma level. Fail-closed: no strike
        data returns empty rows, never fabricated levels.
        """
        strike_gex = (gex_data or {}).get("strike_gex") or {}
        if not strike_gex:
            return {
                "underlying": (gex_data or {}).get("underlying"),
                "zero_gamma_strike": (gex_data or {}).get("zero_gamma_strike"),
                "walls": {"positive": None, "negative": None},
                "rows": [],
            }

        underlying = (gex_data or {}).get("underlying") or 0.0
        rows = []
        for strike, net in sorted(strike_gex.items()):
            strike = float(strike)
            pct_away = ((strike - underlying) / underlying * 100) if underlying else 0.0
            rows.append({
                "strike": strike,
                "net_gex_millions": round(float(net), 4),
                "pct_away": round(pct_away, 2),
                "heat": self._heat_bucket(float(net)),
            })

        positive_wall = max(rows, key=lambda r: r["net_gex_millions"]) if rows else None
        negative_wall = min(rows, key=lambda r: r["net_gex_millions"]) if rows else None
        return {
            "underlying": underlying,
            "zero_gamma_strike": (gex_data or {}).get("zero_gamma_strike"),
            "walls": {
                "positive": positive_wall,
                "negative": negative_wall,
            },
            "rows": rows,
        }

    @staticmethod
    def _heat_bucket(net_gex_millions: float) -> str:
        """Coarse heat label for one strike (abs values tuned to retail OI sizes)."""
        value = abs(net_gex_millions)
        if value >= 50:
            return "extreme"
        if value >= 20:
            return "hot"
        if value >= 5:
            return "elevated"
        if value > 0:
            return "normal"
        return "flat"

    def _approx_gamma(self, S: float, K: float, T: float, sigma: float) -> float:
        """Approximate gamma using Black-Scholes formula."""
        if sigma <= 0 or T <= 0 or S <= 0:
            return 0.0
        try:
            from scipy.stats import norm
            d1 = (np.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
            gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
            return gamma
        except Exception:
            return 0.0

    def _find_zero_gamma(self, strike_gex: Dict[float, float], current_price: float) -> Optional[float]:
        """Find the strike where GEX flips sign (zero gamma level)."""
        if not strike_gex:
            return None
        sorted_strikes = sorted(strike_gex.keys())
        prev_gex = 0
        for strike in sorted_strikes:
            gex = strike_gex[strike]
            if (prev_gex > 0 and gex < 0) or (prev_gex < 0 and gex > 0):
                return strike
            prev_gex = gex
        return sorted_strikes[len(sorted_strikes) // 2] if sorted_strikes else None

    def _classify_gex_regime(self, net_gex_millions: float) -> str:
        """Classify the current GEX regime."""
        if net_gex_millions > 500:
            return "HIGH_POSITIVE_GEX"
        elif net_gex_millions < -500:
            return "HIGH_NEGATIVE_GEX"
        elif abs(net_gex_millions) < 100:
            return "FLIP_ZONE"
        return "NEUTRAL"

    def _days_to_expiry(self, expiry_str: str) -> int:
        """Calculate days to expiry from date string."""
        try:
            from datetime import datetime
            exp = datetime.strptime(expiry_str, "%Y-%m-%d")
            return max((exp - datetime.now()).days, 1)
        except Exception:
            return 30
