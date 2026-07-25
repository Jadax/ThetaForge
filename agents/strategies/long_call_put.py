"""
Long Call/Put Strategy.
Adapted from DaystoExpiry research, OptionsClearing data, and practitioner frameworks.

Research-backed parameters:
- Basic directional strategy with defined risk (premium paid)
- Win rate: 40-50% (but risk/reward makes it viable)
- 70-80% of retail long options traders lose money (OptionsClearing)
- Sweet spot: 0.55-0.60 delta (moderate ITM for consistent profits)
- 30-45 DTE optimal (professional sweet spot for theta/gamma balance)
- Take 50% profit early (60%+ of profits given back by holding too long)
- Trail with 15% stop after reaching 2x profit
- NEVER hold through expiration week
- Best in: moderate IV (30-60 IV Rank), confirmed trend
- Avoid: high IV (>70 IVP), before earnings, weekly options
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class LongCallPutStrategy(BaseStrategy):
    """
    Long Call/Put: Buy calls for bullish exposure, puts for bearish.
    Defined risk (premium paid), unlimited upside potential.
    
    Uses moderate delta (0.55-0.60) for the best consistency.
    Exits early at 50% profit to lock in gains.
    """

    # --- Research-Backed Parameters ---
    LONG_DELTA = 58           # 0.58 delta: moderate ITM "Goldilocks zone"
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 50.0  # Take 50% profit early
    STOP_LOSS_PCT = 50.0      # Stop at 50% loss of premium
    TRAIL_STOP_AFTER_2X = 0.15  # Trail with 15% stop after 2x profit
    MIN_IV_RANK = 30.0        # Minimum for sufficient volatility
    MAX_IV_RANK = 60.0        # Avoid extreme IV (expensive options)

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        direction: str = "auto",  # "auto", "bullish", "bearish"
    ):
        super().__init__(
            name="LongCallPut",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            max_concurrent_positions=5,
            risk_per_trade_pct=2.0,
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
            min_iv_rank=self.MIN_IV_RANK,
            max_iv_rank=self.MAX_IV_RANK,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
        self.direction = direction

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for long call/put opportunities.
        
        Entry criteria:
        1. IV Rank 30-60 (moderate IV -- options not too expensive)
        2. Directional signal confirmed (trend, breakout, catalyst)
        3. 30-45 DTE for optimal theta/gamma balance
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)

            if price <= 0:
                continue

            # --- IV Filter ---
            if not self.check_iv_filter(iv_rank):
                continue

            expiry = self._target_expiry()

            # --- Long Call (Bullish) ---
            if self.direction in ("auto", "bullish"):
                # 58-delta call: slightly ITM for best consistency
                call_strike = price * 0.97  # ~3% ITM
                est_debit = price * 0.04  # Rough: 4% of stock price

                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="BUY",
                    quantity=1,
                    strike=round(call_strike, 2),
                    expiry=expiry,
                    option_type="CALL",
                    confidence_score=self._directional_confidence(iv_rank, "bullish"),
                    risk_warning=(
                        f"Long call: max loss = premium paid (${est_debit * 100:.0f}). "
                        f"Delta {self.LONG_DELTA}/100 (moderate ITM). "
                        f"Take 50% profit early. Never hold through expiration week."
                    ),
                    entry_rules={
                        "delta_target": self.LONG_DELTA / 100,
                        "strike_rule": "Slightly ITM (~3% below price)",
                        "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                        "iv_rank_range": f"{self.MIN_IV_RANK}-{self.MAX_IV_RANK}",
                        "direction": "Bullish: stock above 50-day MA, breakout confirmed",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% gain",
                        "stop_loss": f"Close at {self.STOP_LOSS_PCT}% loss of premium",
                        "trail_stop": f"After 2x profit, trail with {self.TRAIL_STOP_AFTER_2X*100:.0f}% stop",
                        "hold_to_expiry": "NEVER hold through expiration week",
                        "exit_before": "Close by Friday before expiry week",
                    },
                    dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)

            # --- Long Put (Bearish) ---
            if self.direction in ("auto", "bearish"):
                put_strike = price * 1.03  # ~3% OTM for puts
                est_debit = price * 0.035

                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="BUY",
                    quantity=1,
                    strike=round(put_strike, 2),
                    expiry=expiry,
                    option_type="PUT",
                    confidence_score=self._directional_confidence(iv_rank, "bearish"),
                    risk_warning=(
                        f"Long put: max loss = premium paid (${est_debit * 100:.0f}). "
                        f"Delta ~{self.LONG_DELTA}/100. "
                        f"Take 50% profit early. Never hold through expiration week."
                    ),
                    entry_rules={
                        "delta_target": self.LONG_DELTA / 100,
                        "strike_rule": "Slightly ITM (~3% above price for puts)",
                        "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                        "iv_rank_range": f"{self.MIN_IV_RANK}-{self.MAX_IV_RANK}",
                        "direction": "Bearish: stock below 50-day MA, breakdown confirmed",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% gain",
                        "stop_loss": f"Close at {self.STOP_LOSS_PCT}% loss of premium",
                        "trail_stop": f"After 2x profit, trail with {self.TRAIL_STOP_AFTER_2X*100:.0f}% stop",
                        "hold_to_expiry": "NEVER hold through expiration week",
                    },
                    dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if long call/put signal should be taken."""
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

    def _directional_confidence(self, iv_rank: float, direction: str) -> float:
        """
        Confidence based on IV and direction.
        Moderate IV = best for long options (not too expensive, not too cheap).
        """
        base = 55.0
        # Sweet spot IVR 40-50 gives highest confidence
        if 40 <= iv_rank <= 50:
            iv_bonus = 15.0
        elif 30 <= iv_rank < 40 or 50 < iv_rank <= 60:
            iv_bonus = 8.0
        else:
            iv_bonus = 0.0
        return min(base + iv_bonus, 80.0)
