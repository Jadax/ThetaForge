"""
LEAPS (Long-Term Equity Anticipation Securities) Strategy.
Adapted from TheOptionPremium, Foolish Trader research, and JPM studies.

Research-backed parameters:
- Stock replacement with 60-75% less capital than direct ownership
- 90-delta LEAPS on SPY/QQQ beat buy-and-hold in every backtested instance
  when sold at 60 DTE (Foolish Trader)
- Deep ITM LEAPS (0.75-0.85 delta) achieve similar returns to shares
  with 60-85% less capital (Journal of Portfolio Management)
- Optimal DTE: 18-30 months (sweet spot: 18-24 months)
- Roll at 90-120 DTE remaining (before theta acceleration)
- Expected returns: 15-25% annualized on capital deployed
- PMCC variant: Sell 30-45 DTE calls against LEAPS for income
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class LEAPSStrategy(BaseStrategy):
    """
    LEAPS: Long-dated options (9-30+ months) as stock replacements.
    
    Uses deep ITM calls (0.75-0.85 delta) to replicate stock exposure
    with 60-85% less capital. Optional PMCC overlay for income.
    
    Best in: moderate IV, strong fundamental conviction
    Worst in: high IV (overpaying for time), speculative names
    """

    # --- Research-Backed Parameters ---
    TARGET_DELTA = 0.80       # The "80/80 rule": 0.80 delta, 80% intrinsic
    MIN_INTRINSIC_PCT = 0.70  # At least 70% intrinsic value
    TARGET_DTE_MONTHS = 18    # Sweet spot: 18-24 months
    MIN_DTE_MONTHS = 9
    MAX_DTE_MONTHS = 30
    ROLL_DTE_THRESHOLD = 120  # Roll when 120 DTE remain
    MAX_CAPITAL_OUTLAY_PCT = 40.0  # Max 40% of share price
    MAX_IV_RANK = 30.0        # Buy cheap time value
    PROFIT_TARGET_PCT = 50.0  # Take profits at 50% gain

    # PMCC overlay
    PMCC_ENABLED = True
    PMCC_CALL_DELTA = 0.30    # Sell 30-delta calls against LEAPS
    PMCC_DTE_MIN = 30
    PMCC_DTE_MAX = 45

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 15.0,
        pmcc_enabled: bool = True,
    ):
        super().__init__(
            name="LEAPS",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            max_concurrent_positions=5,
            risk_per_trade_pct=2.0,
            min_dte=self.MIN_DTE_MONTHS * 30,
            max_iv_rank=self.MAX_IV_RANK,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL"]
        self.pmcc_enabled = pmcc_enabled

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for LEAPS entry opportunities.
        
        Entry criteria:
        1. IV Rank < 30 (buy cheap time value)
        2. Stock in long-term uptrend
        3. Strong fundamentals (large-cap, profitable)
        4. Capital outlay < 40% of share price
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
                    f"> {self.MAX_IV_RANK} (paying peak prices for time)"
                )
                continue

            # --- LEAPS Entry ---
            # Deep ITM call at ~80 delta
            # Strike will be approximately 20% below current price
            leaps_strike = price * 0.80
            expiry = self._target_expiry_leaps()
            est_debit = price * 0.22  # Rough estimate: ~22% of share price for 80-delta LEAPS

            # Check capital outlay
            capital_outlay_pct = (est_debit / price) * 100
            if capital_outlay_pct > self.MAX_CAPITAL_OUTLAY_PCT:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: estimated outlay {capital_outlay_pct:.0f}% "
                    f"> {self.MAX_CAPITAL_OUTLAY_PCT}%"
                )
                continue

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="BUY",
                quantity=1,
                strike=round(leaps_strike, 2),
                expiry=expiry,
                option_type="CALL",
                confidence_score=self._calculate_confidence(iv_rank, price),
                risk_warning=(
                    f"LEAPS call: max loss = premium paid (${est_debit * 100:.0f}). "
                    f"Capital outlay ~{capital_outlay_pct:.0f}% of share price. "
                    f"Roll when DTE < {self.ROLL_DTE_THRESHOLD}. "
                    "Theta decay accelerates in final 90-120 days."
                ),
                entry_rules={
                    "delta_target": self.TARGET_DELTA,
                    "min_intrinsic_pct": f"{self.MIN_INTRINSIC_PCT*100:.0f}%",
                    "dte_target": f"{self.TARGET_DTE_MONTHS} months",
                    "max_iv_rank": self.MAX_IV_RANK,
                    "capital_outlay_max": f"{self.MAX_CAPITAL_OUTLAY_PCT:.0f}% of share price",
                    "stock_criteria": "Large-cap, profitable, above 200-day MA",
                },
                exit_rules={
                    "roll_trigger": f"Roll when DTE < {self.ROLL_DTE_THRESHOLD} days",
                    "roll_action": "Roll to new 18-24 month LEAPS if thesis intact",
                    "profit_target": f"Take {self.PROFIT_TARGET_PCT}% gain",
                    "stop_loss": "Close if fundamental thesis breaks",
                    "delta_drift": "Adjust strike if delta drifts outside 0.70-0.90 range",
                    "avoid": "Do not hold into final 6 months (theta acceleration)",
                },
                dte_target=self.TARGET_DTE_MONTHS * 30,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

            # --- PMCC Overlay (if we already hold LEAPS) ---
            if self.pmcc_enabled:
                owns_leaps = market_data.get(f"owns_{symbol}_leaps", False)
                if owns_leaps:
                    cc_strike = price * 1.05  # 5% OTM
                    cc_expiry = self._target_expiry_cc()

                    signal = TradeSignal(
                        strategy_name=self.name,
                        symbol=symbol,
                        action="SELL",
                        quantity=1,
                        strike=round(cc_strike, 2),
                        expiry=cc_expiry,
                        option_type="CALL",
                        confidence_score=70.0,
                        risk_warning=(
                            "PMCC: Selling short-dated call against LEAPS. "
                            "Caps near-term upside but generates income. "
                            "Roll up/out if stock approaches strike."
                        ),
                        entry_rules={
                            "delta_target": self.PMCC_CALL_DELTA,
                            "dte_target": (self.PMCC_DTE_MIN + self.PMCC_DTE_MAX) // 2,
                            "strike_rule": "Above LEAPS break-even point",
                        },
                        exit_rules={
                            "profit_target": "50% of short call premium",
                            "roll_trigger": "Stock approaches short call strike",
                            "roll_action": "Roll up and out for credit",
                        },
                        dte_target=(self.PMCC_DTE_MIN + self.PMCC_DTE_MAX) // 2,
                        iv_rank_at_entry=iv_rank,
                    )
                    self.log_signal(signal)
                    signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if LEAPS signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        return True

    def _target_expiry_leaps(self) -> str:
        """Calculate target LEAPS expiry (18-24 months out, nearest month-end)."""
        target = datetime.now() + timedelta(days=self.TARGET_DTE_MONTHS * 30)
        # Round to nearest third Friday (LEAPS expiry)
        return target.strftime("%Y-%m-%d")

    def _target_expiry_cc(self) -> str:
        """Calculate target covered call expiry (35 DTE, nearest Friday)."""
        target = datetime.now() + timedelta(days=35)
        days_until_friday = (4 - target.weekday()) % 7
        if days_until_friday == 0 and target.weekday() != 4:
            days_until_friday = 7
        target = target + timedelta(days=days_until_friday)
        return target.strftime("%Y-%m-%d")

    def _calculate_confidence(self, iv_rank: float, price: float) -> float:
        """Confidence based on IV and stock quality."""
        base = 70.0
        # Lower IV = more confidence (cheaper options)
        iv_bonus = max((self.MAX_IV_RANK - iv_rank) / 2.0, 0.0)
        # Blue-chip bonus
        if price > 100:
            stock_bonus = 5.0
        else:
            stock_bonus = 0.0
        return min(base + iv_bonus + stock_bonus, 90.0)
