"""
Covered Call Strategy.
Adapted from OpScanBot and optionDash with research-backed parameters.

Research-backed parameters (2004-2026):
- Short volatility component has highest Sharpe ratio of strategy components
- CC outperform buy-and-hold on risk-adjusted basis (SSRN study)
- 30-delta sweet spot: best balance of premium and participation
- ATM calls: max premium, max downside cushion, lowest upside capture
- 10-20% OTM: less premium, more upside participation
- Roll up when stock approaches strike
- Do NOT hold through final week (gamma risk)
- CC reduces upside more than it cushions downside (myth dispelling)
- BXM benchmark: 5.64% CAGR, BXMD (30-delta): 8.12% CAGR
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class CoveredCallStrategy(BaseStrategy):
    """
    Covered Call: Own 100+ shares, sell OTM call against the position.
    Generates income from existing stock holdings.
    Capped upside, reduced downside (slightly).
    """

    # --- Research-Backed Parameters ---
    SHORT_DELTA = 30          # 30-delta: best balance of premium and participation
    OTM_PCT = 0.05            # 5% OTM default
    PREFERRED_DTE_MIN = 30
    PREFERRED_DTE_MAX = 45
    PROFIT_TARGET_PCT = 50.0  # Close at 50% of premium received
    ROLL_UP_THRESHOLD = 0.02  # Roll up when stock is within 2% of strike

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 10.0,
        short_delta: int = None,
    ):
        super().__init__(
            name="CoveredCall",
            allocation_pct=allocation_pct,
            profit_target_pct=self.PROFIT_TARGET_PCT,
            max_concurrent_positions=10,
            risk_per_trade_pct=1.0,  # Lower risk since we own the shares
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
        )
        self.symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT"]
        self.short_delta = short_delta or self.SHORT_DELTA

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for covered call opportunities on existing share positions.
        
        Only generates signals when we own 100+ shares.
        Uses 30-delta for balanced premium vs participation.
        """
        signals = []

        for symbol in self.symbols:
            # Must own the stock
            owns_shares = market_data.get(f"owns_{symbol}", False)
            if not owns_shares:
                continue

            shares = market_data.get(f"{symbol}_shares", 0)
            if shares < 100:
                continue

            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)

            if price <= 0:
                continue

            # --- Strike Selection ---
            # 30-delta OTM: approximately 3-5% above current price
            call_strike = price * (1.0 + self.OTM_PCT)
            expiry = self._target_expiry()

            num_contracts = shares // 100

            signal = TradeSignal(
                strategy_name=self.name,
                symbol=symbol,
                action="SELL",
                quantity=num_contracts,
                strike=round(call_strike, 2),
                expiry=expiry,
                option_type="CALL",
                confidence_score=self._calculate_confidence(iv_rank),
                risk_warning=(
                    f"Covered call: caps upside at {call_strike:.2f}. "
                    f"Shares may be called away if ITM at expiration. "
                    f"Reduces upside more than it cushions downside."
                ),
                entry_rules={
                    "delta_target": f"{self.short_delta}/100",
                    "dte_target": (self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                    "prerequisite": f"Must own {num_contracts * 100} shares of {symbol}",
                    "strike_rule": "3-5% OTM for balanced premium vs participation",
                },
                exit_rules={
                    "profit_target": f"Close at {self.PROFIT_TARGET_PCT}% of premium",
                    "roll_up_trigger": f"Stock within {self.ROLL_UP_THRESHOLD*100:.0f}% of strike",
                    "roll_up_action": "Roll up and out for net credit",
                    "assignment": "Accept if called away; return to CSP phase (Wheel)",
                    "gamma_warning": "Close or roll before final 5 days",
                },
                dte_target=(self.PREFERRED_DTE_MIN + self.PREFERRED_DTE_MAX) // 2,
                iv_rank_at_entry=iv_rank,
            )
            self.log_signal(signal)
            signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if covered call signal should be taken."""
        # Must own the stock
        for pos in portfolio.get("positions", []):
            if pos.get("symbol") == signal.symbol and pos.get("quantity", 0) >= 100:
                return True
        return False

    def _target_expiry(self) -> str:
        """Calculate target expiry date (35 DTE, nearest Friday)."""
        target_date = datetime.now() + timedelta(days=35)
        days_until_friday = (4 - target_date.weekday()) % 7
        if days_until_friday == 0 and target_date.weekday() != 4:
            days_until_friday = 7
        target_date = target_date + timedelta(days=days_until_friday)
        return target_date.strftime("%Y-%m-%d")

    def _calculate_confidence(self, iv_rank: float) -> float:
        """Confidence based on IV environment."""
        base = 70.0
        # Higher IV = more premium = higher confidence
        iv_bonus = min(iv_rank / 3.0, 15.0)
        return min(base + iv_bonus, 90.0)
