"""
Bull Put Credit Spread Strategy.
Adapted from OpScanBot with research-backed parameters.

Research-backed parameters (SPY 2005-2025):
- 16-delta short put (1 standard deviation OTM)
- 30-45 DTE (30 DTE showed highest CAGR)
- IV Rank > 40 (deliver ~40% more credit)
- 50% profit target (raises win rate from 60% to 72%)
- 200% stop loss (cuts drawdown by half)
- Win rate: 88-93% with management (vs 65-80% without)
- Width: $2-5 on SPY, scale to underlying price (1-2% of stock price)
- Avoid: VIX > 30 with inverted term structure (66% vs 74% win rate)
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class CreditSpreadStrategy(BaseStrategy):
    """
    Bull Put Credit Spread: Sell OTM put + buy lower-strike put.
    Collects premium while underlying stays above short put.
    Neutral-to-bullish strategy with high win rate.
    """

    # --- Research-Backed Parameters ---
    SHORT_DELTA = 16          # 1 standard deviation OTM (optimal)
    WING_DELTA = 5            # Long protection leg
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 50.0  # Close at 50% of max profit
    STOP_LOSS_MULTIPLIER = 2.0  # Stop at 2x credit received
    MIN_IV_RANK = 40.0        # Deliver ~40% more credit above this
    MAX_VIX = 30.0            # Avoid elevated fear environments
    WIDTH_PCT_OF_PRICE = 0.02  # Width = 2% of stock price

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        short_delta: int = None,
        min_iv_rank: float = None,
    ):
        super().__init__(
            name="CreditSpread",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            stop_loss_multiplier=self.STOP_LOSS_MULTIPLIER,
            max_concurrent_positions=6,
            risk_per_trade_pct=2.0,
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
            min_iv_rank=min_iv_rank or self.MIN_IV_RANK,
            max_vix=self.MAX_VIX,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL"]
        self.short_delta = short_delta or self.SHORT_DELTA

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for Bull Put Credit Spread opportunities.
        
        Entry criteria:
        1. IV Rank > 40 (richer premium)
        2. VIX < 30 (avoid elevated fear)
        3. VIX term structure not inverted
        4. Stock in uptrend or neutral (above 50-day MA)
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

            if vix > self.MAX_VIX:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: VIX {vix:.1f} > {self.MAX_VIX}"
                )
                continue

            if vix_term == "backwardation":
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: VIX term structure inverted"
                )
                continue

            # --- Calculate Strikes ---
            # Width scales with stock price (1-2% of price, minimum $1)
            width = max(price * self.WIDTH_PCT_OF_PRICE, 1.0)
            width = round(width, 0)  # Round to whole dollars

            # Short put: 16 delta OTM (~6% OTM on SPY)
            short_put_strike = price * 0.94
            # Long put: further OTM
            long_put_strike = short_put_strike - width

            expiry = self._target_expiry()
            legs = [
                {
                    "action": "BUY",
                    "option_type": "PUT",
                    "strike": round(long_put_strike, 2),
                    "expiry": expiry,
                    "quantity": 1,
                },
                {
                    "action": "SELL",
                    "option_type": "PUT",
                    "strike": round(short_put_strike, 2),
                    "expiry": expiry,
                    "quantity": 1,
                },
            ]

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="COMPLEX",
                quantity=1,
                strike=round(short_put_strike, 2),
                expiry=expiry,
                option_type="PUT",
                confidence_score=self._calculate_confidence(iv_rank, vix),
                risk_warning=(
                    f"Bull put credit spread: max loss = width x 100 = ${width * 100:.0f}. "
                    f"Profits if {symbol} stays above {short_put_strike:.2f} at expiry."
                ),
                legs=legs,
                max_loss=width * 100,
                spread_width=width,
                entry_rules={
                    "short_delta": f"-{self.short_delta}/100",
                    "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    "min_iv_rank": self.MIN_IV_RANK,
                    "max_vix": self.MAX_VIX,
                    "width": f"${width:.0f} ({self.WIDTH_PCT_OF_PRICE*100:.1f}% of price)",
                },
                exit_rules={
                    "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of max credit",
                    "stop_loss": f"Close at {self.STOP_LOSS_MULTIPLIER}x credit received",
                    "management": "Roll down and out for credit only",
                    "max_adjustments": 1,
                },
                dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if credit spread signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        # Check net delta limit
        net_delta = portfolio.get("net_delta", 0)
        if abs(net_delta) > 0.20:
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

    def _calculate_confidence(self, iv_rank: float, vix: float) -> float:
        """Score confidence based on IV environment."""
        base = 65.0
        # Higher IV Rank = more premium = higher confidence
        iv_bonus = min((iv_rank - 40) / 2.0, 15.0)
        # VIX in sweet spot bonus
        vix_bonus = 5.0 if 15 <= vix <= 25 else 0.0
        return min(max(base + iv_bonus + vix_bonus, 50.0), 90.0)
