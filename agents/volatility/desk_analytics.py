"""
Desk Analytics — the signals institutional option desks read that a retail
chain alone doesn't show.

Steals the methodology public desks publish (Goldman Sachs derivatives
research, JPMorgan's vol notes, Susquehanna-style volatility surface reading)
and computes it from FREE option-chain data (CBOE delayed quotes carry
per-strike delta + IV, which makes surface analysis possible without a paid
feed).

What's here:
  1. IV SKEW — 25-delta risk reversal (RR25) and 25-delta butterfly (BF25),
     the two surface shapes every vol desk quotes. RR25 measures whether puts
     are rich vs calls (hedging demand / fear); BF25 measures whether wings
     are rich vs ATM (tail risk priced).
  2. EARNINGS MOVE — the desk earnings playbook: compare the option market's
     implied move (straddle) to the stock's realized history of post-earnings
     moves. Implied >> realized history means IV is rich (sell the move, IV
     crush); implied << realized means IV is cheap (buy the move).

Fail-closed: any input the formulas need that isn't available returns None —
missing delta (yfinance fallback chains have none) must not fabricate a skew.
"""
import logging
import statistics
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Skew deltas are quoted on the 25-delta convention (the strike whose delta is
# ±0.25), with 50-delta (ATM) as the reference for normalization.
TARGET_DELTA_QUARTER = 0.25
TARGET_DELTA_ATM = 0.50

# Single-name (equity) skew regime bands, expressed as skew normalized by ATM
# IV. These are desk heuristics, deliberately conservative: they describe the
# shape of the surface, they do not on their own authorize a trade.
FEAR_RR25_NORM = 0.15       # puts >15 points rich vs calls at 25Δ → heavy hedging demand
ELEVATED_RR25_NORM = 0.08   # puts 8+ points rich → watch for tail hedging
COMPLACENT_RR25_NORM = 0.02 # puts barely richer than calls → euphoria / no fear priced
COMPLACENT_BF25_NORM = 0.05 # wings flat → no tail risk premium
FEAR_BF25_NORM = 0.15       # wings very rich → fat tails priced

# Earnings window: how many historical earnings events to compare against.
EARNINGS_LOOKBACK = 8


# ── IV Skew ─────────────────────────────────────────────────────────────


def _interp_iv(rows: List[Tuple[float, float]], target_delta: float) -> Optional[float]:
    """Linear-interpolate IV at *target_delta* from sorted (delta, iv) rows."""
    if len(rows) < 2:
        return None
    for index in range(len(rows) - 1):
        low_d, low_iv = rows[index]
        high_d, high_iv = rows[index + 1]
        if low_d <= target_delta <= high_d:
            span = high_d - low_d
            if span <= 0:
                return low_iv
            return low_iv + (high_iv - low_iv) * (target_delta - low_d) / span
    return None


def _expiry_quality(option: Dict[str, Any]) -> Tuple[int, float]:
    """Sort key for picking the expiry with the most traded surface."""
    volume = int(option.get("volume") or 0)
    oi = int(option.get("open_interest") or 0)
    return volume, oi


def calculate_iv_skew(option_chain: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Compute RR25 / BF25 skew from a chain with per-strike delta + IV.

    Returns None (fail-closed) when the chain lacks the delta/IV surface a
    real skew needs — e.g. a yfinance fallback chain. Never a placeholder.
    """
    if not option_chain:
        return None

    # Group by expiry, keeping only expiries that bracket the 25-delta strikes
    # on both sides AND have ATM coverage.
    by_expiry: Dict[str, List[Dict[str, Any]]] = {}
    for opt in option_chain:
        expiry = opt.get("expiry") or ""
        delta = opt.get("delta")
        iv = opt.get("implied_volatility")
        if not expiry or delta is None or not iv or float(iv) <= 0:
            continue
        by_expiry.setdefault(expiry, []).append(opt)

    best: Optional[Tuple[Dict[str, Any], str]] = None
    for expiry, options in by_expiry.items():
        calls = [(float(o["delta"]), float(o["implied_volatility"])) for o in options
                 if str(o.get("option_type", "")).upper() == "CALL" and float(o["delta"]) > 0]
        puts = [(float(o["delta"]), float(o["implied_volatility"])) for o in options
                if str(o.get("option_type", "")).upper() == "PUT" and float(o["delta"]) < 0]
        calls.sort(key=lambda pair: pair[0])
        puts.sort(key=lambda pair: pair[0])
        if not calls or not puts:
            continue
        if calls[-1][0] < TARGET_DELTA_QUARTER or puts[0][0] > -TARGET_DELTA_QUARTER:
            continue
        if _interp_iv(calls, TARGET_DELTA_ATM) is None or _interp_iv(puts, -TARGET_DELTA_ATM) is None:
            continue
        score = _expiry_quality(max(options, key=_expiry_quality))
        if best is None or score > best[0]:
            best = (score, expiry)

    if best is None:
        return None
    expiry = best[1]
    calls = sorted([(float(o["delta"]), float(o["implied_volatility"])) for o in by_expiry[expiry]
                    if str(o.get("option_type", "")).upper() == "CALL" and float(o["delta"]) > 0])
    puts = sorted([(float(o["delta"]), float(o["implied_volatility"])) for o in by_expiry[expiry]
                   if str(o.get("option_type", "")).upper() == "PUT" and float(o["delta"]) < 0])

    iv_call_25 = _interp_iv(calls, TARGET_DELTA_QUARTER)
    iv_put_25 = _interp_iv(puts, -TARGET_DELTA_QUARTER)
    iv_call_50 = _interp_iv(calls, TARGET_DELTA_ATM)
    iv_put_50 = _interp_iv(puts, -TARGET_DELTA_ATM)
    if None in (iv_call_25, iv_put_25, iv_call_50, iv_put_50):
        return None

    atm_iv = (iv_call_50 + iv_put_50) / 2
    if atm_iv <= 0:
        return None

    rr25 = iv_put_25 - iv_call_25     # >0 → puts rich vs calls (fear)
    bf25 = (iv_put_25 + iv_call_25) / 2 - atm_iv  # >0 → wings rich vs ATM (tail risk)
    rr25_norm = rr25 / atm_iv
    bf25_norm = bf25 / atm_iv

    regime, reasoning = _interpret_skew(rr25_norm, bf25_norm)
    return {
        "expiry": expiry,
        "atm_iv": round(atm_iv, 4),
        "iv_call_25": round(iv_call_25, 4),
        "iv_put_25": round(iv_put_25, 4),
        "rr25": round(rr25, 4),
        "bf25": round(bf25, 4),
        "rr25_norm": round(rr25_norm, 4),
        "bf25_norm": round(bf25_norm, 4),
        "regime": regime,
        "reasoning": reasoning,
    }


def _interpret_skew(rr25_norm: float, bf25_norm: float) -> Tuple[str, str]:
    """Classify the surface shape into a readable desk regime."""
    if rr25_norm >= FEAR_RR25_NORM or bf25_norm >= FEAR_BF25_NORM:
        return ("fear", f"Put skew extreme (RR25 {rr25_norm:.2f}, BF25 {bf25_norm:.2f}) — heavy hedging demand, tail risk priced")
    if rr25_norm >= ELEVATED_RR25_NORM:
        return ("elevated_fear", f"Put skew elevated (RR25 {rr25_norm:.2f}) — hedging demand building")
    if rr25_norm <= COMPLACENT_RR25_NORM and bf25_norm <= COMPLACENT_BF25_NORM:
        return ("complacent", f"Flat surface (RR25 {rr25_norm:.2f}, BF25 {bf25_norm:.2f}) — no fear priced")
    return ("neutral", f"Balanced skew (RR25 {rr25_norm:.2f}, BF25 {bf25_norm:.2f})")


# ── Earnings Move ───────────────────────────────────────────────────────


def implied_earnings_move(
    option_chain: List[Dict[str, Any]], stock_price: float
) -> Optional[float]:
    """The front-month ATM straddle's implied move, as a percent of spot.

    Desks read the straddle price over the stock price as 'what the market
    thinks the stock will do'. This uses the NEAREST expiry that has a live
    call AND put (mid prices), so the comparison stays on the earnings-event
    timeframe instead of a generic 30-day IV.
    """
    if not option_chain or not stock_price or stock_price <= 0:
        return None

    by_expiry: Dict[str, List[Dict[str, Any]]] = {}
    for opt in option_chain:
        expiry = opt.get("expiry") or ""
        if not expiry:
            continue
        bid = float(opt.get("bid") or 0)
        ask = float(opt.get("ask") or 0)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif bid > 0:
            mid = bid
        elif ask > 0:
            mid = ask
        else:
            mid = float(opt.get("last") or 0)
        if mid <= 0:
            continue
        by_expiry.setdefault(expiry, []).append((opt, mid))

    dte_by_expiry = {}
    for expiry in by_expiry:
        try:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 0)
        except (TypeError, ValueError):
            dte = 999
        dte_by_expiry[expiry] = dte

    # Nearest expiry with both sides trading; 0DTE excluded (gamma whipsaw).
    eligible = [
        (dte_by_expiry[expiry], expiry)
        for expiry in by_expiry
        if dte_by_expiry[expiry] > 0
        and any(str(o.get("option_type", "")).upper() == "CALL" for o, _ in by_expiry[expiry])
        and any(str(o.get("option_type", "")).upper() == "PUT" for o, _ in by_expiry[expiry])
    ]
    if not eligible:
        return None
    _, chosen = min(eligible, key=lambda pair: pair[0])

    call_mid = put_mid = None
    for opt, mid in by_expiry[chosen]:
        if str(opt.get("option_type", "")).upper() == "CALL":
            distance = abs(float(opt.get("strike") or 0) - stock_price)
            if call_mid is None or distance < call_mid[1]:
                call_mid = (mid, distance)
        else:
            distance = abs(float(opt.get("strike") or 0) - stock_price)
            if put_mid is None or distance < put_mid[1]:
                put_mid = (mid, distance)
    if call_mid is None or put_mid is None:
        return None
    straddle = call_mid[0] + put_mid[0]
    if straddle <= 0:
        return None
    return round(straddle / stock_price * 100, 2)


def historical_earnings_moves(
    closes_df: Any,
    earnings_dates: List[date],
    lookback: int = EARNINGS_LOOKBACK,
) -> List[float]:
    """Realized post-earnings one-day moves from a daily close DataFrame.

    *closes_df* is a pandas DataFrame with a datetime index and a Close column
    (the same shape get_historical_prices returns). Each move is the absolute
    % change from the close before the announcement to the close after.
    """
    moves: List[float] = []
    try:
        index = list(closes_df.index)
        closes = list(closes_df["Close"])
    except Exception:
        return moves
    for event in sorted(earnings_dates)[-lookback:]:
        try:
            target = pd_timestamp(event)
            prev_index = next(i for i, stamp in enumerate(index) if pd_timestamp(stamp) >= target)
            next_index = prev_index + 1
            if next_index >= len(closes) or closes[prev_index] <= 0:
                continue
            move = abs(closes[next_index] / closes[prev_index] - 1) * 100
            if move > 0.05:  # skip data glitches / flat events
                moves.append(round(move, 2))
        except (StopIteration, IndexError, TypeError, ValueError):
            continue
    return moves


def pd_timestamp(value: Any) -> Any:
    """Normalize a date/datetime/Timestamp to a comparable date."""
    try:
        import pandas as pd
        stamp = pd.Timestamp(value)
        if getattr(stamp, "tzinfo", None) is not None:
            stamp = stamp.tz_convert(None)
        return stamp.normalize()
    except Exception:
        return value


def earnings_move_edge(
    implied_move_pct: float,
    historical_moves: List[float],
) -> Optional[Dict[str, Any]]:
    """Compare the market's implied earnings move to the stock's realized
    history — the desk 'sell rich IV into earnings / buy cheap IV' read.

    Returns None when there aren't enough historical events to judge.
    """
    if not historical_moves or len(historical_moves) < 4:
        return None
    median_hist = statistics.median(historical_moves)
    edge = implied_move_pct - median_hist
    if edge > 0:
        read = "sell_iv"  # IV rich vs history → sell the move, collect IV crush
        signal = "premium_sell_edge"
    elif edge < -1.0:
        read = "buy_iv"   # IV cheap vs history → buy the move
        signal = "premium_buy_edge"
    else:
        read = "fair"
        signal = "neutral"
    return {
        "implied_move_pct": round(implied_move_pct, 2),
        "median_historical_move_pct": round(median_hist, 2),
        "edge_pct": round(edge, 2),
        "events_used": len(historical_moves),
        "read": read,
        "signal": signal,
    }
