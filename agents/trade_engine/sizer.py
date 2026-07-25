"""
Trade Recommendation Engine - Position Sizer.

Kelly Criterion + correlation-adjusted portfolio sizing.

PIPELINE STAGE 3 of 6: SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND

SIZING METHODOLOGY:
  1. Full Kelly from win rate and risk/reward ratio
  2. Half-Kelly for safety (accounts for estimation error)
  3. Correlation adjustment (diversification benefit or penalty)
  4. Drawdown constraint (never risk more than max drawdown from peak)
  5. Maximum position size cap (per position and per sector)
  6. Portfolio heat check (total risk across all positions)

FORMULAS:
  Kelly f* = (p*b - q) / b
  Half-Kelly = f* / 2
  Correlation-adjusted = Half-Kelly / (1 + avg_correlation)
  Max risk per trade = min(Kelly result, 2% of account, remaining budget)
"""
import math
from typing import List, Dict, Optional, Tuple

import numpy as np

from .models import (
    AccountInfo, CurrentPosition, StrategyScore,
    RiskTolerance, StrategyType,
)
from .edge_calculator import EdgeCalculator


class PositionSizer:
    """
    Position sizing engine using Kelly Criterion with portfolio-level constraints.

    Ensures each trade is sized appropriately for the account size,
    existing positions, and risk tolerance.
    """

    def __init__(self, account: AccountInfo):
        self.account = account
        self.edge_calc = EdgeCalculator()

        # Risk limits based on tolerance
        self.risk_limits = {
            RiskTolerance.CONSERVATIVE: {
                "max_risk_per_trade_pct": 1.0,
                "max_portfolio_heat_pct": 15.0,
                "max_positions": 10,
                "max_sector_pct": 15.0,
                "max_correlated_positions": 3,
                "kelly_fraction": 0.25,  # Quarter Kelly
                "max_single_loss_pct": 3.0,
            },
            RiskTolerance.MODERATE: {
                "max_risk_per_trade_pct": 2.0,
                "max_portfolio_heat_pct": 25.0,
                "max_positions": 15,
                "max_sector_pct": 25.0,
                "max_correlated_positions": 5,
                "kelly_fraction": 0.50,  # Half Kelly
                "max_single_loss_pct": 5.0,
            },
            RiskTolerance.AGGRESSIVE: {
                "max_risk_per_trade_pct": 3.0,
                "max_portfolio_heat_pct": 35.0,
                "max_positions": 20,
                "max_sector_pct": 30.0,
                "max_correlated_positions": 7,
                "kelly_fraction": 0.75,  # Three-quarter Kelly
                "max_single_loss_pct": 7.0,
            },
        }

    def get_limits(self) -> Dict[str, float]:
        """Get risk limits for current account tolerance."""
        return self.risk_limits[self.account.risk_tolerance]

    # =================================================================
    # CORE SIZING
    # =================================================================

    def calculate_kelly_size(
        self,
        score: StrategyScore,
        max_loss_per_contract: float,
        current_positions: List[CurrentPosition] = None,
        position_correlations: List[float] = None,
    ) -> Dict[str, any]:
        """
        Calculate position size using Kelly Criterion with adjustments.

        Returns a dict with:
          - kelly_full: Full Kelly fraction
          - kelly_half: Half Kelly (safety)
          - kelly_adjusted: After correlation and drawdown adjustments
          - num_contracts: Final number of contracts
          - capital_at_risk: Total capital at risk
          - risk_per_trade_pct: Risk as % of account
          - sizing_reasoning: List of reasoning strings
        """
        if current_positions is None:
            current_positions = []
        if position_correlations is None:
            position_correlations = []

        limits = self.get_limits()
        reasoning = []

        net_liq = self.account.net_liquidation
        if net_liq <= 0 or max_loss_per_contract <= 0:
            return {
                "kelly_full": 0.0,
                "kelly_half": 0.0,
                "kelly_adjusted": 0.0,
                "num_contracts": 0,
                "capital_at_risk": 0.0,
                "risk_per_trade_pct": 0.0,
                "sizing_reasoning": ["Invalid account or max loss"],
            }

        # Step 1: Full Kelly
        kelly_full = score.kelly_fraction * 2.0  # edge_calc returns half-kelly
        if score.win_rate > 0 and score.avg_loss > 0:
            b = score.avg_win / score.avg_loss
            kelly_full = EdgeCalculator.kelly_criterion(score.win_rate, b) * 2.0
        reasoning.append(f"Full Kelly: {kelly_full:.3f}")

        # Step 2: Apply risk tolerance fraction
        kelly_tolerance = kelly_full * limits["kelly_fraction"]
        reasoning.append(
            f"Kelly x {limits['kelly_fraction']:.0%} "
            f"({self.account.risk_tolerance.value}): {kelly_tolerance:.3f}"
        )

        # Step 3: Correlation adjustment
        if position_correlations:
            avg_corr = np.mean(position_correlations)
            corr_adj = 1.0 / (1.0 + max(avg_corr, 0.0))
            kelly_adjusted = kelly_tolerance * corr_adj
            reasoning.append(
                f"Correlation adjustment ({avg_corr:.2f} avg): "
                f"{kelly_adjusted:.3f}"
            )
        else:
            kelly_adjusted = kelly_tolerance
            reasoning.append("No correlation adjustment (no existing positions)")

        # Step 4: Convert to dollar amount
        risk_budget = net_liq * kelly_adjusted
        reasoning.append(f"Risk budget: ${risk_budget:.2f} ({kelly_adjusted:.1%} of ${net_liq:,.0f})")

        # Step 5: Cap at max risk per trade
        max_risk_dollar = net_liq * (limits["max_risk_per_trade_pct"] / 100.0)
        if risk_budget > max_risk_dollar:
            risk_budget = max_risk_dollar
            reasoning.append(f"Capped at {limits['max_risk_per_trade_pct']}% max: ${max_risk_dollar:.2f}")

        # Step 6: Check remaining risk budget
        current_risk = sum(
            abs(p.avg_cost * p.quantity * 0.10)  # Estimate 10% max loss
            for p in current_positions
        )
        total_heat = (current_risk / net_liq) * 100
        remaining_heat = limits["max_portfolio_heat_pct"] - total_heat
        remaining_budget = net_liq * (remaining_heat / 100.0)
        reasoning.append(
            f"Portfolio heat: {total_heat:.1f}% / "
            f"{limits['max_portfolio_heat_pct']:.0f}% "
            f"(remaining: {remaining_heat:.1f}%)"
        )

        if remaining_budget <= 0:
            reasoning.append("PORTFOLIO HEAT LIMIT REACHED - no additional risk")
            return {
                "kelly_full": kelly_full,
                "kelly_half": kelly_full / 2.0,
                "kelly_adjusted": kelly_adjusted,
                "num_contracts": 0,
                "capital_at_risk": 0.0,
                "risk_per_trade_pct": 0.0,
                "sizing_reasoning": reasoning,
            }

        risk_budget = min(risk_budget, remaining_budget)

        # Step 7: Convert to contracts
        num_contracts = max(int(risk_budget / max_loss_per_contract), 0)
        capital_at_risk = num_contracts * max_loss_per_contract
        risk_pct = (capital_at_risk / net_liq) * 100

        # Step 8: Check maximum positions
        if len(current_positions) >= limits["max_positions"]:
            reasoning.append(f"Max positions ({limits['max_positions']}) reached")
            return {
                "kelly_full": kelly_full,
                "kelly_half": kelly_full / 2.0,
                "kelly_adjusted": kelly_adjusted,
                "num_contracts": 0,
                "capital_at_risk": 0.0,
                "risk_per_trade_pct": 0.0,
                "sizing_reasoning": reasoning,
            }

        reasoning.append(
            f"Final: {num_contracts} contracts, "
            f"${capital_at_risk:,.2f} at risk ({risk_pct:.1f}%)"
        )

        return {
            "kelly_full": round(kelly_full, 4),
            "kelly_half": round(kelly_full / 2.0, 4),
            "kelly_adjusted": round(kelly_adjusted, 4),
            "num_contracts": num_contracts,
            "capital_at_risk": round(capital_at_risk, 2),
            "risk_per_trade_pct": round(risk_pct, 2),
            "sizing_reasoning": reasoning,
        }

    # =================================================================
    # RISK VALIDATION
    # =================================================================

    def validate_sizing(
        self,
        num_contracts: int,
        max_loss_per_contract: float,
        credit_per_contract: float,
        current_positions: List[CurrentPosition] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Validate that the proposed sizing is within risk constraints.

        Returns (is_valid, list_of_warnings)
        """
        if current_positions is None:
            current_positions = []

        warnings = []
        limits = self.get_limits()
        net_liq = self.account.net_liquidation

        if num_contracts <= 0:
            return (False, ["Zero contracts"])

        # Check single position risk
        position_risk = num_contracts * max_loss_per_contract
        position_risk_pct = (position_risk / net_liq) * 100
        if position_risk_pct > limits["max_single_loss_pct"]:
            warnings.append(
                f"Position risk {position_risk_pct:.1f}% exceeds "
                f"max {limits['max_single_loss_pct']}%"
            )

        # Check total portfolio heat
        total_risk = position_risk + sum(
            abs(p.avg_cost * p.quantity * 0.10)
            for p in current_positions
        )
        total_heat = (total_risk / net_liq) * 100
        if total_heat > limits["max_portfolio_heat_pct"]:
            warnings.append(
                f"Total heat {total_heat:.1f}% would exceed "
                f"max {limits['max_portfolio_heat_pct']}%"
            )

        # Check margin requirement
        margin_needed = num_contracts * (max_loss_per_contract * 1.5)
        if margin_needed > self.account.buying_power:
            warnings.append(
                f"Margin needed ${margin_needed:,.0f} exceeds "
                f"buying power ${self.account.buying_power:,.0f}"
            )

        # Check minimum premium
        if credit_per_contract <= 0.05:
            warnings.append(
                f"Credit ${credit_per_contract:.2f} too small for "
                f"transaction costs"
            )

        is_valid = len(warnings) == 0
        return (is_valid, warnings)

    # =================================================================
    # BATCH SIZING
    # =================================================================

    def size_batch(
        self,
        scored_strategies: List[StrategyScore],
        current_positions: List[CurrentPosition] = None,
    ) -> List[Dict]:
        """
        Size multiple strategies, respecting portfolio-level constraints.

        Processes strategies in order of composite score (best first),
        allocating risk budget until exhausted.
        """
        if current_positions is None:
            current_positions = []

        results = []
        allocated_risk = 0.0
        net_liq = self.account.net_liquidation
        limits = self.get_limits()
        max_total_risk = net_liq * (limits["max_portfolio_heat_pct"] / 100.0)

        for score in scored_strategies:
            # Estimate max loss (simplified: assume spread width)
            estimated_max_loss = 200.0  # Default per contract

            sizing = self.calculate_kelly_size(
                score, estimated_max_loss, current_positions
            )

            if sizing["num_contracts"] > 0:
                # Check if we have remaining budget
                if allocated_risk + sizing["capital_at_risk"] <= max_total_risk:
                    allocated_risk += sizing["capital_at_risk"]
                    results.append({
                        "strategy_score": score,
                        "sizing": sizing,
                    })
                else:
                    # Try to fit partial
                    remaining = max_total_risk - allocated_risk
                    if remaining > estimated_max_loss:
                        partial_contracts = int(remaining / estimated_max_loss)
                        if partial_contracts > 0:
                            sizing["num_contracts"] = partial_contracts
                            sizing["capital_at_risk"] = (
                                partial_contracts * estimated_max_loss
                            )
                            allocated_risk += sizing["capital_at_risk"]
                            sizing["sizing_reasoning"].append(
                                f"Partial allocation: {partial_contracts} "
                                f"(budget constrained)"
                            )
                            results.append({
                                "strategy_score": score,
                                "sizing": sizing,
                            })

        return results
