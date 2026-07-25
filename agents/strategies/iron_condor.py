"""
Iron Condor Strategy.
Adapted from OpScanBot, OptionStrat, and Tastytrade research.

Research-backed parameters (71,417 trades, 2005-2025):
- 16-delta short strikes (highest Sharpe ratio = 0.78)
- 30-45 DTE (45 DTE optimal, avg hold 22 days)
- VIX sweet spot: 15-25 (73% win rate, +$87 avg P&L)
- 50% profit target (single most impactful rule)
- 200% stop loss (cuts max drawdown from 21.3% to 16.8%)
- Wing width: $5 on SPY/QQQ, $50 on SPX
- Win rate: 78-83% with management (vs 62-70% hold to expiry)
- NEVER enter when VIX > 35 (33% win rate, -$485 avg)
- Avoid FOMC, CPI, NFP, earnings events
- Roll untested side closer when short delta reaches 0.25-0.30
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class IronCondorStrategy(BaseStrategy):
    """
    Iron Condor: Sell OTM put spread + sell OTM call spread.
    Profits when the underlying stays within a range.
    Market-neutral, positive theta, negative vega.
    """

    # --- Research-Backed Parameters ---
    SHORT_DELTA = 16          # 1 standard deviation OTM (optimal Sharpe)
    WING_DELTA = 5            # Long leg for protection
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 50.0  # Close at 50% of max profit
    STOP_LOSS_MULTIPLIER = 2.0  # Stop at 2x credit received
    ADJUSTMENT_DELTA_TRIGGER = 0.25  # Roll when short delta reaches this
    MAX_ADJUSTMENTS = 2       # More than 2 adjustments = -22% performance

    # VIX regime filters (research: win rate by VIX level)
    VIX_SWEET_SPOT_MIN = 15.0
    VIX_SWEET_SPOT_MAX = 25.0
    VIX_MAX_ENTRY = 35.0      # NEVER enter when VIX > 35
    VIX_MIN_ENTRY = 12.0      # Below this, premium too thin

    # Width configuration
    DEFAULT_WIDTH = 5.0       # $5 wide on SPY/QQQ

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        short_delta: int = None,
        width: float = None,
    ):
        super().__init__(
            name="IronCondor",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            stop_loss_multiplier=self.STOP_LOSS_MULTIPLIER,
            max_concurrent_positions=4,
            risk_per_trade_pct=2.0,
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
            min_vix=self.VIX_MIN_ENTRY,
            max_vix=self.VIX_MAX_ENTRY,
        )
        self.symbols = symbols or ["SPY", "QQQ", "IWM"]
        self.short_delta = short_delta or self.SHORT_DELTA
        self.width = width or self.DEFAULT_WIDTH

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for Iron Condor opportunities.
        
        Entry criteria:
        1. VIX between 15-25 (sweet spot) or 25-35 (acceptable but smaller size)
        2. No imminent binary events
        3. Underlying in a range-bound environment
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)
            vix = market_data.get("vix", 20.0)
            vix_term = market_data.get("vix_term_structure", "contango")

            if price <= 0:
                continue

            # --- VIX Regime Check ---
            if vix > self.VIX_MAX_ENTRY:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: VIX {vix:.1f} > {self.VIX_MAX_ENTRY}"
                )
                continue

            if vix < self.VIX_MIN_ENTRY:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: VIX {vix:.1f} too low, thin premium"
                )
                continue

            # --- VIX Term Structure Check ---
            if vix_term == "backwardation":
                logger.info(
                    f"[{self.name}] Skipping {symbol}: VIX in backwardation (rising fear)"
                )
                continue

            # --- Calculate Strikes ---
            # Short put: 16 delta OTM
            short_put_strike = price * (1.0 - self._delta_to_otm_pct(self.short_delta))
            # Long put: further OTM for protection
            long_put_strike = short_put_strike - self.width
            # Short call: 16 delta OTM
            short_call_strike = price * (1.0 + self._delta_to_otm_pct(self.short_delta))
            # Long call: further OTM for protection
            long_call_strike = short_call_strike + self.width

            expiry = self._target_expiry()
            legs = [
                {"action": "BUY", "option_type": "PUT", "strike": round(long_put_strike, 2)},
                {"action": "SELL", "option_type": "PUT", "strike": round(short_put_strike, 2)},
                {"action": "SELL", "option_type": "CALL", "strike": round(short_call_strike, 2)},
                {"action": "BUY", "option_type": "CALL", "strike": round(long_call_strike, 2)},
            ]

            # Confidence based on VIX regime
            confidence = self._vix_regime_confidence(vix)

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="COMPLEX",
                quantity=1,
                strike=round(short_put_strike, 2),  # Primary reference strike
                expiry=expiry,
                option_type="PUT",
                confidence_score=confidence,
                risk_warning=(
                    f"Iron condor: max loss = {self.width * 100 - self._est_credit(vix):.0f}. "
                    "Profits from range-bound movement. Defined risk on both sides."
                ),
                legs=[{**leg, "expiry": expiry, "quantity": 1} for leg in legs],
                max_loss=(self.width * 100),  # Wing width x 100
                max_profit=self._est_credit(vix) * 100,
                spread_width=self.width,
                entry_rules={
                    "short_delta": f"-{self.short_delta}/{self.short_delta}",
                    "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    "vix_range": f"{self.VIX_SWEET_SPOT_MIN}-{self.VIX_SWEET_SPOT_MAX} sweet spot",
                    "avoid_events": "FOMC, CPI, NFP, earnings",
                },
                exit_rules={
                    "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max profit",
                    "stop_loss": f"Close at {self.STOP_LOSS_MULTIPLIER}x credit received",
                    "adjust_trigger": f"Roll when short delta > {self.ADJUSTMENT_DELTA_TRIGGER}",
                    "adjust_action": "Roll untested side closer for credit",
                    "max_adjustments": self.MAX_ADJUSTMENTS,
                    "gamma_warning": "Close entirely if gamma > 0.04 (below 14 DTE)",
                },
                dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if Iron Condor signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        # Check portfolio-level delta and vega
        net_delta = portfolio.get("net_delta", 0)
        net_vega = portfolio.get("net_vega", 0)
        if abs(net_delta) > 0.20:
            logger.warning(f"[{self.name}] Net delta {net_delta:.2f} exceeds 20% limit")
            return False
        if abs(net_vega) > 0.05:
            logger.warning(f"[{self.name}] Net vega {net_vega:.2f} exceeds 5% limit")
            return False

        return True

    def _delta_to_otm_pct(self, delta: int) -> float:
        """
        Convert delta to approximate OTM percentage.
        16 delta ~= 1 standard deviation ~= 5-7% OTM on SPY.
        This is a rough approximation.
        """
        delta_otm_map = {
            8: 0.10, 10: 0.08, 12: 0.07, 16: 0.06,
            20: 0.05, 25: 0.04, 30: 0.03, 35: 0.02,
        }
        return delta_otm_map.get(delta, 0.05)

    def _est_credit(self, vix: float) -> float:
        """
        Rough estimate of credit per spread based on VIX.
        In production, use actual option chain pricing.
        """
        # Credit scales roughly linearly with VIX
        base_credit = 0.80  # ~$0.80 at VIX 15
        vix_factor = (vix - 15) / 20  # Normalized
        return max(base_credit * (1 + vix_factor), 0.30)

    def _vix_regime_confidence(self, vix: float) -> float:
        """
        Confidence score based on VIX regime.
        Sweet spot (15-25) = highest confidence.
        """
        if self.VIX_SWEET_SPOT_MIN <= vix <= self.VIX_SWEET_SPOT_MAX:
            return 80.0
        elif vix < self.VIX_SWEET_SPOT_MIN:
            return 65.0  # Low premium, lower conviction
        else:
            return 55.0  # Higher risk, smaller size

    def _target_expiry(self) -> str:
        """Calculate target expiry date (45 DTE, nearest Friday)."""
        target_date = datetime.now() + timedelta(days=45)
        days_until_friday = (4 - target_date.weekday()) % 7
        if days_until_friday == 0 and target_date.weekday() != 4:
            days_until_friday = 7
        target_date = target_date + timedelta(days=days_until_friday)
        return target_date.strftime("%Y-%m-%d")
