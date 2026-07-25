"""
VIX Term Structure Analysis.
Analyzes VIX futures curve for contango/backwardation signals.
Adapted from institutional volatility analysis frameworks.

Key concepts:
- Contango (normal): Front month < Back month -> Bullish, selling premium
- Backwardation (inverted): Front month > Back month -> Bearish, fear elevated
- VIX Spike: VIX > 30 -> High fear, potential selling opportunity
- VIX Crush: VIX dropping from high -> Post-event normalization
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class VIXTermStructure:
    """
    Analyzes VIX term structure and volatility regime.
    Uses free VIX data from yfinance and CBOE.
    """

    def __init__(self):
        self.vix_thresholds = {
            "very_low": 12,
            "low": 15,
            "normal": 20,
            "elevated": 25,
            "high": 30,
            "extreme": 35,
        }

    def analyze(self, vix_current: float, vix_history: List[float] = None) -> Dict[str, Any]:
        """Full VIX term structure analysis."""
        regime = self._classify_regime(vix_current)
        percentile = self._calculate_percentile(vix_current, vix_history or [])
        recommendations = self._get_recommendations(regime, percentile)

        return {
            "vix_current": vix_current,
            "regime": regime,
            "percentile": percentile,
            "recommendations": recommendations,
            "favor_selling_premium": regime in ["normal", "elevated", "high"],
            "favor_buying_premium": regime in ["very_low", "low", "extreme"],
        }

    def check_term_structure(self, front_month_vix: float, back_month_vix: float) -> Dict[str, Any]:
        """Check VIX term structure for contango/backwardation."""
        ratio = front_month_vix / max(back_month_vix, 0.01)

        if ratio < 0.97:
            structure = "CONTANGO"
            implication = "Bullish - Normal volatility structure"
        elif ratio > 1.03:
            structure = "BACKWARDATION"
            implication = "Bearish - Elevated fear in near-term"
        else:
            structure = "FLAT"
            implication = "Neutral - No clear term structure signal"

        return {
            "structure": structure,
            "ratio": round(ratio, 4),
            "implication": implication,
            "front_month": front_month_vix,
            "back_month": back_month_vix,
        }

    def get_vix_trades(self, vix_current: float) -> List[str]:
        """Suggest VIX-based trade ideas."""
        trades = []
        regime = self._classify_regime(vix_current)

        if regime in ["very_low", "low"]:
            trades.append("Consider buying VIX calls as portfolio insurance (cheap protection)")
            trades.append("Sell iron condors on VIX if comfortable with volatility risk")
        elif regime in ["high", "extreme"]:
            trades.append("Consider selling VIX puts (capture elevated premium)")
            trades.append("Avoid buying VIX calls (likely overpriced)")
            trades.append("Reduce overall portfolio short vega exposure")
        elif regime == "normal":
            trades.append("Normal VIX environment - proceed with standard strategies")

        return trades

    def _classify_regime(self, vix: float) -> str:
        """Classify VIX into regime buckets."""
        if vix < self.vix_thresholds["very_low"]:
            return "very_low"
        elif vix < self.vix_thresholds["low"]:
            return "low"
        elif vix < self.vix_thresholds["normal"]:
            return "normal"
        elif vix < self.vix_thresholds["elevated"]:
            return "elevated"
        elif vix < self.vix_thresholds["high"]:
            return "high"
        return "extreme"

    def _calculate_percentile(self, current: float, history: List[float]) -> float:
        """Calculate VIX percentile over historical period."""
        if not history:
            return 50.0
        below = sum(1 for v in history if v < current)
        return round(below / len(history) * 100, 1)

    def _get_recommendations(self, regime: str, percentile: float) -> List[str]:
        """Get strategy recommendations based on VIX regime."""
        recs = []
        if regime in ["very_low", "low"]:
            recs.append("IV likely < RV - Avoid selling premium")
            recs.append("Good environment for debit spreads and long options")
            recs.append("LEAPS are relatively cheap")
        elif regime in ["high", "extreme"]:
            recs.append("IV likely > RV - Favor selling premium")
            recs.append("Iron condors and credit spreads favored")
            recs.append("Wait for VIX spike to sell premium at best prices")
        else:
            recs.append("Normal volatility - Standard strategy selection")
        return recs
