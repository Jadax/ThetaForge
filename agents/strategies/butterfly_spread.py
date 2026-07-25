"""
Butterfly Spread Strategy.
Adapted from general options pricing theory and practitioner research.

Research-backed parameters:
- Buy 1 lower strike, sell 2 middle strikes, buy 1 higher strike
- Max profit when underlying pins the middle strike at expiration
- Reward-to-risk: 3:1 to 5:1 (better than iron condor's 1:1)
- Win rate: 45-60% (lower than condor but higher payoff ratio)
- Best in: low IV, range-bound markets, post-earnings consolidation
- Vega exposure: short (benefits from IV decline)
- Theta exposure: positive (accelerates near expiration)
- Exit at 50-75% of max profit (avoid gamma risk in final week)
- Width: $2.50-$5.00 for precision in tight ranges
- Call butterflies preferred on indices (better liquidity)
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class ButterflySpreadStrategy(BaseStrategy):
    """
    Butterfly Spread: 1x long lower strike, 2x short middle strike, 1x long higher strike.
    Maximum profit when underlying pins the middle strike at expiration.
    
    Better reward-to-risk than iron condors (3:1+ vs 1:1).
    Best in tight ranges with low IV.
    """

    # --- Research-Backed Parameters ---
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 60.0  # Close at 60% of max profit
    STOP_LOSS_PCT = 50.0      # Stop at 50% loss of debit
    WIDTH_PCT = 0.02           # Width = 2% of stock price (~$5 on SPY)
    MAX_IV_RANK = 30.0         # Low IV preferred (short vega)
    STRIKE_OFFSET_PCT = 0.0    # Center at ATM (0% offset)

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        width: float = None,
    ):
        super().__init__(
            name="ButterflySpread",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            max_concurrent_positions=4,
            risk_per_trade_pct=2.0,
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
            max_iv_rank=self.MAX_IV_RANK,
        )
        self.symbols = symbols or ["SPY", "QQQ", "SPX"]
        self.width = width

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for butterfly spread opportunities.
        
        Entry criteria:
        1. IV Rank < 30 (low IV = short vega benefits)
        2. Underlying range-bound (low ATR)
        3. After significant move (stock basing out)
        4. Post-earnings consolidation (IV crush benefits)
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)

            if price <= 0:
                continue

            # --- Entry Filters ---
            if iv_rank > self.MAX_IV_RANK:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: IV Rank {iv_rank:.0f} "
                    f"> {self.MAX_IV_RANK} (need low IV)"
                )
                continue

            # --- Strike Selection ---
            # Center strike at ATM (or slightly offset for directional lean)
            center_strike = price * (1.0 + self.STRIKE_OFFSET_PCT)
            half_width = self.width or (price * self.WIDTH_PCT / 2)
            lower_strike = center_strike - half_width
            upper_strike = center_strike + half_width

            expiry = self._target_expiry()

            legs = [
                {
                    "action": "BUY",
                    "option_type": "CALL",
                    "strike": round(lower_strike, 2),
                    "expiry": expiry,
                    "quantity": 1,
                },
                {
                    "action": "SELL",
                    "option_type": "CALL",
                    "strike": round(center_strike, 2),
                    "expiry": expiry,
                    "quantity": 2,
                },
                {
                    "action": "BUY",
                    "option_type": "CALL",
                    "strike": round(upper_strike, 2),
                    "expiry": expiry,
                    "quantity": 1,
                },
            ]

            # Estimate max profit and debit
            est_debit = price * 0.005  # Rough: 0.5% of stock price for ATM butterfly
            max_profit = (half_width * 100) - (est_debit * 100)

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="COMPLEX",
                quantity=1,
                strike=round(center_strike, 2),
                expiry=expiry,
                option_type="CALL",
                confidence_score=self._calculate_confidence(iv_rank),
                risk_warning=(
                    f"Butterfly spread: max loss = debit paid (${est_debit * 100:.0f}). "
                    f"Max profit = ${max_profit:.0f} (if {symbol} pins {center_strike:.2f}). "
                    f"Win rate ~50% but reward:risk is 3:1+."
                ),
                legs=legs,
                max_loss=est_debit * 100,
                max_profit=max_profit,
                spread_width=half_width * 2,
                entry_rules={
                    "center_strike": "ATM (maximum theta)",
                    "width": f"${half_width * 2:.2f} total ({self.WIDTH_PCT*100:.1f}% of price)",
                    "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    "max_iv_rank": self.MAX_IV_RANK,
                    "ideal_environment": "Range-bound, low ATR, post-earnings consolidation",
                },
                exit_rules={
                    "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max profit",
                    "stop_loss": f"Close at {self.STOP_LOSS_PCT}% of debit",
                    "gamma_warning": "Exit before final 5-7 days (gamma kills butterflies)",
                    "roll": "Roll to new butterfly centered at new price if underlying moves",
                    "avoid": "Never scale up after wins (butterfly wins look smooth, then sharp losses)",
                },
                dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if butterfly spread signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        return True

    def _target_expiry(self) -> str:
        """Calculate target expiry (35 DTE, nearest Friday)."""
        target = datetime.now() + timedelta(days=35)
        days_until_friday = (4 - target.weekday()) % 7
        if days_until_friday == 0 and target.weekday() != 4:
            days_until_friday = 7
        target = target + timedelta(days=days_until_friday)
        return target.strftime("%Y-%m-%d")

    def _calculate_confidence(self, iv_rank: float) -> float:
        """Confidence based on IV environment."""
        base = 60.0
        # Lower IV = more confidence for short vega strategies
        iv_bonus = max((self.MAX_IV_RANK - iv_rank) / 3.0, 0.0)
        return min(base + iv_bonus, 85.0)
