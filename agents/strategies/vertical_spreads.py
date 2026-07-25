"""
Vertical Spreads Strategy (Call Debit Spreads / Bear Put Spreads).
Adapted from IBKR-trader and TradingStrategyGuides research.

Research-backed parameters:
- Call debit spreads for bullish directional plays
- Bear put spreads for bearish directional plays
- ITM (0.75-0.90 delta): 88% win rate, lower ROI
- ATM (0.60-0.70 delta): 75% win rate, balanced
- OTM (0.45-0.55 delta): 62% win rate, higher reward
- Sweet spot: Buy 0.55-0.60 delta, Sell 0.30-0.40 delta
- DTE: 30-45 days (swing trades), 14-21 days (catalyst)
- IV Rank < 30: Excellent for debit spreads (options are cheap)
- IV Rank 30-70: Moderate
- IV Rank > 70: Avoid (sell credit spreads instead)
- Wider spreads produce better long-term results (3:1 to 5:1 R:R)
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class VerticalSpreadStrategy(BaseStrategy):
    """
    Directional vertical spreads:
    - Bull Call Spread: Buy ATM call + Sell OTM call (bullish)
    - Bear Put Spread: Buy ATM put + Sell OTM put (bearish)
    
    Best in low IV environments (options are cheap).
    Profit from directional movement with defined risk.
    """

    # --- Research-Backed Parameters ---
    LONG_DELTA = 55           # Buy slightly ITM (0.55 delta)
    SHORT_DELTA = 35          # Sell OTM (0.35 delta)
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 75.0  # Close at 75% of max profit (debit strategy)
    STOP_LOSS_PCT = 50.0      # Stop at 50% of debit paid
    MAX_IV_RANK = 30.0        # Best when IV is low (cheap options)
    WIDTH_PCT_OF_PRICE = 0.02  # Width = 2% of stock price

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 15.0,
        direction: str = "auto",  # "auto", "bullish", "bearish"
    ):
        super().__init__(
            name="VerticalSpreads",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            max_concurrent_positions=5,
            risk_per_trade_pct=2.0,
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
            max_iv_rank=self.MAX_IV_RANK,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT"]
        self.direction = direction

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for directional vertical spread opportunities.
        
        Entry criteria:
        1. IV Rank < 30 (options are cheap -- good for debit strategies)
        2. Directional signal (trend, breakout, or momentum)
        3. Sufficient DTE for thesis to play out
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)

            if price <= 0:
                continue

            # --- IV Filter ---
            # Debit spreads benefit from LOW IV (buying cheap options)
            # If IV is high, we should be selling premium instead
            if iv_rank > self.MAX_IV_RANK:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: IV Rank {iv_rank:.0f} "
                    f"> {self.MAX_IV_RANK} (sell credit spreads instead)"
                )
                continue

            # --- Width Calculation ---
            width = max(price * self.WIDTH_PCT_OF_PRICE, 2.0)
            width = round(width, 0)

            expiry = self._target_expiry()

            # --- Bull Call Spread ---
            if self.direction in ("auto", "bullish"):
                long_strike = price  # ATM
                short_strike = price * 1.04  # ~4% OTM

                # Ensure width is consistent
                actual_width = short_strike - long_strike
                if actual_width < width:
                    short_strike = long_strike + width

                legs = [
                    {
                        "action": "BUY",
                        "option_type": "CALL",
                        "strike": round(long_strike, 2),
                        "expiry": expiry,
                        "quantity": 1,
                    },
                    {
                        "action": "SELL",
                        "option_type": "CALL",
                        "strike": round(short_strike, 2),
                        "expiry": expiry,
                        "quantity": 1,
                    },
                ]

                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="COMPLEX",
                    quantity=1,
                    strike=round(long_strike, 2),
                    expiry=expiry,
                    option_type="CALL",
                    confidence_score=self._directional_confidence(iv_rank),
                    risk_warning=(
                        f"Bull call spread: max loss = debit paid. "
                        f"Max profit = (width - debit) x 100. "
                        f"Profits if {symbol} rises above {short_strike:.2f}."
                    ),
                    legs=legs,
                    max_loss=width * 100,  # Worst case
                    spread_width=width,
                    entry_rules={
                        "long_delta": f"{self.LONG_DELTA}/100",
                        "short_delta": f"{self.SHORT_DELTA}/100",
                        "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                        "max_iv_rank": self.MAX_IV_RANK,
                        "width": f"${width:.0f}",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max profit",
                        "stop_loss": f"Close at {self.STOP_LOSS_PCT}% of debit paid",
                        "trail_stop": "After 2x profit, trail with 15% stop",
                        "hold_to_expiry": "Avoid -- close before final week",
                    },
                    dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)

            # --- Bear Put Spread ---
            if self.direction in ("auto", "bearish"):
                long_strike = price  # ATM
                short_strike = price * 0.96  # ~4% OTM

                actual_width = long_strike - short_strike
                if actual_width < width:
                    short_strike = long_strike - width

                legs = [
                    {
                        "action": "BUY",
                        "option_type": "PUT",
                        "strike": round(long_strike, 2),
                        "expiry": expiry,
                        "quantity": 1,
                    },
                    {
                        "action": "SELL",
                        "option_type": "PUT",
                        "strike": round(short_strike, 2),
                        "expiry": expiry,
                        "quantity": 1,
                    },
                ]

                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="COMPLEX",
                    quantity=1,
                    strike=round(long_strike, 2),
                    expiry=expiry,
                    option_type="PUT",
                    confidence_score=self._directional_confidence(iv_rank),
                    risk_warning=(
                        f"Bear put spread: max loss = debit paid. "
                        f"Max profit = (width - debit) x 100. "
                        f"Profits if {symbol} falls below {short_strike:.2f}."
                    ),
                    legs=legs,
                    max_loss=width * 100,
                    spread_width=width,
                    entry_rules={
                        "long_delta": f"{self.LONG_DELTA}/100",
                        "short_delta": f"{self.SHORT_DELTA}/100",
                        "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                        "max_iv_rank": self.MAX_IV_RANK,
                        "width": f"${width:.0f}",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max profit",
                        "stop_loss": f"Close at {self.STOP_LOSS_PCT}% of debit paid",
                        "trail_stop": "After 2x profit, trail with 15% stop",
                        "hold_to_expiry": "Avoid -- close before final week",
                    },
                    dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if vertical spread signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        return True

    def _target_expiry(self) -> str:
        """Calculate target expiry date (35 DTE, nearest Friday)."""
        target_date = datetime.now() + timedelta(days=35)
        days_until_friday = (4 - target_date.weekday()) % 7
        if days_until_friday == 0 and target_date.weekday() != 4:
            days_until_friday = 7
        target_date = target_date + timedelta(days=days_until_friday)
        return target_date.strftime("%Y-%m-%d")

    def _directional_confidence(self, iv_rank: float) -> float:
        """
        Confidence based on IV environment.
        Low IV = cheap options = better risk/reward for debit spreads.
        """
        base = 65.0
        # Lower IV = more confidence for debit spreads
        iv_bonus = max((self.MAX_IV_RANK - iv_rank) / 5.0, 0.0)
        return min(base + iv_bonus, 85.0)
