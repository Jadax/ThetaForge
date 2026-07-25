"""
Earnings Straddle/Strangle Strategy.
Adapted from ibkr-odte-strategies, Wheel Screener, and Options Strategies Insider research.

Research-backed parameters (17-year backtest, 800+ earnings):
- Options overestimate earnings moves ~70% of the time
- Systematic short straddle wins 38% of individual trades
  BUT profits in winners far exceed losses in losers
- 108% CAGR for systematic short straddles (2006-2023)
- BUT: 83.8% max single-week loss (2009)
- Long straddles: only profitable when IV Rank < 20 at entry
- Expected Move Framework: compare implied vs historical move
  - If expected > historical by 15%+: SELL (iron condor / short straddle)
  - If expected < historical by 15%+: BUY (straddle / strangle)
  - If within 15%: NO TRADE (no edge)
- Avoid: stocks with binary event history
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class EarningsStraddleStrategy(BaseStrategy):
    """
    Earnings Straddle: Trade options around earnings announcements.
    
    Two approaches:
    1. Long straddle (buy the move): Only when IV Rank < 20 (options are cheap)
    2. Short iron condor (sell the move): When expected move > historical move
    
    Uses Expected Move Framework to determine direction.
    """

    # --- Research-Backed Parameters ---
    PREFERRED_DTE_MIN = 7
    PREFERRED_DTE_MAX = 14
    PROFIT_TARGET_LONG_PCT = 100.0  # 100% gain on debit strategies
    PROFIT_TARGET_SHORT_PCT = 50.0  # 50% of credit on credit strategies
    STOP_LOSS_LONG_PCT = 50.0      # Stop at 50% loss on debit
    STOP_LOSS_SHORT_MULTIPLIER = 2.0  # 2x credit on credit

    # Long straddle: only profitable when IV is very cheap
    LONG_IV_RANK_MAX = 20.0  # Research: only profitable bucket

    # Short strategies: only when implied >> historical
    SHORT_IMPLIED_PREMIUM_THRESHOLD = 1.15  # Expected move 15% > historical

    # Risk limits (earnings are high-risk)
    MAX_PORTFOLIO_ALLOCATION_PCT = 5.0  # Max 5% of portfolio
    MAX_SINGLE_POSITION_RISK_PCT = 1.0  # Max 1% per trade

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 5.0,
    ):
        super().__init__(
            name="EarningsStraddle",
            allocation_pct=allocation_pct,
            max_concurrent_positions=3,
            risk_per_trade_pct=1.0,  # Very conservative sizing
            preferred_dte_min=self.PREFERRED_DTE_MIN,
            preferred_dte_max=self.PREFERRED_DTE_MAX,
        )
        self.symbols = symbols or ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for earnings straddle opportunities.
        
        Uses Expected Move Framework:
        - Compare ATM straddle price (implied move) to historical avg earnings move
        - If implied >> historical: sell the move (iron condor)
        - If implied << historical: buy the move (straddle)
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            iv_rank = market_data.get(f"{symbol}_iv_rank", 0)
            days_to_earnings = market_data.get(f"{symbol}_dte", 999)

            if price <= 0:
                continue

            # Only trade within 5 days of earnings
            if days_to_earnings > 5 or days_to_earnings < 0:
                continue

            # --- Expected Move Comparison ---
            implied_move_pct = market_data.get(f"{symbol}_implied_move_pct", 5.0)
            historical_move_pct = market_data.get(f"{symbol}_historical_earnings_move_pct", 5.0)

            if historical_move_pct > 0:
                implied_ratio = implied_move_pct / historical_move_pct
            else:
                implied_ratio = 1.0

            # --- Long Straddle (Buy the Move) ---
            # Only profitable when IV Rank < 20
            if iv_rank < self.LONG_IV_RANK_MAX and implied_ratio < 0.85:
                expiry = self._target_expiry()
                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="BUY",
                    quantity=1,
                    strike=price,  # ATM
                    expiry=expiry,
                    option_type="CALL",  # Would be a straddle in production
                    confidence_score=55.0,  # Lower confidence for long options
                    risk_warning=(
                        "LONG STRADDLE: High risk. IV crush after earnings can "
                        "cause both legs to lose value even if stock moves. "
                        f"Max loss = premium paid. IV Rank: {iv_rank:.0f}."
                    ),
                    legs=[
                        {
                            "action": "BUY",
                            "option_type": "CALL",
                            "strike": round(price, 2),
                            "expiry": expiry,
                            "quantity": 1,
                        },
                        {
                            "action": "BUY",
                            "option_type": "PUT",
                            "strike": round(price, 2),
                            "expiry": expiry,
                            "quantity": 1,
                        },
                    ],
                    entry_rules={
                        "strategy": "Long straddle (buy the move)",
                        "iv_rank_max": self.LONG_IV_RANK_MAX,
                        "implied_vs_historical": f"Implied {implied_move_pct:.1f}% < Historical {historical_move_pct:.1f}%",
                        "entry_timing": "1-3 days before earnings",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_LONG_PCT}% gain on debit",
                        "stop_loss": f"Close at {self.STOP_LOSS_LONG_PCT}% loss of debit",
                        "hold_through_earnings": "YES -- this is the point of the trade",
                        "exit_after": "Close at open next trading day regardless",
                    },
                    dte_target=self.PREFERRED_DTE_MIN,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)

            # --- Short Iron Condor (Sell the Move) ---
            # Only when implied >> historical (market is overpricing the move)
            elif implied_ratio > self.SHORT_IMPLIED_PREMIUM_THRESHOLD:
                expiry = self._target_expiry()
                width = price * 0.05  # 5% wide wings

                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="COMPLEX",
                    quantity=1,
                    strike=price,
                    expiry=expiry,
                    option_type="CALL",
                    confidence_score=65.0,
                    risk_warning=(
                        f"SHORT EARNINGS IRON CONDOR: Selling the expected move. "
                        f"Implied move ({implied_move_pct:.1f}%) > "
                        f"historical ({historical_move_pct:.1f}%). "
                        "Binary risk: large moves cause outsized losses."
                    ),
                    legs=[
                        {"action": "BUY", "option_type": "PUT", "strike": round(price - width * 1.5, 2), "expiry": expiry, "quantity": 1},
                        {"action": "SELL", "option_type": "PUT", "strike": round(price - width, 2), "expiry": expiry, "quantity": 1},
                        {"action": "SELL", "option_type": "CALL", "strike": round(price + width, 2), "expiry": expiry, "quantity": 1},
                        {"action": "BUY", "option_type": "CALL", "strike": round(price + width * 1.5, 2), "expiry": expiry, "quantity": 1},
                    ],
                    entry_rules={
                        "strategy": "Short iron condor (sell the move)",
                        "implied_ratio": f"{implied_ratio:.2f}x (implied / historical)",
                        "implied_move": f"{implied_move_pct:.1f}%",
                        "historical_move": f"{historical_move_pct:.1f}%",
                        "entry_timing": "1-2 days before earnings",
                    },
                    exit_rules={
                        "profit_target": f"Close at {self.PROFIT_TARGET_SHORT_PCT}% of credit",
                        "stop_loss": f"Close at {self.STOP_LOSS_SHORT_MULTIPLIER}x credit",
                        "exit_after": "Close before earnings announcement if possible",
                    },
                    dte_target=self.PREFERRED_DTE_MIN,
                    iv_rank_at_entry=iv_rank,
                )
                self.log_signal(signal)
                signals.append(signal)
            else:
                logger.debug(
                    f"[{self.name}] {symbol}: No edge. "
                    f"Implied ratio = {implied_ratio:.2f} (need >1.15 or <0.85)"
                )

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if earnings straddle signal should be taken."""
        if not self.check_position_limit(portfolio):
            return False

        if self.check_duplicate_symbol(signal.symbol, portfolio):
            return False

        # Check total portfolio allocation to earnings plays
        earnings_positions = [
            p for p in portfolio.get("positions", [])
            if p.get("strategy_name") == self.name
        ]
        total_earnings_risk = sum(p.get("risk_amount", 0) for p in earnings_positions)
        portfolio_value = portfolio.get("net_liquidation", 1)
        current_allocation = (total_earnings_risk / portfolio_value) * 100 if portfolio_value > 0 else 0

        if current_allocation >= self.MAX_PORTFOLIO_ALLOCATION_PCT:
            logger.warning(
                f"[{self.name}] Max portfolio allocation to earnings reached: "
                f"{current_allocation:.1f}%"
            )
            return False

        return True

    def _target_expiry(self) -> str:
        """Calculate target expiry date (10 DTE, nearest Friday)."""
        target_date = datetime.now() + timedelta(days=10)
        days_until_friday = (4 - target_date.weekday()) % 7
        if days_until_friday == 0 and target_date.weekday() != 4:
            days_until_friday = 7
        target_date = target_date + timedelta(days=days_until_friday)
        return target_date.strftime("%Y-%m-%d")
