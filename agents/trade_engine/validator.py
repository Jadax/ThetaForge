"""
Trade Recommendation Engine - Risk Validator.

Final risk validation and circuit breaker checks before recommendation.

PIPELINE STAGE 5 of 6: SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND

VALIDATION LAYERS:
  1. Hard Stop Checks        - Absolute no-go conditions
  2. Risk Budget Checks      - Portfolio-level risk limits
  3. Concentration Checks    - Sector, strategy, symbol overlap
  4. Correlation Checks      - Cross-position correlation risk
  5. Event Risk Checks       - Earnings, FOMC, macro event proximity
  6. Drawdown Circuit Breakers - Stop trading after excessive drawdown
  7. Greeks Exposure Checks  - Net Greeks within limits
  8. Liquidity Depth Checks  - Sufficient OI and volume at strikes

METHODOLOGY:
  - TastyTrade: Portfolio heat management, beta weighting
  - Option Alpha: Decision recipe IF/THEN validation
  - Institutional: Drawdown circuit breakers, max loss caps
"""
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

from .models import (
    AccountInfo, CurrentPosition, MarketConditions, StrategyScore,
    TradeRecommendation, RiskTolerance, StrategyType, Direction,
)


@dataclass
class ValidationResult:
    """Result of validating a trade recommendation."""
    is_approved: bool = True
    hard_stops: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    concentration_warnings: List[str] = field(default_factory=list)
    correlation_warnings: List[str] = field(default_factory=list)
    event_warnings: List[str] = field(default_factory=list)
    drawdown_warnings: List[str] = field(default_factory=list)
    greeks_warnings: List[str] = field(default_factory=list)
    liquidity_warnings: List[str] = field(default_factory=list)
    adjustments_made: List[str] = field(default_factory=list)
    final_score_penalty: float = 0.0


class RiskValidator:
    """
    Comprehensive risk validation engine.

    Checks recommendations against multiple layers of risk management
    before they are sent to the recommendation output.
    """

    def __init__(
        self,
        account: AccountInfo,
        market: MarketConditions,
        current_positions: List[CurrentPosition] = None,
        current_drawdown_pct: float = 0.0,
        daily_pnl_pct: float = 0.0,
        consecutive_losses: int = 0,
    ):
        self.account = account
        self.market = market
        self.current_positions = current_positions or []
        self.current_drawdown_pct = current_drawdown_pct
        self.daily_pnl_pct = daily_pnl_pct
        self.consecutive_losses = consecutive_losses

    # =================================================================
    # LAYER 1: HARD STOPS (Absolute no-go conditions)
    # =================================================================

    def _check_hard_stops(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """
        Hard stop conditions that absolutely prevent the trade.

        These are non-negotiable risk limits.
        """
        stops = []

        # Zero contracts
        if sizing.get("num_contracts", 0) <= 0:
            stops.append("HARD STOP: Zero contracts (sizing returned 0)")

        # No positive expected value
        if score.expected_value <= 0:
            stops.append(
                f"HARD STOP: Negative EV ({score.expected_value:.3f}). "
                f"Strategy has no statistical edge."
            )

        # Kelly fraction negative
        if score.kelly_fraction <= 0:
            stops.append(
                f"HARD STOP: Kelly fraction {score.kelly_fraction:.3f} "
                f"indicates no edge"
            )

        # Account below minimum
        if self.account.net_liquidation < 5000:
            stops.append(
                f"HARD STOP: Account ${self.account.net_liquidation:,.0f} "
                f"below minimum $5,000"
            )

        return stops

    # =================================================================
    # LAYER 2: RISK BUDGET CHECKS
    # =================================================================

    def _check_risk_budget(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """Check portfolio-level risk budget."""
        warnings = []

        risk_pct = sizing.get("risk_per_trade_pct", 0)
        net_liq = self.account.net_liquidation

        # Calculate total portfolio heat
        existing_risk = sum(
            abs(p.avg_cost * p.quantity * 0.10)
            for p in self.current_positions
        )
        new_risk = sizing.get("capital_at_risk", 0)
        total_heat = ((existing_risk + new_risk) / net_liq) * 100

        if total_heat > 30:
            warnings.append(
                f"Portfolio heat {total_heat:.1f}% exceeds 30% - "
                f"high risk of large drawdown"
            )
        elif total_heat > 20:
            warnings.append(
                f"Portfolio heat {total_heat:.1f}% is elevated (limit: 25%)"
            )

        # Daily loss check
        if self.daily_pnl_pct < -2.0:
            warnings.append(
                f"Daily P&L {self.daily_pnl_pct:.1f}% - consider pausing"
            )

        # Buying power check
        if self.account.buying_power < net_liq * 0.1:
            warnings.append(
                f"Low buying power: ${self.account.buying_power:,.0f} "
                f"({self.account.buying_power/net_liq*100:.1f}%)"
            )

        return warnings

    # =================================================================
    # LAYER 3: CONCENTRATION CHECKS
    # =================================================================

    def _check_concentration(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """Check for over-concentration in sector, strategy, or symbol."""
        warnings = []

        # Same symbol
        same_symbol = [
            p for p in self.current_positions
            if p.symbol == score.symbol
        ]
        if same_symbol:
            total_exposure = sum(
                abs(p.avg_cost * p.quantity) for p in same_symbol
            )
            exposure_pct = (total_exposure / self.account.net_liquidation) * 100
            warnings.append(
                f"Already exposed to {score.symbol}: "
                f"${total_exposure:,.0f} ({exposure_pct:.1f}%)"
            )

        # Same strategy concentration
        strategy_counts = {}
        for p in self.current_positions:
            strategy_counts[p.strategy] = strategy_counts.get(p.strategy, 0) + 1
        current_count = strategy_counts.get(score.strategy.value, 0)
        if current_count >= 5:
            warnings.append(
                f"Already have {current_count} "
                f"{score.strategy.value} positions"
            )

        return warnings

    # =================================================================
    # LAYER 4: CORRELATION CHECKS
    # =================================================================

    def _check_correlation(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """Check cross-position correlation risk."""
        warnings = []

        # Simple sector-based correlation check
        same_sector = [
            p for p in self.current_positions
            if p.sector and p.sector != ""
        ]

        if len(same_sector) >= 4:
            warnings.append(
                f"High sector concentration: {len(same_sector)} "
                f"positions in similar sectors"
            )

        return warnings

    # =================================================================
    # LAYER 5: EVENT RISK CHECKS
    # =================================================================

    def _check_event_risk(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """Check for proximity to market-moving events."""
        warnings = []

        is_credit = score.strategy in [
            StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
            StrategyType.BEAR_CALL_CREDIT, StrategyType.WHEEL,
        ]

        if self.market.fomc_days_away <= 3 and is_credit:
            warnings.append(
                f"FOMC in {self.market.fomc_days_away} days - "
                f"credit strategies carry event risk"
            )

        if self.market.cpi_days_away <= 2 and is_credit:
            warnings.append(
                f"CPI in {self.market.cpi_days_away} days - "
                f"elevated volatility risk"
            )

        return warnings

    # =================================================================
    # LAYER 6: DRAWDOWN CIRCUIT BREAKERS
    # =================================================================

    def _check_drawdown_circuit_breakers(self) -> List[str]:
        """
        Drawdown-based circuit breakers.

        These pause trading when the account is in significant drawdown.
        Based on institutional risk management frameworks.
        """
        warnings = []

        if self.current_drawdown_pct > 25:
            warnings.append(
                f"CIRCUIT BREAKER: Drawdown {self.current_drawdown_pct:.1f}% "
                f"exceeds 25% - STOP TRADING, review all positions"
            )
        elif self.current_drawdown_pct > 15:
            warnings.append(
                f"WARNING: Drawdown {self.current_drawdown_pct:.1f}% "
                f"exceeds 15% - reduce position sizes"
            )

        if self.consecutive_losses >= 5:
            warnings.append(
                f"CIRCUIT BREAKER: {self.consecutive_losses} consecutive "
                f"losses - pause and review strategy"
            )
        elif self.consecutive_losses >= 3:
            warnings.append(
                f"WARNING: {self.consecutive_losses} consecutive losses - "
                f"be cautious"
            )

        return warnings

    # =================================================================
    # LAYER 7: GREEKS EXPOSURE CHECKS
    # =================================================================

    def _check_greeks_exposure(
        self, score: StrategyScore, sizing: Dict
    ) -> List[str]:
        """Check net Greeks exposure across the portfolio."""
        warnings = []

        # Calculate net Greeks (simplified)
        net_delta = sum(
            p.delta * p.quantity * 100 for p in self.current_positions
        )
        net_vega = sum(
            p.vega * p.quantity * 100 for p in self.current_positions
        )
        net_theta = sum(
            p.theta * p.quantity * 100 for p in self.current_positions
        )

        net_liq = self.account.net_liquidation

        # Delta exposure as % of account
        delta_pct = abs(net_delta) / net_liq * 100
        if delta_pct > 50:
            warnings.append(
                f"High delta exposure: {delta_pct:.1f}% of account"
            )

        # Vega exposure
        if abs(net_vega) > net_liq * 0.05:
            warnings.append(
                f"High vega exposure: ${abs(net_vega):,.0f}"
            )

        return warnings

    # =================================================================
    # LAYER 8: LIQUIDITY DEPTH CHECKS
    # =================================================================

    def _check_liquidity_depth(
        self, recommendation: TradeRecommendation
    ) -> List[str]:
        """Check that sufficient OI and volume exist at chosen strikes."""
        warnings = []

        # This is a placeholder that would check actual option chain data
        # In production, this would verify OI > minimum at each strike
        for leg in recommendation.legs:
            strike = leg.get("strike", 0)
            if strike <= 0:
                warnings.append(f"Invalid strike price: {strike}")

        return warnings

    # =================================================================
    # MAIN VALIDATION
    # =================================================================

    def validate(
        self,
        score: StrategyScore,
        sizing: Dict,
        recommendation: TradeRecommendation = None,
    ) -> ValidationResult:
        """
        Run all validation layers and return comprehensive result.

        Returns ValidationResult with pass/fail, warnings, and adjustments.
        """
        result = ValidationResult()

        # Layer 1: Hard stops
        result.hard_stops = self._check_hard_stops(score, sizing)
        if result.hard_stops:
            result.is_approved = False

        # Layer 2: Risk budget
        result.risk_warnings = self._check_risk_budget(score, sizing)

        # Layer 3: Concentration
        result.concentration_warnings = self._check_concentration(score, sizing)

        # Layer 4: Correlation
        result.correlation_warnings = self._check_correlation(score, sizing)

        # Layer 5: Event risk
        result.event_warnings = self._check_event_risk(score, sizing)

        # Layer 6: Drawdown circuit breakers
        result.drawdown_warnings = self._check_drawdown_circuit_breakers()
        if any("CIRCUIT BREAKER" in w for w in result.drawdown_warnings):
            result.is_approved = False

        # Layer 7: Greeks exposure
        result.greeks_warnings = self._check_greeks_exposure(score, sizing)

        # Layer 8: Liquidity depth
        if recommendation:
            result.liquidity_warnings = self._check_liquidity_depth(
                recommendation
            )

        # Calculate score penalty from warnings
        total_warnings = (
            len(result.risk_warnings)
            + len(result.concentration_warnings)
            + len(result.correlation_warnings)
            + len(result.event_warnings)
            + len(result.drawdown_warnings)
            + len(result.greeks_warnings)
        )
        result.final_score_penalty = min(total_warnings * 3.0, 30.0)

        return result
