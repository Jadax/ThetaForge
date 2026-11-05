"""Post-trade review and learned setup adjustments -- the self-improving loop.

Every closed trade in the public journal (the only record of what was really
placed) is reviewed: why it won or lost, in terms of its exit and the entry
context that was captured when it was placed. Outcomes are then grouped by
*setup* -- strategy crossed with one entry feature at a time (trend, IV-rank
band, RSI band, market risk tilt, DTE band) -- and a setup that is reliably
losing is vetoed, one that is negative is penalized, and one that is reliably
winning gets a small boost. The Recommenders apply these before ranking, so the
engine stops repeating the setups that lose and leans into the ones that work.

Guard rails, because a few dozen trades are a small sample and a learning loop
that chases noise is worse than none:

* ``MIN_SETUP_SAMPLES`` closed trades before a setup can be adjusted at all,
  and the veto needs the loss to be clear of one standard error.
* Scores move by at most a few points; only a statistically clear loser is
  vetoed. The research-based gates (IV rank floor, expected-move buffer, DTE
  band, earnings/macro vetoes, Bridge risk checks) are never loosened here.
* Trades closed by a rule that no longer exists (the pre-FOMC flatten, removed
  in v1.17.29) or carried over from an unmanaged ledger are excluded: they say
  nothing about the setup.
* Missing or unreachable data fails open: no adjustments, never a fabricated
  veto.

A 100% win rate is not a reachable target for any real strategy -- every
setup has losers, and pushing win rate up by taking ever-smaller credits or
cutting winners early lowers expectancy. The loop optimizes expectancy (average
outcome per unit of risk), which is what compounds an account.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

JOURNAL_URL = "https://journal.astraiva.app/trades.json"
CACHE_TTL_SECONDS = 1800

# Only trades closed in the last LEARNING_WINDOW_DAYS count. A veto stops a
# setup from trading, so without a window it could never earn its way back;
# as its losses age out the setup becomes eligible again and is re-tested.
LEARNING_WINDOW_DAYS = 120
MIN_SETUP_SAMPLES = 6          # closed trades before a setup is adjusted
MIN_BOOST_SAMPLES = 8          # more evidence needed to lean in than to back off
PENALTY_POINTS = 5.0           # composite-score points
BOOST_POINTS = 3.0
SCRATCH_FRACTION = 0.05        # |outcome| under 5% of the unit = scratch

# Exits that carry no information about the entry setup.
EXCLUDED_EXITS = {"close_pre_macro"}

# Recommender candidate types -> the strategy names the journal records.
_STRATEGY_ALIASES = {
    "csp": "cash_secured_put", "wheel": "cash_secured_put", "wheel_csp": "cash_secured_put",
    "cc": "covered_call", "wheel_cc": "covered_call",
    "bull_put": "bull_put_credit", "bear_call": "bear_call_credit",
    "call_debit": "call_debit_spread", "put_debit": "put_debit_spread",
}

_EXIT_FROM_NOTE = (
    ("hard stop", "close_stop"), ("trailing", "close_trail"), ("2x", "close_loss"),
    ("loss", "close_loss"), ("take-profit", "close_profit"), ("target", "close_profit"),
    ("profit", "close_profit"), ("dte", "close_time"), ("expiry week", "close_time"),
    ("earnings", "close_pre_earnings"), ("macro", "close_pre_macro"), ("time exit", "close_time"),
)

_LESSONS = {
    "close_profit": "Thesis played out to its target -- repeat this setup.",
    "close_trail": "Trend carried the trade; the trailing stop banked it.",
    "close_stop": "Price went straight to the stop -- the entry was early or the tape was against it.",
    "close_loss": "The underlying moved through the short strike; premium did not cover the move.",
    "close_time": "Time ran out before the trade worked -- enter with more DTE or a stronger edge.",
    "close_pre_earnings": "Earnings landed inside the holding window and forced an early exit -- check the calendar before entering.",
    "close_post_earnings": "Event trade closed after the print as designed.",
}


def canonical_strategy(strategy: Any) -> str:
    key = str(strategy or "").lower()
    return _STRATEGY_ALIASES.get(key, key)


def _band(value: Any, edges: Tuple[float, ...], labels: Tuple[str, ...]) -> Optional[str]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    for edge, label in zip(edges, labels):
        if number < edge:
            return label
    return labels[-1]


def _trend(value: Any) -> Optional[str]:
    text = str(value or "").lower()
    if not text or text == "none":
        return None
    if "bear" in text or "below" in text or "down" in text:
        return "bearish"
    if "bull" in text or "above" in text or "up" in text:
        return "bullish"
    if "neutral" in text or "range" in text or "side" in text:
        return "neutral"
    return None


def trade_features(trade: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Entry-context features of a journal trade (or a candidate shaped alike)."""
    conditions = trade.get("conditions") or {}
    asset = "equity" if trade.get("asset_class") == "equity" else "options"
    features: Dict[str, Optional[str]] = {
        "asset": asset,
        "strategy": canonical_strategy(trade.get("strategy")),
        "trend": _trend(conditions.get("trend") or conditions.get("regime")),
    }
    if asset == "options":
        ivr = conditions.get("eff_iv_rank", conditions.get("iv_rank"))
        features["ivr_band"] = _band(ivr, (30, 50, 70), ("ivr<30", "ivr30-50", "ivr50-70", "ivr70+"))
        features["dte_band"] = _band(trade.get("dte_at_entry"), (30, 46, 61), ("dte<30", "dte30-45", "dte46-60", "dte60+"))
    else:
        features["rsi_band"] = _band(conditions.get("rsi_14"), (50, 60, 70), ("rsi<50", "rsi50-60", "rsi60-70", "rsi70+"))
        features["risk_tilt"] = (str(conditions.get("risk_tilt")).lower() or None) if conditions.get("risk_tilt") else None
    return features


def exit_reason(trade: Dict[str, Any]) -> Optional[str]:
    close = trade.get("close_order") or {}
    reason = close.get("reason") or trade.get("exit_reason")
    if reason:
        return str(reason)
    note = str(trade.get("exit_note") or "").lower()
    for needle, mapped in _EXIT_FROM_NOTE:
        if needle in note:
            return mapped
    return None


def outcome_units(trade: Dict[str, Any]) -> Optional[float]:
    """Outcome normalized to the trade's own risk unit.

    Equity: R-multiple (P&L / dollars risked to the stop). Credit option
    structures: fraction of the credit kept (+0.5 = took half the credit,
    -1.0 = lost the credit's worth). Debit structures: P&L / debit paid.
    """
    try:
        pnl = float(trade.get("net_pnl"))
    except (TypeError, ValueError):
        return None
    unit = None
    if trade.get("asset_class") != "equity":
        try:
            unit = float(trade.get("max_profit") or 0)
        except (TypeError, ValueError):
            unit = None
    if not unit:
        try:
            unit = float(trade.get("capital_at_risk") or 0)
        except (TypeError, ValueError):
            unit = None
    if not unit or unit <= 0:
        return None
    return pnl / unit


def review_trade(trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Verdict, drivers and a one-line lesson for a closed trade."""
    if trade.get("status") != "closed":
        return None
    units = outcome_units(trade)
    try:
        pnl = float(trade.get("net_pnl") or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    if units is not None and abs(units) < SCRATCH_FRACTION:
        verdict = "scratch"
    else:
        verdict = "win" if pnl > 0 else "loss"
    exit_kind = exit_reason(trade)
    features = trade_features(trade)
    conditions = trade.get("conditions") or {}
    drivers: List[str] = []
    if exit_kind:
        drivers.append(f"exit: {exit_kind.replace('_', ' ')}")
    if units is not None:
        unit_name = "R" if features["asset"] == "equity" else "x credit"
        drivers.append(f"outcome {units:+.2f} {unit_name}")
    if features["asset"] == "equity":
        rsi = conditions.get("rsi_14")
        if verdict == "loss" and isinstance(rsi, (int, float)) and rsi >= 70:
            drivers.append(f"entered overbought (RSI {rsi:.0f})")
        tilt = str(conditions.get("risk_tilt") or "").lower()
        if verdict == "loss" and tilt in ("mixed", "risk_off"):
            drivers.append(f"entered into a {tilt} tape")
    else:
        ivr = conditions.get("eff_iv_rank", conditions.get("iv_rank"))
        if isinstance(ivr, (int, float)) and verdict == "loss" and ivr < 40:
            drivers.append(f"premium was thin (IV rank {ivr:.0f})")
        dte = trade.get("dte_at_entry")
        if isinstance(dte, (int, float)) and exit_kind == "close_time" and dte < 30:
            drivers.append(f"entered at {int(dte)} DTE -- little time before the 21-DTE exit")
        if features["trend"] == "bearish" and canonical_strategy(trade.get("strategy")) in (
                "cash_secured_put", "bull_put_credit") and verdict == "loss":
            drivers.append("sold puts into a downtrend")
    if exit_kind in EXCLUDED_EXITS:
        lesson = "Closed by the pre-FOMC flatten rule (since removed) -- not a verdict on the setup."
    else:
        lesson = _LESSONS.get(exit_kind or "", "Outcome recorded; not enough context to attribute it.")
        if verdict == "scratch":
            lesson = "Scratch -- the trade neither worked nor failed; " + lesson[0].lower() + lesson[1:]
    return {"verdict": verdict, "drivers": drivers, "lesson": lesson,
            "units": round(units, 3) if units is not None else None}


def _learnable(trade: Dict[str, Any], since: Optional[str] = None) -> bool:
    if since and str(trade.get("closed") or "")[:10] < since:
        return False
    return (trade.get("status") == "closed"
            and exit_reason(trade) not in EXCLUDED_EXITS and outcome_units(trade) is not None)


def _stats(values: List[float]) -> Dict[str, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    wins = sum(v > 0 for v in values)
    return {
        "n": n,
        "mean_units": round(mean, 3),
        "se": round(math.sqrt(var / n), 3) if n > 1 else 0.0,
        "win_rate": round(wins / n * 100, 1),
        # Beta(2,2)-shrunk win rate: 3 wins of 3 reads ~71%, not 100%.
        "win_rate_shrunk": round((wins + 2) / (n + 4) * 100, 1),
    }


_SETUP_DIMENSIONS = {
    "options": ("trend", "ivr_band", "dte_band"),
    "equity": ("trend", "rsi_band", "risk_tilt"),
}


def learn(trades: Iterable[Dict[str, Any]], today: Optional[str] = None) -> Dict[str, Any]:
    """Setup statistics and the adjustments they justify (rolling window)."""
    from datetime import date, timedelta
    anchor = date.fromisoformat(today) if today else date.today()
    since = (anchor - timedelta(days=LEARNING_WINDOW_DAYS)).isoformat()
    closed = [t for t in trades if _learnable(t, since)]
    groups: Dict[Tuple[Tuple[str, str], ...], List[float]] = defaultdict(list)
    for trade in closed:
        features = trade_features(trade)
        units = outcome_units(trade)
        base = (("asset", features["asset"]), ("strategy", features["strategy"]))
        groups[base].append(units)
        for dim in _SETUP_DIMENSIONS[features["asset"]]:
            if features.get(dim):
                groups[base + ((dim, features[dim]),)].append(units)

    setups, adjustments = [], []
    for key, values in sorted(groups.items()):
        stats = _stats(values)
        setup = {"when": dict(key), **stats}
        setups.append(setup)
        if stats["n"] < MIN_SETUP_SAMPLES:
            continue
        mean, se = stats["mean_units"], stats["se"]
        if mean + se < 0:
            action, points = "veto", None
        elif mean < 0:
            action, points = "penalize", -PENALTY_POINTS
        elif stats["n"] >= MIN_BOOST_SAMPLES and mean - se > 0:
            action, points = "boost", BOOST_POINTS
        else:
            continue
        adjustments.append({
            "when": dict(key), "action": action, "points": points,
            "evidence": f"n={stats['n']}, mean {mean:+.2f} (+/-{se:.2f}), win {stats['win_rate']:.0f}%",
        })
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window_days": LEARNING_WINDOW_DAYS,
        "closed_learnable": len(closed),
        "setups": setups,
        "adjustments": adjustments,
    }


def setup_adjustment(adjustments: Iterable[Dict[str, Any]], features: Dict[str, Optional[str]]
                     ) -> Tuple[bool, float, List[str]]:
    """(veto, score_delta, reasons) for a candidate's features.

    A veto wins; otherwise point changes add up, capped so learning can nudge
    but never dominate the research-based score.
    """
    veto, delta, reasons = False, 0.0, []
    for adjustment in adjustments or []:
        when = adjustment.get("when") or {}
        if not when or any(features.get(k) != v for k, v in when.items()):
            continue
        reasons.append(f"learned {adjustment['action']}: "
                       + ", ".join(f"{k}={v}" for k, v in when.items() if k != "asset")
                       + f" ({adjustment.get('evidence')})")
        if adjustment["action"] == "veto":
            veto = True
        else:
            delta += float(adjustment.get("points") or 0)
    return veto, max(-2 * PENALTY_POINTS, min(2 * BOOST_POINTS, delta)), reasons


_CACHE: Dict[str, Any] = {"at": 0.0, "adjustments": []}


def load_adjustments(url: str = JOURNAL_URL) -> List[Dict[str, Any]]:
    """Adjustments learned from the published journal (TTL-cached, fail-open)."""
    if time.time() - _CACHE["at"] < CACHE_TTL_SECONDS:
        return _CACHE["adjustments"]
    adjustments: List[Dict[str, Any]] = []
    try:
        import httpx
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        journal = response.json()
        trades = journal.get("trades", []) if isinstance(journal, dict) else []
        adjustments = learn(trades)["adjustments"]
    except Exception:
        adjustments = []
    _CACHE["at"] = time.time()
    _CACHE["adjustments"] = adjustments
    return adjustments
