"""Trade management engine for open short-premium positions.

Implements the exit framework with the strongest published support
(docs/SOURCES.md):

- **50% of max credit** take-profit: Tastytrade's multi-year research shows
  50% exits beat hold-to-expiration on P&L-per-day-in-trade; the remaining
  premium costs more gamma than it is worth.
- **21-DTE rule**: close or roll regardless of profit/loss once 21 days remain
  — the final three weeks carry disproportionate gamma risk relative to the
  theta left (Tastytrade ~200k-trade DTE study: ~15-20% better risk-adjusted
  returns; Cboe confirms gamma acceleration).
- **Loss-to-credit stop**: a short leg worth 2x the original credit is a
  loss-to-credit ratio of 2 — tastylive's "cut your losses in half" threshold
  (in ~90% of cases, holding to expiry would make it worse).
- **Earnings exit**: a short-premium position held through an earnings print
  is a different, binary trade; close before the event.
- **Macro exit**: FOMC/CPI/NFP prints are market-wide scheduled vol events; no
  short vega through the print — close inside the blackout window.
- **Tested short strike**: a breached strike is where premium sellers lose
  money; if the loss is inside the stop it becomes a flagged review (close or
  roll), otherwise it closes.

Portfolio layer applies the community sizing rules: a position cap, a max
slice of capital in one underlying, and a trailing realized-drawdown circuit
breaker that suspends new entries after a losing streak.

All functions are pure/deterministic so the management loop is unit-testable
and the recommendation is traceable to one rule firing first.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from agents.trade_engine.macro_calendar import MACRO_BLACKOUT_DAYS

# --- exit thresholds --------------------------------------------------------

# Fraction of max credit captured that triggers a take-profit close.
PROFIT_TARGET = 0.50
# Gamma-acceleration window: manage (close/roll) at or below this DTE.
MANAGE_DTE = 21
# Loss-to-credit stop: short leg worth this many times the original credit.
LOSS_TO_CREDIT_STOP = 2.0
# Close short premium before an earnings print within this many days.
EARNINGS_EXIT_DAYS = 7
# Close short premium before a scheduled FOMC/CPI/NFP print inside the macro
# blackout window (same window the Brain uses to refuse new entries).
MACRO_EXIT_DAYS = MACRO_BLACKOUT_DAYS
# Portfolio rules (r/thetagang + Tastytrade sizing guidance).
MAX_POSITIONS = 8
MAX_CAPITAL_SLICE_PCT = 0.30
# Realized drawdown circuit breaker: if trailing realized P&L is down more
# than this fraction of starting capital, suspend new entries.
DRAWDOWN_BREAKER_PCT = 0.15


@dataclass
class OpenPosition:
    """A single open short-premium spread, as reported by the Bridge."""
    symbol: str
    strategy: str
    short_strike: float
    long_strike: float
    expiry: Optional[str] = None
    credit_received: float = 0.0
    quantity: int = 1
    spot: Optional[float] = None
    dte: Optional[int] = None
    short_leg_value: Optional[float] = None  # current mid of the short leg

    @property
    def max_loss(self) -> Optional[float]:
        width = abs(self.long_strike - self.short_strike)
        if width <= 0:
            return None
        return (width - self.credit_received) * 100 * self.quantity

    @property
    def max_profit(self) -> float:
        return self.credit_received * 100 * self.quantity

    @property
    def profit_pct(self) -> Optional[float]:
        """Fraction of max credit captured, 0..1+. None when unmeasurable."""
        if not self.short_leg_value or self.credit_received <= 0:
            return None
        return (self.credit_received - self.short_leg_value) / self.credit_received


def _days_to_earnings(days_to_earnings: Optional[int]) -> bool:
    return days_to_earnings is not None and 0 < days_to_earnings <= EARNINGS_EXIT_DAYS


def _days_to_macro(days_to_macro: Optional[int]) -> bool:
    return days_to_macro is not None and 0 <= days_to_macro <= MACRO_EXIT_DAYS


def evaluate_position(
    position: OpenPosition,
    days_to_earnings: Optional[int] = None,
    days_to_macro: Optional[int] = None,
) -> Dict[str, Any]:
    """Decide one action for one open position.

    Rule order (first match wins):
      1. close_profit      — 50% of max credit captured
      2. close_time        — inside the 21-DTE gamma window
      3. close_loss        — short leg worth >= 2x the credit
      4. close_pre_earnings— earnings print inside the blackout window
      5. close_pre_macro   — FOMC/CPI/NFP print inside the blackout window
      6. review_tested     — short strike breached but loss still inside stop
      7. hold              — nothing fired
    """
    action = "hold"
    reason = "No management trigger fired"
    urgency = "low"

    profit_pct = position.profit_pct
    loss_to_credit = (
        position.short_leg_value / position.credit_received
        if position.credit_received and position.short_leg_value
        else None
    )
    tested = False
    if position.spot and position.short_strike:
        if position.strategy and "call" in position.strategy.lower():
            tested = position.spot > position.short_strike
        else:
            tested = position.spot < position.short_strike

    if profit_pct is not None and profit_pct >= PROFIT_TARGET:
        action = "close_profit"
        reason = f"captured {profit_pct:.0%} of max credit (target {PROFIT_TARGET:.0%})"
        urgency = "medium"
    elif position.dte is not None and position.dte <= MANAGE_DTE:
        action = "close_time"
        reason = (
            f"{position.dte} DTE inside the {MANAGE_DTE}-day gamma window — "
            "close or roll regardless of P/L"
        )
        urgency = "high"
    elif loss_to_credit is not None and loss_to_credit >= LOSS_TO_CREDIT_STOP:
        action = "close_loss"
        reason = f"short leg at {loss_to_credit:.1f}x the credit (stop {LOSS_TO_CREDIT_STOP:.0f}x)"
        urgency = "high"
    elif _days_to_earnings(days_to_earnings):
        action = "close_pre_earnings"
        reason = f"earnings in {days_to_earnings} days — no short premium through the event"
        urgency = "high"
    elif _days_to_macro(days_to_macro):
        action = "close_pre_macro"
        reason = (
            f"scheduled macro event in {days_to_macro} day"
            f"{'s' if days_to_macro != 1 else ''} — no short premium through the print"
        )
        urgency = "high"
    elif tested:
        action = "review_tested"
        reason = (
            f"short strike {position.short_strike} tested (spot {position.spot}) — "
            "review: close or roll, do not sit through the gamma window"
        )
        urgency = "medium"
    elif profit_pct is not None and profit_pct >= 0.25:
        action = "hold"
        reason = f"open winner at {profit_pct:.0%} of max credit — hold toward the {PROFIT_TARGET:.0%} target"
        urgency = "low"

    return {
        "action": action,
        "reason": reason,
        "urgency": urgency,
        "symbol": position.symbol,
        "strategy": position.strategy,
        "profit_pct": round(profit_pct, 4) if profit_pct is not None else None,
        "loss_to_credit": round(loss_to_credit, 2) if loss_to_credit is not None else None,
        "dte": position.dte,
        "short_strike": position.short_strike,
        "long_strike": position.long_strike,
        "max_loss": position.max_loss,
        "max_profit": position.max_profit,
    }


def portfolio_plan(
    positions: List[Dict[str, Any]],
    capital: float,
    *,
    realized_pnl: float = 0.0,
    starting_capital: Optional[float] = None,
    weekly_capital_limit: Optional[float] = None,
    weekly_capital_used: float = 0.0,
) -> Dict[str, Any]:
    """Portfolio-level checks for opening NEW positions."""
    base = starting_capital if starting_capital else capital
    drawdown_pct = 0.0
    if base > 0:
        drawdown_pct = min(realized_pnl, 0) / base

    num_positions = len(positions)
    per_symbol: Dict[str, float] = {}
    for position in positions:
        symbol = str(position.get("symbol", "")).upper()
        per_symbol[symbol] = per_symbol.get(symbol, 0.0) + float(position.get("capital_required", 0) or 0)

    max_symbol_slice = capital * MAX_CAPITAL_SLICE_PCT if capital > 0 else 0.0
    over_allocated = [s for s, used in per_symbol.items() if used > max_symbol_slice]

    violations: List[str] = []
    if num_positions >= MAX_POSITIONS:
        violations.append(f"already at {num_positions} of {MAX_POSITIONS} positions")
    if over_allocated:
        violations.append(f"over-allocated in {', '.join(over_allocated)} (> {MAX_CAPITAL_SLICE_PCT:.0%} of capital each)")
    if drawdown_pct <= -DRAWDOWN_BREAKER_PCT:
        violations.append(f"realized drawdown {drawdown_pct:.0%} breached the {DRAWDOWN_BREAKER_PCT:.0%} circuit breaker")
    if weekly_capital_limit is not None and weekly_capital_used > weekly_capital_limit:
        violations.append(
            f"weekly capital used {weekly_capital_used:.0f} over the {weekly_capital_limit:.0f} limit"
        )

    return {
        "can_open_new": not violations,
        "violations": violations,
        "num_positions": num_positions,
        "max_positions": MAX_POSITIONS,
        "per_symbol_capital": {s: round(v, 2) for s, v in per_symbol.items()},
        "max_symbol_slice": round(max_symbol_slice, 2),
        "realized_drawdown_pct": round(drawdown_pct, 4),
        "drawdown_breaker_pct": DRAWDOWN_BREAKER_PCT,
    }
