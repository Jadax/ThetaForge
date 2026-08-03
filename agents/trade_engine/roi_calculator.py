"""
Options ROI Calculator.
Stolen from: OptionsellerROI.com, Barchart, Moomoo Option Seller Report.

Calculates return on capital for every possible options trade across all strikes
and expirations - the core feature that makes OptionsellerROI valuable.

Key metrics:
- Premium Yield = Premium / (Strike * 100) for CSPs
- Annualized Return = (Premium Yield / DTE) * 365
- Return on Risk = Premium / Max Loss
- Win Rate adjusted return = Win% * Avg Win - (1-Win%) * Avg Loss
"""
import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta


class ROICalculator:
    """
    Calculates ROI metrics for all option selling strategies.
    Replicates OptionsellerROI's core feature: instant comparison of
    return on capital across every strike and expiration.
    """

    def __init__(self, risk_free_rate: float = 0.05):
        self.risk_free_rate = risk_free_rate

    def csp_roi(
        self,
        strike: float,
        premium: float,
        dte: int,
        stock_price: float,
        iv: float = 0.20,
    ) -> Dict[str, float]:
        """
        Cash-Secured Put ROI calculation.
        This is the OPTIONSELLERROI core metric.
        """
        if strike <= 0 or dte <= 0:
            return {}

        capital_required = strike * 100
        premium_collected = premium * 100
        premium_yield = (premium / strike) * 100  # As percentage
        annualized_return = (premium_yield / dte) * 365 if dte > 0 else 0
        otm_pct = ((strike - stock_price) / stock_price) * 100
        return_on_risk = (premium / (strike - premium)) * 100 if (strike - premium) > 0 else 0
        pop = self._approx_pop_otm(stock_price, strike, dte, "put", iv)

        return {
            "strategy": "cash_secured_put",
            "strike": strike,
            "premium": premium,
            "dte": dte,
            "capital_required": capital_required,
            "premium_collected": premium_collected,
            "premium_yield_pct": round(premium_yield, 2),
            "annualized_return_pct": round(annualized_return, 2),
            "otm_pct": round(otm_pct, 2),
            "return_on_risk_pct": round(return_on_risk, 2),
            "probability_of_profit": round(pop, 1),
            "max_loss": round(capital_required - premium_collected, 2),
            "breakeven": round(strike - premium, 2),
        }

    def covered_call_roi(
        self,
        strike: float,
        premium: float,
        dte: int,
        stock_price: float,
        cost_basis: float = None,
        iv: float = 0.20,
    ) -> Dict[str, float]:
        """Covered Call ROI calculation."""
        if strike <= 0 or dte <= 0:
            return {}

        basis = cost_basis or stock_price
        capital_required = basis * 100
        premium_collected = premium * 100
        premium_yield = (premium / basis) * 100
        annualized_return = (premium_yield / dte) * 365 if dte > 0 else 0
        otm_pct = ((strike - stock_price) / stock_price) * 100
        max_profit = (strike - basis + premium) * 100
        return_on_capital = (max_profit / capital_required) * 100

        pop = self._approx_pop_otm(stock_price, strike, dte, "call", iv)

        return {
            "strategy": "covered_call",
            "strike": strike,
            "premium": premium,
            "dte": dte,
            "capital_required": capital_required,
            "premium_collected": premium_collected,
            "premium_yield_pct": round(premium_yield, 2),
            "annualized_return_pct": round(annualized_return, 2),
            "otm_pct": round(otm_pct, 2),
            "return_on_capital_pct": round(return_on_capital, 2),
            "max_profit": round(max_profit, 2),
            "probability_of_profit": round(pop, 1),
            "breakeven": round(basis - premium, 2),
        }

    def credit_spread_roi(
        self,
        short_strike: float,
        long_strike: float,
        credit: float,
        dte: int,
        stock_price: float,
        spread_type: str = "put",
        iv: float = 0.20,
    ) -> Dict[str, float]:
        """Credit Spread (Bull Put / Bear Call) ROI calculation."""
        width = abs(short_strike - long_strike)
        max_loss = (width - credit) * 100
        max_profit = credit * 100
        capital_required = max_loss
        premium_yield = (credit / width) * 100
        annualized_return = (premium_yield / dte) * 365 if dte > 0 else 0
        return_on_risk = (credit / (width - credit)) * 100 if (width - credit) > 0 else 0

        pop = self._approx_pop_otm(stock_price, short_strike, dte, spread_type, iv)

        return {
            "strategy": f"{spread_type}_credit_spread",
            "short_strike": short_strike,
            "long_strike": long_strike,
            "width": width,
            "credit": credit,
            "dte": dte,
            "capital_required": capital_required,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "premium_yield_pct": round(premium_yield, 2),
            "annualized_return_pct": round(annualized_return, 2),
            "return_on_risk_pct": round(return_on_risk, 2),
            "probability_of_profit": round(pop, 1),
            "breakeven": round(short_strike - credit, 2) if spread_type == "put" else round(short_strike + credit, 2),
        }

    def iron_condor_roi(
        self,
        put_short: float,
        put_long: float,
        call_short: float,
        call_long: float,
        credit: float,
        dte: int,
        stock_price: float,
        iv: float = 0.20,
    ) -> Dict[str, float]:
        """Iron Condor ROI calculation."""
        put_width = put_short - put_long
        call_width = call_long - call_short
        # Maximum loss occurs on the wider wing, not the narrower one.
        width = max(put_width, call_width)
        max_loss = (width - credit) * 100
        max_profit = credit * 100
        capital_required = max_loss
        premium_yield = (credit / width) * 100
        annualized_return = (premium_yield / dte) * 365 if dte > 0 else 0

        # POP = probability stock stays between short strikes
        pop_put = self._approx_pop_otm(stock_price, put_short, dte, "put", iv) / 100
        pop_call = self._approx_pop_otm(stock_price, call_short, dte, "call", iv) / 100
        pop = pop_put + pop_call - 1.0

        return {
            "strategy": "iron_condor",
            "put_short": put_short,
            "put_long": put_long,
            "call_short": call_short,
            "call_long": call_long,
            "credit": credit,
            "width": width,
            "dte": dte,
            "capital_required": capital_required,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "premium_yield_pct": round(premium_yield, 2),
            "annualized_return_pct": round(annualized_return, 2),
            "probability_of_profit": round(max(pop * 100, 0), 1),
        }

    def rank_opportunities(
        self, opportunities: List[Dict[str, Any]], sort_by: str = "annualized_return_pct"
    ) -> List[Dict[str, Any]]:
        """
        Rank all opportunities by ROI - this is the OptionsellerROI killer feature.
        Sort by any metric: annualized return, premium yield, POP, return on risk.
        """
        return sorted(opportunities, key=lambda x: x.get(sort_by, 0), reverse=True)

    def expected_value(
        self,
        max_profit: float,
        max_loss: float,
        probability_of_profit: float,
        probability_of_loss: Optional[float] = None,
    ) -> float:
        """Expected value across three outcome zones (Option Alpha method).

        The naive two-outcome model (``max_profit * P - max_loss * (1 - P)``)
        ignores the real outcome where the underlying expires *between* the
        strikes and only partial profit or loss is realized. This version
        splits the trade into max-profit, partial, and max-loss regions and
        values the partial zone at the midpoint of max profit and max loss.

        Probabilities are fractions in [0, 1]. When *probability_of_loss* is
        omitted it defaults to ``1 - probability_of_profit`` (pure two-outcome).
        """
        if max_profit <= 0 or max_loss <= 0:
            return 0.0
        prob_profit = max(0.0, min(1.0, float(probability_of_profit)))
        if probability_of_loss is None:
            prob_loss = max(0.0, 1.0 - prob_profit)
        else:
            prob_loss = max(0.0, min(1.0, float(probability_of_loss)))
        prob_partial = max(0.0, 1.0 - prob_profit - prob_loss)
        partial_pnl = (max_profit - max_loss) / 2.0
        return round(
            max_profit * prob_profit
            + partial_pnl * prob_partial
            - max_loss * prob_loss,
            4,
        )

    def alpha_score(
        self,
        max_profit: float,
        max_loss: float,
        probability_of_profit: float,
        probability_of_loss: Optional[float] = None,
    ) -> float:
        """Alpha: expected value per dollar of defined risk (EV / max loss).

        Option Alpha's Trade Ideas 2.0 metric. Only meaningful for
        defined-risk structures, so an undefined max loss scores zero.
        """
        if max_loss <= 0:
            return 0.0
        ev = self.expected_value(max_profit, max_loss, probability_of_profit, probability_of_loss)
        return round(ev / max_loss, 4)

    def scan_all_strikes_csp(
        self,
        chain: List[Dict[str, Any]],
        stock_price: float,
        dte: int,
    ) -> List[Dict[str, float]]:
        """
        Scan ALL strikes for CSP opportunities - instant ROI comparison.
        This is exactly what OptionsellerROI does.
        """
        results = []
        for option in chain:
            if option.get("option_type") != "PUT":
                continue
            strike = option.get("strike", 0)
            mid = option.get("last", 0) or option.get("mid", 0)
            if mid <= 0 or strike <= 0:
                continue

            roi = self.csp_roi(strike, mid, dte, stock_price)
            if roi:
                roi["symbol"] = option.get("symbol", "")
                roi["volume"] = option.get("volume", 0)
                roi["open_interest"] = option.get("open_interest", 0)
                results.append(roi)

        return self.rank_opportunities(results)

    def scan_all_strikes_cc(
        self,
        chain: List[Dict[str, Any]],
        stock_price: float,
        dte: int,
        cost_basis: float = None,
    ) -> List[Dict[str, float]]:
        """Scan ALL strikes for Covered Call opportunities."""
        results = []
        for option in chain:
            if option.get("option_type") != "CALL":
                continue
            strike = option.get("strike", 0)
            mid = option.get("last", 0) or option.get("mid", 0)
            if mid <= 0 or strike <= stock_price:
                continue

            roi = self.covered_call_roi(strike, mid, dte, stock_price, cost_basis)
            if roi:
                roi["symbol"] = option.get("symbol", "")
                results.append(roi)

        return self.rank_opportunities(results)

    def _approx_pop_otm(self, stock_price: float, strike: float, dte: int, option_type: str = "put", iv: float = 0.20) -> float:
        """Lognormal probability that an option expires out of the money.

        This is an IV-based model estimate, not a guaranteed win probability.
        """
        if dte <= 0 or stock_price <= 0 or strike <= 0:
            return 50.0
        volatility = max(float(iv or 0), 0.01)
        time = dte / 365
        sigma_sqrt_t = volatility * math.sqrt(time)
        if sigma_sqrt_t <= 0:
            return 50.0
        d2 = (math.log(stock_price / strike) - 0.5 * volatility * volatility * time) / sigma_sqrt_t
        normal_cdf = lambda value: 0.5 * (1 + math.erf(value / math.sqrt(2)))
        pop = normal_cdf(d2) if option_type.lower() == "put" else normal_cdf(-d2)
        return min(max(pop * 100, 1), 99)
