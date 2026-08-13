"""High-win-rate entry gates distilled from the trader playbook.

Rules sourced from the public research surveyed in docs/SOURCES.md:

- Tastytrade / tastylive 200k-trade studies: enter short premium at 30-45
  DTE, manage at 21 DTE or 50% of max credit (whichever first). New
  premium-selling entries inside 21 DTE sit in the gamma-acceleration zone.
- Cboe Options Institute: 25-30-delta short strikes at 45 DTE run ~70-75%
  win rates held to expiry; the win rate rises further when the short strike
  sits at/beyond the 1-SD expected move.
- William O'Neil / IBD (Investor's Business Daily): trade only leaders —
  relative strength in the upper quartile vs the market, price above the
  50/200-day averages. Selling puts into laggards or into a confirmed
  downtrend is the classic "picking up pennies in front of a steamroller".
- r/thetagang / ThetaScanner: avoid earnings (IV crush is an event, not an
  edge); never more than a fixed slice of capital per underlying.

These gates are *hard vetoes* applied to a candidate after the generic
quality gates (POP, IV rank, credit-to-width, probability-of-touch). They
exist so the "best" structure on a strong-looking chain is still refused when
the *context* (trend, relative strength, time, earnings) is wrong — a high
win rate comes from context selection, not just strike mechanics.
"""
from typing import Optional, Tuple

# --- configurable thresholds ------------------------------------------------

# Gamma-acceleration floor for NEW short-premium entries. Inside 21 DTE the
# remaining theta is small and the gamma tail is large; hold-to-expiry has no
# place in a systematic book (you manage those open positions, you don't mint
# them). Upper bound avoids locking capital into months of flat decay.
MIN_SELL_DTE = 21
MAX_ENTRY_DTE = 60
# Long-premium (debit) structures are bought, so the gamma concern inverts;
# keep a sanity floor so we never buy into the last week's time value.
MIN_BUY_DTE = 14

# Short strike must sit at least this many 1-SD expected moves from spot for
# a new credit structure. 1.0 x expected move ≈ the 68% expiry-probability
# boundary, the minimum POP the community treats as a "high-win-rate" trade.
EXPECTED_MOVE_MIN_MULTIPLE = 1.0

# No NEW short-premium entries with earnings within this many days. The
# earnings move is binary; IV crush makes the credit look rich without being
# an edge (thetagang rule).
EARNINGS_BLACKOUT_DAYS = 7

# IBD "L" rule, approximated with free data: 6-month relative strength vs SPY
# (symbol 126-trading-day return minus SPY's). A name trailing the market by
# more than this is a laggard — do not sell directional premium against it.
RS_MIN_FOR_DIRECTIONAL_SELL = -0.10

# --- strategy taxonomy -----------------------------------------------------

_SELL_PREMIUM_TYPES = {
    "bull_put", "bear_call", "iron_condor", "cash_secured_put",
    "covered_call", "short_straddle", "short_strangle",
    "iron_butterfly", "jade_lizard", "bull_put_credit",
    "bear_call_credit", "iron_condor_weekly", "credit_spread_weekly",
    "wheel",
}
# This is the single source of truth for "is this a premium-selling
# strategy" across the whole system -- trade_manager.py and ai_brain.py both
# import is_premium_selling() from here rather than keeping their own lists,
# after those lists were found to have silently drifted (missing
# jade_lizard/wheel in some, an extra substring-match false-negative risk in
# others). deployment/vm_auto_manager.py can't import this (it runs on the
# VM without the full package tree) and mirrors this exact set manually --
# keep that mirror in sync if this set ever changes.
_BULL_TYPES = {
    "bull_put", "bull_put_credit", "cash_secured_put", "covered_call",
    "bull_call", "call_debit", "call_debit_spread", "bull_call_spread",
}
_BEAR_TYPES = {
    "bear_call", "bear_call_credit", "bear_put", "bear_put_spread",
    "put_debit", "put_debit_spread",
}


def strategy_bias(strategy: str) -> str:
    """Map a strategy name/type to a directional bucket: bull/bear/range."""
    key = (strategy or "").lower()
    if key in _BULL_TYPES:
        return "bull"
    if key in _BEAR_TYPES:
        return "bear"
    return "range"


def is_premium_selling(strategy: str) -> bool:
    return (strategy or "").lower() in _SELL_PREMIUM_TYPES


def trend_alignment_ok(strategy: str, trend: Optional[str]) -> Tuple[bool, str]:
    """A directional structure must not fight the prevailing trend.

    bull = sell puts / buy calls, bear = sell calls / buy puts,
    range = neutral premium (iron condor). A bull structure in a confirmed
    downtrend is refused even when IV is rich — that is where premium sellers
    lose the trade.
    """
    bias = strategy_bias(strategy)
    trend = (trend or "neutral").lower()
    if bias == "bull" and trend == "bearish":
        return False, "trend_mismatch: bull structure into a confirmed downtrend"
    if bias == "bear" and trend == "bullish":
        return False, "trend_mismatch: bear structure into a confirmed uptrend"
    return True, ""


def expected_move_buffer_ok(
    strategy: str,
    short_strike: Optional[float],
    spot: Optional[float],
    expected_move_1sd: Optional[float],
) -> Tuple[bool, str]:
    """Short premium strikes must sit at/outside the 1-SD expected move.

    A short strike inside the 1-SD boundary has less than the ~68% expiry POP
    that defines the strategy's win-rate profile, so it is refused even if the
    credit looks attractive. Debit structures (bought premium) are unaffected.
    """
    if not is_premium_selling(strategy):
        return True, ""
    if not short_strike or not spot or not expected_move_1sd or expected_move_1sd <= 0:
        return True, ""  # no strike data → not our gate to veto on
    distance = abs(spot - short_strike)
    multiple = distance / expected_move_1sd
    if multiple < EXPECTED_MOVE_MIN_MULTIPLE:
        return (
            False,
            f"short strike {distance:.2f} from spot is inside the 1-SD expected move "
            f"({expected_move_1sd:.2f}) → POP below ~68%",
        )
    return True, ""


def entry_dte_ok(strategy: str, dte: Optional[int]) -> Tuple[bool, str]:
    if not dte:
        return True, ""
    if is_premium_selling(strategy):
        if dte < MIN_SELL_DTE:
            return False, f"entry DTE {dte} < {MIN_SELL_DTE} (gamma acceleration zone)"
        if dte > MAX_ENTRY_DTE:
            return False, f"entry DTE {dte} > {MAX_ENTRY_DTE} (capital drag, flat decay)"
    elif dte < MIN_BUY_DTE:
        return False, f"entry DTE {dte} < {MIN_BUY_DTE} (too little time value left)"
    return True, ""


def earnings_window_ok(
    strategy: str,
    days_to_earnings: Optional[int],
    dte: Optional[int] = None,
) -> Tuple[bool, str]:
    """Refuse new short-premium entries whose life spans an earnings print."""
    if not is_premium_selling(strategy) or days_to_earnings is None:
        return True, ""
    if 0 < days_to_earnings <= EARNINGS_BLACKOUT_DAYS:
        return (
            False,
            f"earnings in {days_to_earnings} days → no new short premium into the event",
        )
    return True, ""


def relative_strength_ok(
    strategy: str,
    rs_126: Optional[float],
) -> Tuple[bool, str]:
    """IBD 'L': directional short premium only on names leading the market.

    ``rs_126`` is the symbol's 6-month return minus SPY's (fractions, e.g.
    0.05 = 5pp of outperformance). Laggards below the floor are refused for
    directional bull/bear credit spreads; neutral (iron condor) and debit
    structures and index ETFs are unaffected. Missing RS data is not a veto —
    fail-open only on a *soft* enrichment, never on a hard safety input.
    """
    if not is_premium_selling(strategy) or rs_126 is None:
        return True, ""
    if strategy_bias(strategy) == "range":
        return True, ""
    if rs_126 < RS_MIN_FOR_DIRECTIONAL_SELL:
        return False, f"relative strength {rs_126:+.0%} vs SPY — laggard, no directional premium"
    return True, ""


def evaluate_entry(
    strategy: str,
    *,
    trend: Optional[str] = None,
    short_strike: Optional[float] = None,
    spot: Optional[float] = None,
    expected_move_1sd: Optional[float] = None,
    dte: Optional[int] = None,
    days_to_earnings: Optional[int] = None,
    rs_126: Optional[float] = None,
) -> Tuple[bool, str]:
    """Run every high-win-rate gate. Returns (pass, reason).

    First failure wins so the reason is specific and actionable. No gate is
    skipped on missing data except relative-strength, which is a soft signal.
    """
    for ok, reason in (
        trend_alignment_ok(strategy, trend),
        expected_move_buffer_ok(strategy, short_strike, spot, expected_move_1sd),
        entry_dte_ok(strategy, dte),
        earnings_window_ok(strategy, days_to_earnings, dte),
        relative_strength_ok(strategy, rs_126),
    ):
        if not ok:
            return False, reason
    return True, ""
