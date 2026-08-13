"""
Equity position manager — exit rules for open stock/ETF longs.

Mirrors the options `trade_manager.py` contract: a pure rule engine returning
an (action, reason) pair, first-match-wins, so the auto-manager can close a
position or leave it alone without making any decision of its own.

Rules (in order):
  1. close_stop       — price at or below the ATR stop.
  2. close_trail      — price at or below the chandelier trail (2x ATR below
                        the highest high since entry) once the trade is at
                        least +1R in profit.
  3. close_profit     — price at or above the 2R target.
  4. close_time       — position held past the max hold window without
                        progress toward target.
  5. close_pre_earnings — earnings within the blackout window (event gap risk).
  6. close_pre_macro  — scheduled FOMC/CPI/NFP within the blackout window.
  otherwise → hold.

The manager returns the updated highest_high so the caller can persist it; the
trail only ever ratchets in the trade's favor.
"""
from typing import Dict, Optional, Tuple

# Once the trade is +1R in profit the stop starts trailing by 2 ATR below the
# running high (chandelier). Below +1R the hard stop stays at the entry stop.
TRAIL_ARM_R_MULTIPLIER = 1.0
TRAIL_ATR_MULTIPLIER = 2.0
# Max hold window before the time exit fires (trading days).
MAX_HOLD_DAYS = 60
# Event blackout windows in calendar days.
EARNINGS_BLACKOUT_DAYS = 2
MACRO_BLACKOUT_DAYS = 2

CLOSE_ACTIONS = {
    "close_stop", "close_trail", "close_profit", "close_time",
    "close_pre_earnings", "close_pre_macro",
}


def evaluate_position(
    symbol: str,
    current_price: float,
    entry_price: float,
    stop_price: float,
    target_price: float,
    highest_high: float,
    atr: Optional[float],
    risk_per_share: float,
    days_held: int = 0,
    days_to_earnings: Optional[int] = None,
    days_to_macro: Optional[int] = None,
    trail_atr_multiplier: float = TRAIL_ATR_MULTIPLIER,
) -> Tuple[str, str, float]:
    """Return (action, reason, updated_highest_high)."""
    updated_high = max(float(highest_high or 0), float(current_price or 0))
    price = float(current_price or 0)
    entry = float(entry_price or 0)
    stop = float(stop_price or 0)
    target = float(target_price or 0)
    risk = float(risk_per_share or 0)

    if price <= 0 or entry <= 0 or stop <= 0:
        return "hold", "missing pricing data", updated_high

    if price <= stop:
        return "close_stop", f"{symbol} at {price:.2f} hit the {stop:.2f} stop", updated_high

    if days_to_macro is not None and days_to_macro <= MACRO_BLACKOUT_DAYS:
        return "close_pre_macro", (
            f"Scheduled macro print in {days_to_macro}d; standing aside"
        ), updated_high

    if days_to_earnings is not None and days_to_earnings <= EARNINGS_BLACKOUT_DAYS:
        return "close_pre_earnings", (
            f"Earnings in {days_to_earnings}d; standing aside"
        ), updated_high

    # Trail only arms once the trade has moved +1R in our favor.
    if risk > 0 and atr and atr > 0:
        profit_r = (price - entry) / risk
        if profit_r >= TRAIL_ARM_R_MULTIPLIER:
            chandelier = updated_high - trail_atr_multiplier * atr
            if chandelier > stop:
                stop = chandelier
            if price <= stop:
                return "close_trail", (
                    f"{symbol} broke the {stop:.2f} chandelier trail (2x ATR)"
                ), updated_high

    if target > 0 and price >= target:
        return "close_profit", f"{symbol} reached the {target:.2f} target", updated_high

    if days_held >= MAX_HOLD_DAYS:
        return "close_time", f"{symbol} held {days_held}d without a managed exit", updated_high

    return "hold", "above stop, below target", updated_high
