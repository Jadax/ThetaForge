"""
Trade Recommendation Engine - Edge Calculator.

Mathematical models for calculating trading edge, expected value,
probability of profit, and all quantitative metrics.

FORMULAS IMPLEMENTED:
  Expected Value = (Win% * Avg Win) - (Loss% * Avg Loss)
  Kelly Criterion f* = (p*b - q) / b
  Probability of Profit: Black-Scholes based
  Expected Move = Price * IV * sqrt(DTE/365)
  NVRP = (IV - HV) / IV
  Sharpe Ratio = (Mean Return - Risk Free Rate) / Std Dev * sqrt(252)
  Information Ratio = (Rp - Rb) / Tracking Error
  Premium Yield = Premium Received / Max Loss
  Annualized Return = Premium Yield * (365 / DTE)
"""
import math
from typing import Dict, Any, Tuple, Optional
from scipy.stats import norm
import numpy as np


class EdgeCalculator:
    """
    Calculates mathematical edge for options trades.
    Synthesizes quantitative frameworks from institutional trading.
    """

    # Strategy baseline parameters (backtested win rates)
    STRATEGY_BASELINES = {
        "Wheel":                      {"win_rate": 0.78, "avg_win_mult": 0.50, "avg_loss_mult": 0.75},
        "BullPutCreditSpread":        {"win_rate": 0.72, "avg_win_mult": 0.50, "avg_loss_mult": 1.00},
        "BearCallCreditSpread":       {"win_rate": 0.72, "avg_win_mult": 0.50, "avg_loss_mult": 1.00},
        "IronCondor":                 {"win_rate": 0.78, "avg_win_mult": 0.50, "avg_loss_mult": 0.83},
        "CoveredCall":                {"win_rate": 0.82, "avg_win_mult": 0.40, "avg_loss_mult": 0.60},
        "CashSecuredPut":             {"win_rate": 0.77, "avg_win_mult": 0.45, "avg_loss_mult": 0.70},
        "BullCallDebitSpread":        {"win_rate": 0.50, "avg_win_mult": 1.80, "avg_loss_mult": 1.00},
        "BearPutDebitSpread":         {"win_rate": 0.50, "avg_win_mult": 1.80, "avg_loss_mult": 1.00},
        "LongCall":                   {"win_rate": 0.40, "avg_win_mult": 2.50, "avg_loss_mult": 1.00},
        "LongPut":                    {"win_rate": 0.40, "avg_win_mult": 2.50, "avg_loss_mult": 1.00},
        "CalendarSpread":             {"win_rate": 0.60, "avg_win_mult": 0.70, "avg_loss_mult": 1.00},
        "ButterflySpread":            {"win_rate": 0.55, "avg_win_mult": 3.50, "avg_loss_mult": 1.00},
        "Straddle":                   {"win_rate": 0.45, "avg_win_mult": 2.00, "avg_loss_mult": 1.00},
        "Strangle":                   {"win_rate": 0.55, "avg_win_mult": 1.50, "avg_loss_mult": 1.00},
        "LEAPS":                      {"win_rate": 0.50, "avg_win_mult": 2.00, "avg_loss_mult": 1.00},
        "PoorMansCoveredCall":        {"win_rate": 0.60, "avg_win_mult": 0.70, "avg_loss_mult": 1.00},
        "EarningsShortStraddle":      {"win_rate": 0.38, "avg_win_mult": 3.00, "avg_loss_mult": 1.00},
        "EarningsIronCondor":         {"win_rate": 0.70, "avg_win_mult": 0.50, "avg_loss_mult": 0.83},
        "ZeroDTEPutSpread":           {"win_rate": 0.86, "avg_win_mult": 0.50, "avg_loss_mult": 1.00},
        "ZeroDTECallSpread":          {"win_rate": 0.86, "avg_win_mult": 0.50, "avg_loss_mult": 1.00},
    }

    # =========================================================================
    # CORE FORMULAS
    # =========================================================================

    @staticmethod
    def expected_value(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Expected Value per dollar risked.
        EV = (Win% * Avg Win) - (Loss% * Avg Loss)

        A positive EV means the strategy is profitable over many trades.
        This is the single most important number for strategy evaluation.
        """
        return (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)

    @staticmethod
    def kelly_criterion(prob_win: float, win_loss_ratio: float) -> float:
        """
        Kelly Criterion: Optimal fraction of capital to risk per trade.
        f* = (p * b - q) / b

        where:
            p = probability of winning
            q = probability of losing (1 - p)
            b = win/loss ratio (avg win / avg loss)

        Returns negative if no edge exists (do not trade).
        Uses fractional Kelly (half-Kelly) for practical sizing.
        """
        if prob_win <= 0 or prob_win >= 1 or win_loss_ratio <= 0:
            return 0.0

        p = prob_win
        q = 1.0 - p
        b = win_loss_ratio

        full_kelly = (p * b - q) / b
        # Use half-Kelly for safety (accounts for estimation error)
        return max(full_kelly / 2.0, 0.0)

    @staticmethod
    def probability_of_profit_long(
        S: float, K: float, sigma: float, T: float, r: float = 0.05
    ) -> float:
        """
        Probability of profit for a long option position using Black-Scholes.
        For long call: P(S_T > K) = N(d2)
        For long put: P(S_T < K) = N(-d2)

        Uses normal distribution approximation.
        """
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0
        d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm.cdf(d2)

    @staticmethod
    def probability_of_profit_short(
        S: float, K: float, sigma: float, T: float, r: float = 0.05
    ) -> float:
        """
        Probability of profit for a short option position.
        For short call: P(S_T < K) = N(-d2) = 1 - POP_long
        For short put: P(S_T > K) = N(d2) = POP_long_call
        """
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0
        d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return 1.0 - norm.cdf(d2)

    @staticmethod
    def probability_otm(delta: float) -> float:
        """
        Approximate probability of finishing OTM from delta.
        |delta| ~= probability of finishing ITM.
        P(OTM) = 1 - |delta|

        This is a quick approximation used by TastyTrade for POP display.
        """
        return 1.0 - abs(delta)

    @staticmethod
    def expected_move(
        price: float, iv: float, dte: int
    ) -> Tuple[float, float, float]:
        """
        Expected Move calculation (MarketChameleon framework).
        EM = Price * IV * sqrt(DTE / 365)

        Returns (expected_move_dollar, upper_bound, lower_bound)
        This is the 1-standard-deviation range (68% probability).
        """
        if price <= 0 or iv <= 0:
            return (0.0, price, price)
        em = price * iv * math.sqrt(dte / 365.0)
        return (em, price + em, price - em)

    @staticmethod
    def expected_move_from_straddle(
        straddle_price: float, price: float
    ) -> Tuple[float, float]:
        """
        Expected Move from ATM straddle price.
        EM ~= Straddle Price * 0.85

        Returns (expected_move_dollar, expected_move_pct)
        """
        if price <= 0:
            return (0.0, 0.0)
        em = straddle_price * 0.85
        em_pct = (em / price) * 100.0
        return (em, em_pct)

    @staticmethod
    def nvrp(iv: float, hv: float) -> float:
        """
        Normalized Volatility Risk Premium (Option Alpha / TastyTrade).
        NVRP = (IV - HV) / IV

        Positive NVRP = options are expensive relative to realized vol = sell premium
        Negative NVRP = options are cheap relative to realized vol = buy premium
        """
        if iv <= 0:
            return 0.0
        return (iv - hv) / iv

    @staticmethod
    def iv_hv_ratio(iv: float, hv: float) -> float:
        """
        IV/HV ratio (Market Chameleon framework).
        > 1.0 = IV > HV (overpriced options, sell)
        < 1.0 = IV < HV (underpriced options, buy)
        > 1.5 = Strong sell signal
        < 0.7 = Strong buy signal
        """
        if hv <= 0:
            return 1.0
        return iv / hv

    @staticmethod
    def premium_yield(premium_received: float, max_loss: float) -> float:
        """
        Premium Yield (Jeff Bierman / OptionSellerROI framework).
        Premium Yield = Premium Received / Max Loss (or Capital at Risk)

        This is the ROI per trade if you win.
        Higher = better risk/reward for credit strategies.
        """
        if max_loss <= 0:
            return 0.0
        return premium_received / max_loss

    @staticmethod
    def annualized_return(
        premium_yield: float, dte: int, win_rate: float = 0.75
    ) -> float:
        """
        Annualized return estimate.
        Annualized = Premium Yield * (365 / DTE) * Win Rate

        Adjusts for the probability of actually collecting the premium.
        """
        if dte <= 0:
            return 0.0
        trades_per_year = 365.0 / dte
        return premium_yield * trades_per_year * win_rate

    @staticmethod
    def sharpe_ratio(
        returns: np.ndarray, risk_free_rate: float = 0.05
    ) -> float:
        """
        Sharpe Ratio = (Mean Return - Risk Free Rate) / Std Dev * sqrt(252)

        > 1.0 = Good
        > 2.0 = Excellent
        > 3.0 = Outstanding (rare)
        """
        if len(returns) < 2:
            return 0.0
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)
        if std_ret <= 0:
            return 0.0
        return float((mean_ret - risk_free_rate / 252) / std_ret * math.sqrt(252))

    @staticmethod
    def information_ratio(
        active_returns: np.ndarray, benchmark_return: float = 0.0
    ) -> float:
        """
        Information Ratio = (Mean Active Return) / Tracking Error

        Measures skill of active management vs passive benchmark.
        > 0.5 = Good
        > 1.0 = Excellent
        """
        if len(active_returns) < 2:
            return 0.0
        active = active_returns - benchmark_return / 252
        tracking_error = np.std(active)
        if tracking_error <= 0:
            return 0.0
        return float(np.mean(active) / tracking_error * math.sqrt(252))

    @staticmethod
    def max_drawdown_constraint(
        equity_curve: list, max_allowed_pct: float = 20.0
    ) -> Tuple[float, bool]:
        """
        Calculate maximum drawdown and check against constraint.
        Returns (drawdown_pct, is_within_constraint)
        """
        if not equity_curve:
            return (0.0, True)
        peak = equity_curve[0]
        max_dd = 0.0
        for value in equity_curve:
            if value > peak:
                peak = value
            dd = ((peak - value) / peak) * 100.0
            max_dd = max(max_dd, dd)
        return (max_dd, max_dd <= max_allowed_pct)

    @staticmethod
    def correlation_adjusted_sizing(
        position_correlations: list, base_sizing: float
    ) -> float:
        """
        Correlation-adjusted portfolio sizing.
        When positions are correlated, effective risk is higher.
        Adjusted sizing = Base sizing / (1 + avg_correlation)

        If all positions are perfectly correlated (1.0), sizing is halved.
        If uncorrelated (0.0), sizing stays the same.
        """
        if not position_correlations:
            return base_sizing
        avg_corr = np.mean(position_correlations)
        return base_sizing / (1.0 + max(avg_corr, 0.0))

    # =========================================================================
    # STRATEGY-SPECIFIC CALCULATIONS
    # =========================================================================

    @classmethod
    def get_strategy_baseline(cls, strategy_name: str) -> Dict[str, float]:
        """Get the baseline win rate and ratios for a strategy."""
        return cls.STRATEGY_BASELINES.get(strategy_name, {
            "win_rate": 0.50, "avg_win_mult": 1.0, "avg_loss_mult": 1.0
        })

    @classmethod
    def calculate_strategy_ev(
        cls, strategy_name: str, iv_rank: float = 50.0,
        vix: float = 20.0, trend_aligned: bool = True
    ) -> Dict[str, float]:
        """
        Calculate Expected Value for a specific strategy with market context.

        Adjusts baseline stats based on IV environment and trend alignment.
        This is where edge is quantified: does this strategy have positive
        expected value given current conditions?
        """
        baseline = cls.get_strategy_baseline(strategy_name)
        win_rate = baseline["win_rate"]
        avg_win = baseline["avg_win_mult"]
        avg_loss = baseline["avg_loss_mult"]

        # --- IV Environment Adjustment ---
        # Selling premium benefits from higher IV (more premium collected)
        # Buying premium benefits from lower IV (cheaper options)
        is_selling = strategy_name in [
            "IronCondor", "BullPutCreditSpread", "BearCallCreditSpread",
            "Wheel", "CoveredCall", "CashSecuredPut",
            "EarningsShortStraddle", "EarningsIronCondor",
            "ZeroDTEPutSpread", "ZeroDTECallSpread"
        ]

        if is_selling:
            # Higher IV Rank = more credit = higher win rate
            iv_adjustment = (iv_rank - 50.0) / 200.0  # +/- 25% adjustment
            win_rate = min(max(win_rate + iv_adjustment, 0.3), 0.95)
            # Higher IV = better credit = higher avg win
            avg_win *= (1.0 + (iv_rank - 50.0) / 200.0)
        else:
            # Lower IV Rank = cheaper options = better risk/reward
            iv_adjustment = (50.0 - iv_rank) / 200.0
            win_rate = min(max(win_rate + iv_adjustment, 0.2), 0.8)
            avg_win *= (1.0 + (50.0 - iv_rank) / 200.0)

        # --- Trend Alignment Adjustment ---
        if trend_aligned:
            win_rate *= 1.05  # 5% boost for trend alignment
        else:
            win_rate *= 0.95  # 5% penalty for counter-trend

        # --- VIX Regime Adjustment ---
        if vix > 30 and is_selling:
            win_rate *= 1.03  # High fear = selling premium wins more
        elif vix < 12 and is_selling:
            win_rate *= 0.90  # Complacency = selling premium is riskier
        elif vix < 12 and not is_selling:
            win_rate *= 1.05  # Cheap options = good for buyers

        # Calculate EV
        ev = cls.expected_value(win_rate, avg_win, avg_loss)

        # Calculate Kelly
        b = avg_win / max(avg_loss, 0.001)
        kelly = cls.kelly_criterion(win_rate, b)

        return {
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expected_value": round(ev, 4),
            "kelly_fraction": round(kelly, 4),
            "risk_reward_ratio": round(b, 4),
            "has_positive_ev": ev > 0,
        }

    @staticmethod
    def calculate_probability_of_profit(
        strategy_type: str,
        S: float,
        short_strike: float,
        long_strike: float = None,
        sigma: float = 0.20,
        dte: int = 30,
        r: float = 0.05
    ) -> float:
        """
        Calculate Probability of Profit for various strategies.

        Credit strategies: P(max profit) based on short strike OTM probability
        Debit strategies: P(max profit) based on long strike ITM probability
        Complex strategies: combine leg probabilities
        """
        T = dte / 365.0
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0

        # Simple approximation: use delta as proxy
        d2_short = (math.log(S / short_strike) + (r - 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

        credit_strategies = [
            "IronCondor", "BullPutCreditSpread", "BearCallCreditSpread",
            "Wheel", "CashSecuredPut", "CoveredCall",
            "EarningsShortStraddle", "EarningsIronCondor",
            "ZeroDTEPutSpread", "ZeroDTECallSpread"
        ]

        debit_strategies = [
            "BullCallDebitSpread", "BearPutDebitSpread",
            "LongCall", "LongPut", "LEAPS"
        ]

        if strategy_type in credit_strategies:
            # P(staying OTM) = P(price stays above short put or below short call)
            pop = 1.0 - norm.cdf(d2_short)  # P(S_T > K_short) for puts
        elif strategy_type in debit_strategies:
            # P(reaching strike)
            pop = norm.cdf(d2_short)
        elif strategy_type == "CalendarSpread":
            # Approximate: 55-60% base, adjusted
            pop = 0.58
        elif strategy_type == "ButterflySpread":
            # Lower probability but higher R:R
            pop = 0.45
        elif strategy_type == "Straddle":
            pop = 0.50
        elif strategy_type == "Strangle":
            pop = 0.60
        else:
            pop = 0.50

        return round(min(max(pop, 0.05), 0.95), 4)

    @staticmethod
    def calculate_breakeven(
        strategy_type: str,
        strike: float,
        premium: float,
        width: float = 0.0,
        is_long: bool = True
    ) -> float:
        """
        Calculate breakeven price for various strategies.

        Long call: BE = Strike + Premium
        Short put: BE = Strike - Premium
        Bull call spread: BE = Long Strike + Net Debit
        Bear put spread: BE = Long Strike - Net Debit
        """
        if is_long:
            if "Call" in strategy_type:
                return strike + premium
            elif "Put" in strategy_type:
                return strike - premium
        else:
            if "Put" in strategy_type or "Credit" in strategy_type:
                return strike - premium
            elif "Call" in strategy_type:
                return strike + premium
        return strike

    @staticmethod
    def calculate_greeks_impact(
        net_delta: float, net_theta: float, net_vega: float,
        price_change_pct: float, iv_change_pct: float, days_held: int
    ) -> float:
        """
        Estimate P&L impact from Greeks over a given holding period.
        P&L ~= Delta * dS + Theta * dT + Vega * dIV + 0.5 * Gamma * dS^2

        For simplicity, we use first-order Greeks:
        P&L = (Delta * Price Change * 100) + (Theta * Days Held * 100) + (Vega * IV Change * 100)
        """
        delta_pnl = net_delta * price_change_pct * 100
        theta_pnl = net_theta * days_held * 100
        vega_pnl = net_vega * iv_change_pct * 100
        return delta_pnl + theta_pnl + vega_pnl

    @classmethod
    def calculate_full_trade_metrics(
        cls,
        strategy_name: str,
        S: float,          # Underlying price
        K: float,          # Primary strike
        premium: float,    # Credit received or debit paid
        width: float,      # Spread width
        dte: int,          # Days to expiration
        iv: float,         # Implied volatility
        iv_rank: float,    # IV Rank
        vix: float,        # Current VIX
        is_credit: bool,   # True for credit strategies
        trend_aligned: bool = True
    ) -> Dict[str, Any]:
        """
        Calculate complete trade metrics for a single strategy.
        Combines all formulas into a single comprehensive output.
        """
        T = dte / 365.0

        # Strategy EV
        ev_data = cls.calculate_strategy_ev(
            strategy_name, iv_rank, vix, trend_aligned
        )

        # Probability of Profit
        pop = cls.calculate_probability_of_profit(
            strategy_name, S, K, sigma=iv, dte=dte
        )

        # Max Profit and Max Loss
        if is_credit:
            max_profit = premium * 100  # Premium collected (per contract)
            max_loss = (width - premium) * 100 if width > 0 else premium * 300
            breakeven = K - premium  # For put credit spread
        else:
            max_profit = (width - premium) * 100 if width > 0 else premium * 200
            max_loss = premium * 100  # Premium paid
            breakeven = K + premium  # For call debit spread

        # Premium Yield (Bierman metric)
        py = cls.premium_yield(premium, max_loss / 100)

        # Annualized Return
        ar = cls.annualized_return(py, dte, ev_data["win_rate"])

        # Expected Value per contract
        ev_per_contract = ev_data["expected_value"] * (max_profit + max_loss)

        return {
            **ev_data,
            "probability_of_profit": pop,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakeven": breakeven,
            "premium_yield": round(py, 4),
            "annualized_return": round(ar, 4),
            "expected_value_per_contract": round(ev_per_contract, 2),
            "risk_reward_ratio": round(max_profit / max(max_loss, 1), 4),
        }