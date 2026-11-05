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
# Earnings IV-crush condors are entered 1-2 sessions before the print
# (ai_brain.EARNINGS_PLAY_MAX_DAYS) and closed the first session after it:
# once the next print is further out than the entry window, the event passed.
EARNINGS_PLAY_ENTRY_DAYS = 2
# Fallback when the earnings date is unknown: never carry the event trade
# into its expiry week's last sessions.
EARNINGS_PLAY_LAST_DTE = 1
# Close short-dated short premium before a gating macro print (same window the
# Brain uses to refuse new entries). Standard 45-DTE positions hold through:
# their strikes sit outside the expected move and the 2x stop still applies;
# only positions inside MACRO_EXIT_MAX_DTE carry enough gamma to exit.
MACRO_EXIT_DAYS = MACRO_BLACKOUT_DAYS
MACRO_EXIT_MAX_DTE = 14
# Bought (debit) spreads: take half the remaining max profit, stop when half
# the debit is gone, and never hold into expiry week -- a leg finishing in the
# money gets exercised/assigned into shares a small account cannot carry.
DEBIT_PROFIT_TARGET = 0.50
DEBIT_STOP_LOSS = 0.50
DEBIT_EXIT_DTE = 7
# Portfolio rules (r/thetagang + Tastytrade sizing guidance).
MAX_POSITIONS = 8
MAX_CAPITAL_SLICE_PCT = 0.30
# Realized drawdown circuit breaker: if trailing realized P&L is down more
# than this fraction of starting capital, suspend new entries.
DRAWDOWN_BREAKER_PCT = 0.15


@dataclass
class OpenPosition:
    """A single open option position, as reported by the Bridge.

    Short premium (credit) structures carry ``credit_received``; bought
    (debit) spreads carry ``debit_paid``. Single-leg CSPs and covered calls
    have no long strike.
    """
    symbol: str
    strategy: str
    short_strike: float
    long_strike: Optional[float] = None
    expiry: Optional[str] = None
    credit_received: float = 0.0
    quantity: int = 1
    spot: Optional[float] = None
    dte: Optional[int] = None
    # Current per-share mark of the WHOLE position -- its cost to close (sold
    # legs' mids minus bought legs' mids). Comparing only the short leg's mid
    # with the NET credit read a fresh 0.80/0.30 put spread (credit 0.50) as a
    # 1.6x loss on day one and mis-stated every spread's profit.
    short_leg_value: Optional[float] = None
    debit_paid: float = 0.0

    @property
    def max_loss(self) -> Optional[float]:
        if not self.long_strike:
            return None
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


def _days_to_macro(days_to_macro: Optional[int], dte: Optional[int]) -> bool:
    return (days_to_macro is not None and 0 <= days_to_macro <= MACRO_EXIT_DAYS
            and dte is not None and dte <= MACRO_EXIT_MAX_DTE)


def evaluate_debit_position(position: OpenPosition) -> Dict[str, Any]:
    """Exit rules for a bought (debit) spread.

    ``short_leg_value`` is the whole-position mark (sold mids minus bought
    mids), so a debit spread's current value is its negation.
    """
    action, reason, urgency = "hold", "No management trigger fired", "low"
    value = -position.short_leg_value if position.short_leg_value is not None else None
    width = abs((position.long_strike or 0) - position.short_strike)
    max_profit = width - position.debit_paid if width > 0 else None
    if position.dte is not None and position.dte <= DEBIT_EXIT_DTE:
        action, urgency = "close_time", "high"
        reason = f"{position.dte} DTE on a bought spread -- never held into expiry week (assignment risk)"
    elif value is not None and max_profit and value - position.debit_paid >= DEBIT_PROFIT_TARGET * max_profit:
        action, urgency = "close_profit", "medium"
        reason = (f"spread worth {value:.2f} vs {position.debit_paid:.2f} paid: "
                  f">= {DEBIT_PROFIT_TARGET:.0%} of the {max_profit:.2f} max profit")
    elif value is not None and value <= position.debit_paid * (1 - DEBIT_STOP_LOSS):
        action, urgency = "close_loss", "high"
        reason = f"spread worth {value:.2f}, {DEBIT_STOP_LOSS:.0%}+ of the {position.debit_paid:.2f} debit lost"
    return {
        "symbol": position.symbol,
        "strategy": position.strategy,
        "action": action,
        "reason": reason,
        "urgency": urgency,
        "profit_pct": ((value - position.debit_paid) / max_profit) if value is not None and max_profit else None,
        "loss_to_credit": None,
        "dte": position.dte,
        "short_strike": position.short_strike,
        "long_strike": position.long_strike,
        "max_loss": position.debit_paid * 100 * position.quantity,
        "max_profit": max_profit * 100 * position.quantity if max_profit else None,
    }


def evaluate_position(
    position: OpenPosition,
    days_to_earnings: Optional[int] = None,
    days_to_macro: Optional[int] = None,
) -> Dict[str, Any]:
    """Decide one action for one open position.

    Rule order (first match wins):
      1. close_profit      — 50% of max credit captured
      1a. close_post_earnings — earnings IV-crush condor after its print
      2. close_time        — inside the 21-DTE gamma window (earnings
                             condors: only at <= 1 DTE)
      3. close_loss        — short leg worth >= 2x the credit
      4. close_pre_earnings— earnings print inside the blackout window
      5. close_pre_macro   — FOMC/CPI/NFP print inside the blackout window
      6. review_tested     — short strike breached but loss still inside stop
      7. hold              — nothing fired

    Bought (debit) spreads use evaluate_debit_position instead.
    """
    if position.debit_paid > 0:
        return evaluate_debit_position(position)
    action = "hold"
    reason = "No management trigger fired"
    urgency = "low"

    earnings_play = "earnings" in (position.strategy or "").lower()
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
    elif earnings_play and days_to_earnings is not None and days_to_earnings > EARNINGS_PLAY_ENTRY_DAYS:
        action = "close_post_earnings"
        reason = "earnings print has passed — close the IV-crush condor"
        urgency = "high"
    elif earnings_play and position.dte is not None and position.dte <= EARNINGS_PLAY_LAST_DTE:
        action = "close_time"
        reason = f"{position.dte} DTE on an earnings event trade — never carried into expiry"
        urgency = "high"
    elif not earnings_play and position.dte is not None and position.dte <= MANAGE_DTE:
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
    elif not earnings_play and _days_to_earnings(days_to_earnings):
        action = "close_pre_earnings"
        reason = f"earnings in {days_to_earnings} days — no short premium through the event"
        urgency = "high"
    elif _days_to_macro(days_to_macro, position.dte):
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
