"""
0DTE Gamma Blast Strategy.
Adapted from gamma-blast, ibkr-odte-strategies, and 0DTE research papers.

Research-backed parameters:
- 0DTE options volume: ~50% of total S&P 500 options volume
- Unconditional 0DTE strategies: Sharpe 0.41 (below 0.7 viability threshold)
- 42% of accounts experience 50%+ drawdown at 3% risk per trade
- Removing just 8 FOMC days improved P&L by $4,200
- High VIX (>25) 0DTE trades: NEGATIVE expected value
- Best configuration: 1PM entry, 8-delta put spread, 20pt width
  - 86% win rate, Sharpe 0.62
- Afternoon entries outperform morning entries
- 0DTE iron condor: 78% win rate, Sharpe 0.41 (10-delta)
- Monte Carlo: 1.5-2% risk per trade keeps drawdowns manageable

Critical: This is the highest-risk strategy in the book.
Gamma is 5-10x higher than 30-DTE positions.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.strategies.base_strategy import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class GammaBlastStrategy(BaseStrategy):
    """
    0DTE Gamma Blast: Intraday directional trades on SPX/SPY using 0DTE options.
    
    Entry: When daily range is tight (< 1%) and time is afternoon (> 1PM ET)
    Exit: Same day before 3 PM ET
    Risk/Reward: 3x take profit, 1x stop loss (or tighter)
    
    WARNING: This is the highest-risk strategy. Small allocation only.
    """

    # --- Research-Backed Parameters ---
    MAX_DAILY_RANGE_PCT = 1.0  # Enter when range < 1%
    ENTRY_TIME_HOUR = 13       # 1 PM ET (avoids morning volatility)
    EXIT_TIME_HOUR = 15        # Close by 3 PM ET (before final 30 min)
    VIX_MAX = 22.0             # Avoid 0DTE when VIX > 22 (negative EV)
    PROFIT_TARGET_MULTIPLIER = 3.0  # 3x profit target
    STOP_LOSS_MULTIPLIER = 1.0     # 1x stop loss (max premium)
    RISK_PER_TRADE_PCT = 1.0       # Max 1% risk per trade (research: 1.5-2%)

    # Skip high-impact event days
    SKIP_EVENTS = {"FOMC", "CPI", "NFP", "GDP", "PPI", "CPI_CORE"}

    def __init__(
        self,
        symbols: List[str] = None,
        allocation_pct: float = 5.0,
    ):
        super().__init__(
            name="GammaBlast",
            allocation_pct=allocation_pct,
            max_concurrent_positions=1,
            risk_per_trade_pct=self.RISK_PER_TRADE_PCT,
            preferred_dte_min=0,
            preferred_dte_max=0,
            max_vix=self.VIX_MAX,
        )
        self.symbols = symbols or ["SPX", "SPY"]

    async def scan(self, market_data: Dict[str, Any]) -> List[TradeSignal]:
        """
        Scan for 0DTE Gamma Blast opportunities.
        
        Entry criteria (ALL must be met):
        1. Time > 1 PM ET (avoid morning volatility)
        2. Daily range < 1% (compression = explosion potential)
        3. VIX < 22 (negative EV above this)
        4. Not a high-impact event day
        5. SPX/SPY only
        """
        signals = []

        for symbol in self.symbols:
            price = market_data.get(f"{symbol}_price", 0)
            daily_range_pct = market_data.get(f"{symbol}_daily_range_pct", 10.0)
            vix = market_data.get("vix", 20.0)

            if price <= 0:
                continue

            # --- Entry Filters ---
            # VIX filter
            if vix > self.VIX_MAX:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: VIX {vix:.1f} > {self.VIX_MAX}"
                )
                continue

            # Daily range filter
            if daily_range_pct >= self.MAX_DAILY_RANGE_PCT:
                logger.debug(
                    f"[{self.name}] Skipping {symbol}: range {daily_range_pct:.2f}% >= {self.MAX_DAILY_RANGE_PCT}%"
                )
                continue

            # Event day filter
            events = market_data.get("events_today", set())
            if events & self.SKIP_EVENTS:
                logger.info(f"[{self.name}] Skipping {symbol}: event day ({events})")
                continue

            # --- Directional Bias ---
            # Use intraday momentum for direction
            intraday_trend = market_data.get(f"{symbol}_intraday_trend", "neutral")

            if intraday_trend == "bullish":
                option_type = "CALL"
                action = "BUY"
            elif intraday_trend == "bearish":
                option_type = "PUT"
                action = "BUY"
            else:
                # No clear direction: use put credit spread instead
                option_type = "PUT"
                action = "SELL"

            expiry = datetime.now().strftime("%Y-%m-%d")  # 0DTE

            if action == "BUY":
                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action=action,
                    quantity=1,
                    strike=price,  # ATM
                    expiry=expiry,
                    option_type=option_type,
                    confidence_score=55.0,  # Low confidence -- this is speculative
                    risk_warning=(
                        f"0DTE {option_type}: EXTREME RISK. Gamma is maximized. "
                        f"Price changes are extreme. Close by 3 PM ET. "
                        f"Max loss = premium paid. VIX: {vix:.1f}."
                    ),
                    entry_rules={
                        "time_window": f"After {self.ENTRY_TIME_HOUR}:00 ET",
                        "daily_range": f"< {self.MAX_DAILY_RANGE_PCT}% (current: {daily_range_pct:.2f}%)",
                        "vix_max": self.VIX_MAX,
                        "skip_events": list(self.SKIP_EVENTS),
                        "risk_reward": f"{self.PROFIT_TARGET_MULTIPLIER}x profit / {self.STOP_LOSS_MULTIPLIER}x stop",
                    },
                    exit_rules={
                        "profit_target": f"{self.PROFIT_TARGET_MULTIPLIER}x premium paid",
                        "stop_loss": f"{self.STOP_LOSS_MULTIPLIER}x premium paid",
                        "time_stop": "MUST close by 3 PM ET",
                        "max_hold": "Same day -- never hold overnight",
                    },
                    dte_target=0,
                    iv_rank_at_entry=market_data.get(f"{symbol}_iv_rank", 0),
                )
                self.log_signal(signal)
                signals.append(signal)

            elif action == "SELL":
                # Put credit spread variant (afternoon 0DTE)
                signal = TradeSignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    action="COMPLEX",
                    quantity=1,
                    strike=price * 0.99,  # Slightly OTM short put
                    expiry=expiry,
                    option_type="PUT",
                    confidence_score=65.0,
                    risk_warning=(
                        f"0DTE put credit spread: selling premium on same-day options. "
                        f"86% win rate (research) but gamma is extreme. "
                        f"Close by 3 PM ET. VIX: {vix:.1f}."
                    ),
                    legs=[
                        {"action": "BUY", "option_type": "PUT", "strike": round(price * 0.98, 2), "expiry": expiry, "quantity": 1},
                        {"action": "SELL", "option_type": "PUT", "strike": round(price * 0.99, 2), "expiry": expiry, "quantity": 1},
                    ],
                    entry_rules={
                        "time_window": f"After {self.ENTRY_TIME_HOUR}:00 ET",
                        "daily_range": f"< {self.MAX_DAILY_RANGE_PCT}% (current: {daily_range_pct:.2f}%)",
                        "vix_max": self.VIX_MAX,
                        "delta_target": "-8 to -10 (8-10 delta short put)",
                        "width": "$1-2 on SPY",
                    },
                    exit_rules={
                        "profit_target": "Close at 50% of credit (same day)",
                        "stop_loss": "2x credit received",
                        "time_stop": "MUST close by 3 PM ET",
                        "max_hold": "Same day -- never hold overnight",
                    },
                    dte_target=0,
                    iv_rank_at_entry=market_data.get(f"{symbol}_iv_rank", 0),
                )
                self.log_signal(signal)
                signals.append(signal)

        return signals

    async def evaluate(self, signal: TradeSignal, portfolio: Dict[str, Any]) -> bool:
        """Evaluate if 0DTE signal should be taken."""
        # Very strict position limits for 0DTE
        if not self.check_position_limit(portfolio):
            return False

        # No 0DTE positions alongside other short volatility positions
        short_vix_positions = [
            p for p in portfolio.get("positions", [])
            if p.get("strategy_name") in ("IronCondor", "CreditSpread", "Wheel")
            and p.get("quantity", 0) < 0
        ]
        if short_vix_positions:
            logger.warning(
                f"[{self.name}] Already have {len(short_vix_positions)} short vol positions. "
                "Skipping 0DTE to avoid correlation risk."
            )
            return False

        return True
