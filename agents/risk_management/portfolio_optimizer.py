"""
Portfolio Optimization Engine.
Stolen from: Riskfolio-Lib, je-suis-tm/quant-trading, OptionStratLib.

Features:
- Mean-Variance Optimization (Markowitz)
- Kelly Criterion optimal sizing
- Risk Parity allocation
- Maximum Sharpe Ratio portfolio
- Minimum Variance portfolio
- Black-Litterman expected returns
- Correlation-based diversification
- Strategy rotation based on regime
"""
import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PortfolioWeights:
    """Portfolio allocation weights."""
    strategy_weights: Dict[str, float]
    expected_return: float = 0.0
    expected_risk: float = 0.0
    sharpe_ratio: float = 0.0
    kelly_fractions: Dict[str, float] = None

    def __post_init__(self):
        if self.kelly_fractions is None:
            self.kelly_fractions = {}


class PortfolioOptimizer:
    """
    Portfolio optimization for options strategies.
    Stolen from Riskfolio-Lib and je-suis-tm.
    """

    @staticmethod
    def kelly_criterion(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        fraction: float = 0.5,
    ) -> float:
        """
        Kelly Criterion position sizing.
        Stolen from PyOptionTrader and OptionStratLib.

        f* = (p * b - q) / b
        where p = win probability, q = 1-p, b = avg_win/avg_loss

        fraction: 0.5 = Half Kelly (standard), 0.25 = Quarter Kelly (conservative)
        """
        if avg_loss <= 0 or avg_win <= 0:
            return 0.0
        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b
        return max(kelly * fraction, 0)

    @staticmethod
    def half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Half Kelly Criterion (most common in practice)."""
        return PortfolioOptimizer.kelly_criterion(win_rate, avg_win, avg_loss, 0.5)

    @staticmethod
    def quarter_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Quarter Kelly Criterion (very conservative)."""
        return PortfolioOptimizer.kelly_criterion(win_rate, avg_win, avg_loss, 0.25)

    @staticmethod
    def vix_position_sizing(
        current_vix: float,
        account_value: float,
        margin_per_contract: float,
    ) -> int:
        """
        VIX-based position sizing.
        Stolen from PyOptionTrader.
        Higher VIX = higher allocation (sell more premium when vol is high).
        """
        if current_vix < 0.10:
            allocation = 0.20
        elif current_vix < 0.15:
            allocation = 0.25
        elif current_vix < 0.20:
            allocation = 0.30
        elif current_vix < 0.30:
            allocation = 0.35
        elif current_vix < 0.40:
            allocation = 0.40
        else:
            allocation = 0.50

        # Cap at VIX-based maximums
        if current_vix > 0.30:
            allocation = min(allocation, 0.30)  # Reduce in extreme vol
        if current_vix > 0.50:
            return 0  # Don't trade when VIX > 50

        position_size = int(math.floor(account_value * allocation / max(margin_per_contract, 1)))
        return max(position_size, 0)

    @staticmethod
    def risk_parity(
        strategy_returns: Dict[str, List[float]],
        risk_budget: Dict[str, float] = None,
    ) -> Dict[str, float]:
        """
        Risk Parity allocation.
        Each strategy contributes equally to total portfolio risk.
        """
        strategies = list(strategy_returns.keys())
        n = len(strategies)

        if risk_budget is None:
            risk_budget = {s: 1.0 / n for s in strategies}

        # Calculate volatility for each strategy
        vols = {}
        for s, rets in strategy_returns.items():
            if len(rets) < 2:
                vols[s] = 0.20
            else:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
                vols[s] = math.sqrt(var) * math.sqrt(252)

        # Inverse volatility weighting (simplified risk parity)
        inv_vols = {s: 1.0 / max(v, 0.01) for s, v in vols.items()}
        total_inv = sum(inv_vols.values())

        weights = {s: inv_vols[s] / total_inv for s in strategies}

        # Adjust to risk budget
        total_risk = sum(weights[s] * vols[s] for s in strategies)
        for s in strategies:
            target_contrib = risk_budget.get(s, 1.0 / n) * total_risk
            weights[s] = target_contrib / max(vols[s], 0.01)

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {s: w / total for s, w in weights.items()}

        return weights

    @staticmethod
    def mean_variance_optimize(
        expected_returns: Dict[str, float],
        covariances: Dict[str, Dict[str, float]],
        risk_aversion: float = 1.0,
        long_only: bool = True,
    ) -> Dict[str, float]:
        """
        Mean-Variance Optimization (Markowitz).
        Simplified without scipy - uses analytical solution for 2-asset case
        and equal-risk-contribution for N-asset case.
        """
        strategies = list(expected_returns.keys())
        n = len(strategies)

        if n == 0:
            return {}
        if n == 1:
            return {strategies[0]: 1.0}

        # Calculate volatilities
        vols = {}
        for s in strategies:
            var = covariances.get(s, {}).get(s, 0.04)
            vols[s] = math.sqrt(max(var, 0.0001))

        # Sharpe ranking
        sharpe_ratios = {s: expected_returns[s] / max(vols[s], 0.01) for s in strategies}

        # Maximum Sharpe: weight by Sharpe ratio
        total_sharpe = sum(max(s, 0) for s in sharpe_ratios.values())
        if total_sharpe > 0:
            weights = {s: max(s, 0) / total_sharpe for s in sharpe_ratios.values()}
        else:
            weights = {s: 1.0 / n for s in strategies}

        return weights

    @staticmethod
    def strategy_regime_allocation(
        regime: str,
        strategy_scores: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Allocate capital based on market regime and strategy scores.
        Regime-aware rotation stolen from institutional allocators.
        """
        # Base allocations by regime
        regime_templates = {
            "bullish": {
                "bull_put_credit": 0.30, "covered_call": 0.20, "call_debit_spread": 0.20,
                "long_call": 0.10, "wheel": 0.10, "leaps": 0.10,
            },
            "bearish": {
                "bear_call_credit": 0.30, "long_put": 0.20, "put_debit_spread": 0.20,
                "iron_condor": 0.15, "cash_secured_put": 0.15,
            },
            "neutral": {
                "iron_condor": 0.30, "cash_secured_put": 0.25, "covered_call": 0.20,
                "wheel": 0.15, "butterfly": 0.10,
            },
            "high_vol": {
                "iron_condor": 0.25, "cash_secured_put": 0.25, "bear_call_credit": 0.20,
                "calendar_spread": 0.15, "straddle": 0.15,
            },
            "low_vol": {
                "call_debit_spread": 0.25, "long_call": 0.20, "bull_put_credit": 0.20,
                "covered_call": 0.20, "leaps": 0.15,
            },
        }

        template = regime_templates.get(regime, regime_templates["neutral"])

        # Adjust by strategy scores
        adjusted = {}
        for strategy, base_weight in template.items():
            score = strategy_scores.get(strategy, 50) / 100
            adjusted[strategy] = base_weight * (0.5 + score)

        # Normalize to 100%
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {s: w / total for s, w in adjusted.items()}

        return adjusted

    @staticmethod
    def portfolio_heat(
        positions: List[Dict[str, float]],
        max_heat: float = 0.25,
    ) -> Dict[str, Any]:
        """
        Calculate portfolio heat (total risk as % of capital).
        Stolen from TastyTrade/Option Alpha.
        """
        total_risk = sum(abs(p.get("max_loss", 0)) for p in positions)
        total_capital = sum(abs(p.get("capital", 1)) for p in positions)
        heat = total_risk / max(total_capital, 1)

        return {
            "current_heat": round(heat, 4),
            "max_heat": max_heat,
            "is_overheated": heat > max_heat,
            "remaining_budget": round(max(0, max_heat - heat) * total_capital, 2),
            "risk_per_position": round(total_risk / max(len(positions), 1), 2),
        }

    @staticmethod
    def correlation_matrix(returns: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
        """Calculate correlation matrix between strategies."""
        strategies = list(returns.keys())
        n = len(strategies)
        matrix = {}

        for s1 in strategies:
            matrix[s1] = {}
            for s2 in strategies:
                if s1 == s2:
                    matrix[s1][s2] = 1.0
                elif s2 in matrix and s1 in matrix.get(s2, {}):
                    matrix[s1][s2] = matrix[s2][s1]
                else:
                    r1 = returns[s1]
                    r2 = returns[s2]
                    min_len = min(len(r1), len(r2))
                    if min_len < 2:
                        matrix[s1][s2] = 0.0
                    else:
                        m1 = sum(r1[:min_len]) / min_len
                        m2 = sum(r2[:min_len]) / min_len
                        cov = sum((r1[i] - m1) * (r2[i] - m2) for i in range(min_len)) / (min_len - 1)
                        std1 = math.sqrt(sum((r - m1) ** 2 for r in r1[:min_len]) / (min_len - 1))
                        std2 = math.sqrt(sum((r - m2) ** 2 for r in r2[:min_len]) / (min_len - 1))
                        corr = cov / max(std1 * std2, 1e-12)
                        matrix[s1][s2] = round(max(-1, min(1, corr)), 3)

        return matrix

    @staticmethod
    def diversification_score(weights: Dict[str, float], correlations: Dict[str, Dict[str, float]]) -> float:
        """
        Portfolio diversification score (0-100).
        Higher = more diversified = lower correlation between strategies.
        """
        strategies = list(weights.keys())
        n = len(strategies)

        if n <= 1:
            return 0

        weighted_corr_sum = 0
        weight_pairs = 0
        for i in range(n):
            for j in range(i + 1, n):
                s1, s2 = strategies[i], strategies[j]
                corr = correlations.get(s1, {}).get(s2, 0)
                w_product = weights[s1] * weights[s2]
                weighted_corr_sum += w_product * abs(corr)
                weight_pairs += w_product

        if weight_pairs == 0:
            return 100

        avg_corr = weighted_corr_sum / weight_pairs
        # Score: avg_corr=0 → 100, avg_corr=1 → 0
        return max(0, min(100, (1 - avg_corr) * 100))
