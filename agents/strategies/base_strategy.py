"""
Base strategy class for all ThetaForge strategies.
Enforces common interface and risk management constraints.

Research-backed defaults:
- Profit target: 50% of max profit (single most impactful rule)
- Stop loss: 2x credit received (cuts max drawdown from 21% to 17%)
- Max position risk: 2% of portfolio per trade
- Preferred DTE: 30-45 days (optimal theta decay)
"""
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TradeSignal:
    """Lightweight trade signal (avoids pydantic import for strategy logic)."""
    def __init__(
        self,
        strategy_name: str,
        symbol: str,
        action: str,
        quantity: int,
        strike: float,
        expiry: str,
        option_type: str,
        limit_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        confidence_score: float = 0.0,
        risk_warning: str = "",
        legs: Optional[List[Dict[str, Any]]] = None,
        net_debit: Optional[float] = None,
        net_credit: Optional[float] = None,
        max_loss: Optional[float] = None,
        max_profit: Optional[float] = None,
        spread_width: Optional[float] = None,
        dte_target: Optional[int] = None,
        iv_rank_at_entry: Optional[float] = None,
        entry_rules: Optional[Dict[str, Any]] = None,
        exit_rules: Optional[Dict[str, Any]] = None,
    ):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.action = action
        self.quantity = quantity
        self.strike = strike
        self.expiry = expiry
        self.option_type = option_type
        self.limit_price = limit_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.confidence_score = confidence_score
        self.risk_warning = risk_warning
        self.legs = legs
        self.net_debit = net_debit
        self.net_credit = net_credit
        self.max_loss = max_loss
        self.max_profit = max_profit
        self.spread_width = spread_width
        self.dte_target = dte_target
        self.iv_rank_at_entry = iv_rank_at_entry
        self.entry_rules = entry_rules or {}
        self.exit_rules = exit_rules or {}

    def __repr__(self):
        return (
            f"<TradeSignal {self.strategy_name} "
            f"{self.action} {self.quantity}x {self.symbol} "
            f"{self.expiry} {self.strike}{self.option_type[0]} "
            f"conf={self.confidence_score}>"
        )


class BaseStrategy(ABC):
    """
    Abstract base class for all ThetaForge strategies.
    Provides common risk management checks, profit target logic,
    and position management framework.

    Subclasses must implement:
        - scan(market_data) -> List[TradeSignal]
        - evaluate(signal, portfolio) -> bool
    """

    def __init__(
        self,
        name: str,
        allocation_pct: float = 10.0,
        profit_target_pct: float = 50.0,
        stop_loss_multiplier: float = 2.0,
        max_concurrent_positions: int = 5,
        risk_per_trade_pct: float = 2.0,
        preferred_dte_min: int = 30,
        preferred_dte_max: int = 45,
        min_iv_rank: float = 0.0,
        max_iv_rank: float = 100.0,
        min_vix: Optional[float] = None,
        max_vix: Optional[float] = None,
    ):
        self.name = name
        self.allocation_pct = allocation_pct
        self.is_active = True

        # Research-backed management defaults
        self.profit_target_pct = profit_target_pct       # Close at 50% of max profit
        self.stop_loss_multiplier = stop_loss_multiplier  # Stop at 2x credit
        self.max_concurrent_positions = max_concurrent_positions
        self.risk_per_trade_pct = risk_per_trade_pct

        # Entry filters
        self.preferred_dte_min = preferred_dte_min
        self.preferred_dte_max = preferred_dte_max
        self.min_iv_rank = min_iv_rank
        self.max_iv_rank = max_iv_rank
        self.min_vix = min_vix
        self.max_vix = max_vix

    @abstractmethod
    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for potential trade opportunities based on strategy logic.
        Returns a list of TradeSignal objects.
        """
        pass

    @abstractmethod
    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """
        Evaluate if a signal should be taken based on current portfolio state
        and risk limits.
        """
        pass

    def check_iv_filter(self, iv_rank: float) -> bool:
        """Check if IV Rank falls within this strategy's preferred range."""
        return self.min_iv_rank <= iv_rank <= self.max_iv_rank

    def check_vix_filter(self, vix: float) -> bool:
        """Check if VIX is within acceptable range."""
        if self.min_vix is not None and vix < self.min_vix:
            return False
        if self.max_vix is not None and vix > self.max_vix:
            return False
        return True

    def check_position_limit(self, portfolio: Dict[str, Any]) -> bool:
        """Check if adding a new position exceeds the concurrent limit."""
        positions = portfolio.get("positions", [])
        strategy_positions = [
            p for p in positions
            if p.get("strategy_name") == self.name
        ]
        return len(strategy_positions) < self.max_concurrent_positions

    def check_duplicate_symbol(self, symbol: str, portfolio: Dict[str, Any]) -> bool:
        """Returns True if we already have a position in this symbol."""
        for pos in portfolio.get("positions", []):
            if pos.get("symbol") == symbol:
                return True
        return False

    def check_portfolio_risk(self, portfolio: Dict[str, Any]) -> bool:
        """Check if total portfolio risk is within limits."""
        total_risk = portfolio.get("total_at_risk_pct", 0.0)
        return total_risk < 100.0  # Can be made configurable

    def calculate_profit_target_price(
        self, entry_credit: float, spread_width: float, is_short: bool = True
    ) -> float:
        """
        Calculate the price at which to take profit.
        For credit spreads: buy back at profit_target_pct of credit received.
        For debit spreads: sell at profit_target_pct above debit paid.
        """
        if is_short:
            # Credit strategy: profit = credit - buyback; target = credit * (1 - target%)
            return entry_credit * (1.0 - self.profit_target_pct / 100.0)
        else:
            # Debit strategy: profit = sell price - debit; target = debit * (1 + target%)
            return entry_credit * (1.0 + self.profit_target_pct / 100.0)

    def calculate_stop_loss_price(
        self, entry_credit: float, is_short: bool = True
    ) -> float:
        """
        Calculate stop loss price.
        For credit strategies: stop when loss reaches stop_loss_multiplier * credit.
        """
        if is_short:
            return entry_credit * self.stop_loss_multiplier
        return entry_credit * 0.5  # For debit strategies, stop at 50% loss

    def should_close_at_dte(self, current_dte: int) -> bool:
        """
        Returns True if position should be closed because DTE is too low.
        Gamma risk accelerates below 14 DTE.
        """
        return current_dte <= 7

    def log_signal(self, signal: TradeSignal):
        """Log a trade signal for audit trail."""
        logger.info(
            f"[{self.name}] Signal: {signal.action} {signal.quantity}x "
            f"{signal.symbol} {signal.expiry} {signal.strike}{signal.option_type[0]} "
            f"(confidence: {signal.confidence_score:.1f})"
        )

    def __repr__(self):
        return f"<Strategy {self.name} (Allocation: {self.allocation_pct}%, Active: {self.is_active})>"
