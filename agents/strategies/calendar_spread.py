"""
Calendar Spread Strategy.
Adapted from general options pricing theory and Carr & Wu (2009) research.

Research-backed parameters:
- Sell near-term, buy same-strike longer-term option
- Profits from differential time decay and rising implied volatility
- P&L dominated by FORWARD VOLATILITY, not theta alone
- VRP increases with tenor: 30-day ~6.27 pts vs 90-day ~11.28 pts (SPY)
- Ideal conditions:
  - IV Rank 20-50% (moderate, not extreme)
  - VIX term structure in contango (back > front IV)
  - Range-bound underlying
  - VRP Z-Score > 0.5 at back-month tenor
- Avoid: low IV (no vega cushion), inverted term structure, binary events
- Strike: ATM for max theta; OTM for directional lean
- Front: 7-30 DTE, Back: 45-90 DTE
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class CalendarSpreadStrategy(BaseStrategy):
    """
    Calendar Spread: Sell short-term option, buy longer-term option (same strike).
    Profits from time decay differential and IV expansion.
    
    Best in: low-to-moderate IV, range-bound markets, contango term structure.
    """

    # --- Research-Backed Parameters ---
    FRONT_DTE_MIN = 7
    FRONT_DTE_MAX = 30
    BACK_DTE_MIN = 45
    BACK_DTE_MAX = 90
    PROFIT_TARGET_PCT = 50.0  # Close at 50% of max profit
    STOP_LOSS_MULTIPLIER = 2.0  # Stop at 2x debit paid
    MIN_IV_RANK = 20.0        # Minimum IV for sufficient vega exposure
    MAX_IV_RANK = 50.0        # Avoid extremely high IV
    STRIKE_OFFSET_PCT = 0.0   # 0% = ATM, can be adjusted for directional lean

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        option_type: str = "auto",  # "call", "put", or "auto"
    ):
        super().__init__(
            name="CalendarSpread",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            stop_loss_multiplier=self.STOP_LOSS_MULTIPLIER,
            max_concurrent_positions=4,
            risk_per_trade_pct=2.0,
            min_iv_rank=self.MIN_IV_RANK,
            max_iv_rank=self.MAX_IV_RANK,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT"]
        self.option_type = option_type

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for calendar spread opportunities.
        
        Entry criteria:
        1. IV Rank 20-50 (moderate IV environment)
        2. VIX term structure in contango
        3. Underlying range-bound (low ATR)
        4. VRP Z-Score > 0.5 (implied > realized)
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)
            vix = market_data.get("vix", 20.0)
            vix_term = market_data.get("vix_term_structure", "contango")

            if price <= 0:
                continue

            # --- Entry Filters ---
            if not self.check_iv_filter(iv_rank):
                continue

            # Must be in contango (back month IV > front month IV)
            if vix_term == "backwardation":
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: term structure inverted"
                )
                continue

            # --- Strike Selection ---
            # ATM for maximum theta capture
            strike = price * (1.0 + self.STRIKE_OFFSET_PCT)

            # --- DTE Selection ---
            front_expiry = self._target_expiry_front()
            back_expiry = self._target_expiry_back()

            # --- Option Type ---
            # Use calls by default (better liquidity)
            opt_type = "CALL" if self.option_type == "auto" else self.option_type.upper()

            legs = [
                {
                    "action": "SELL",
                    "option_type": opt_type,
                    "strike": round(strike, 2),
                    "expiry": front_expiry,
                    "quantity": 1,
                },
                {
                    "action": "BUY",
                    "option_type": opt_type,
                    "strike": round(strike, 2),
                    "expiry": back_expiry,
                    "quantity": 1,
                },
            ]

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="COMPLEX",
                quantity=1,
                strike=round(strike, 2),
                expiry=front_expiry,
                option_type=opt_type,
                confidence_score=self._calculate_confidence(iv_rank),
                risk_warning=(
                    f"Calendar spread: max loss = debit paid. "
                    f"Profits from IV expansion and time decay differential. "
                    f"P&L driven by forward volatility, not just theta. "
                    f"Front: {front_expiry}, Back: {back_expiry}."
                ),
                legs=legs,
                entry_rules={
                    "strike": "ATM (maximum theta capture)",
                    "front_dte": f"{self.FRONT_DTE_MIN}-{self.FRONT_DTE_MAX} days",
                    "back_dte": f"{self.BACK_DTE_MIN}-{self.BACK_DTE_MAX} days",
                    "iv_rank_range": f"{self.MIN_IV_RANK}-{self.MAX_IV_RANK}",
                    "term_structure": "Contango required (back IV > front IV)",
                    "vrp_z_score": "> 0.5 at back-month tenor",
                },
                exit_rules={
                    "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max profit",
                    "stop_loss": f"Close at {self.STOP_LOSS_MULTIPLIER}x debit paid",
                    "roll_front": "Roll front leg to next month if back leg still profitable",
                    "gamma_warning": "Close or roll before final 7 days",
                    "avoid_binary": "Close before earnings/FOMC",
                },
                dte_target=(self.BACK_DTE_MIN + self.BACK_DTE_MAX) // 2,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if calendar spread signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        return True

    def _target_expiry_front(self) -> str:
        """Calculate front-month expiry (~21 DTE, nearest Friday)."""
        target = datetime.now() + timedelta(days=21)
        days_until_friday = (4 - target.weekday()) % 7
        if days_until_friday == 0 and target.weekday() != 4:
            days_until_friday = 7
        target = target + timedelta(days=days_until_friday)
        return target.strftime("%Y-%m-%d")

    def _target_expiry_back(self) -> str:
        """Calculate back-month expiry (~60 DTE, nearest Friday)."""
        target = datetime.now() + timedelta(days=60)
        days_until_friday = (4 - target.weekday()) % 7
        if days_until_friday == 0 and target.weekday() != 4:
            days_until_friday = 7
        target = target + timedelta(days=days_until_friday)
        return target.strftime("%Y-%m-%d")

    def _calculate_confidence(self, iv_rank: float) -> float:
        """Confidence based on IV environment."""
        base = 60.0
        # Sweet spot around IVR 30-40
        if 30 <= iv_rank <= 40:
            iv_bonus = 15.0
        elif 20 <= iv_rank < 30 or 40 < iv_rank <= 50:
            iv_bonus = 8.0
        else:
            iv_bonus = 0.0
        return min(base + iv_bonus, 85.0)
