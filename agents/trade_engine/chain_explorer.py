"""Options chain explorer (Market Chameleon / thinkorswim pattern).

Turns the free option chain into a desk-style chain table: one row per strike
with the call and put sides side-by-side (bid/ask/mid, IV, open interest,
volume, the free feed's own greeks), plus a per-expiry summary carrying the
readings a trader actually checks — ATM IV, the ATM straddle's expected move,
max pain, put/call OI and volume ratios, and IV skew when the chain's
delta/IV surface allows it. Pure reshaping of caller-supplied data; nothing is
fetched and nothing is fabricated (missing reads stay null/0, never placeholders).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.trade_engine.analytics import OptionsAnalytics
from agents.volatility.desk_analytics import calculate_iv_skew


def _mid(opt: Dict[str, Any]) -> float:
    bid = float(opt.get("bid") or 0)
    ask = float(opt.get("ask") or 0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    return round(float(opt.get("last") or 0), 2)


def _side(opt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One side of a strike row (call or put) from a normalized chain row."""
    if not opt:
        return None
    return {
        "bid": round(float(opt.get("bid") or 0), 2),
        "ask": round(float(opt.get("ask") or 0), 2),
        "mid": _mid(opt),
        "last": round(float(opt.get("last") or 0), 2),
        "iv": round(float(opt.get("implied_volatility") or 0), 4),
        "open_interest": int(opt.get("open_interest") or 0),
        "volume": int(opt.get("volume") or 0),
        "delta": round(float(opt.get("delta") or 0), 3),
        "gamma": round(float(opt.get("gamma") or 0), 5),
        "theta": round(float(opt.get("theta") or 0), 4),
        "vega": round(float(opt.get("vega") or 0), 4),
    }


def _expiry_dte(rows: List[Dict[str, Any]], expiry: str) -> int:
    return min(int(row.get("dte") or 0) for row in rows if row.get("expiry") == expiry)


def build_chain_explorer(
    chain: List[Dict[str, Any]],
    spot: float,
    *,
    expiry: Optional[str] = None,
    target_dte: int = 30,
) -> Dict[str, Any]:
    """Build the chain table + desk summary for the requested expiry.

    expiry: explicit CBOE-style "YYYY-MM-DD" to inspect, or None to auto-pick
    the expiry nearest ``target_dte``. Fail-closed: an empty chain, bad spot,
    or requested-but-absent expiry returns an error payload, never an empty
    table pretending to be a chain.
    """
    if not chain or not spot or float(spot) <= 0:
        return {"error": "an option chain and a positive spot are required"}
    spot = float(spot)

    rows: List[Dict[str, Any]] = []
    for opt in chain:
        strike = float(opt.get("strike") or 0)
        exp = str(opt.get("expiry") or "")
        opt_type = str(opt.get("option_type") or "").upper()
        if strike <= 0 or not exp or opt_type not in ("CALL", "PUT"):
            continue
        rows.append(
            {
                "strike": strike,
                "expiry": exp,
                "dte": int(opt.get("dte") or 0),
                "option_type": opt_type,
                "bid": float(opt.get("bid") or 0),
                "ask": float(opt.get("ask") or 0),
                "last": float(opt.get("last") or 0),
                "volume": int(opt.get("volume") or 0),
                "open_interest": int(opt.get("open_interest") or 0),
                "implied_volatility": float(opt.get("implied_volatility") or 0),
                "delta": float(opt.get("delta") or 0),
                "gamma": float(opt.get("gamma") or 0),
                "theta": float(opt.get("theta") or 0),
                "vega": float(opt.get("vega") or 0),
            }
        )
    if not rows:
        return {"error": "chain has no usable option rows"}

    expiries = sorted({row["expiry"] for row in rows}, key=lambda e: _expiry_dte(rows, e))
    dte_by_expiry = {e: _expiry_dte(rows, e) for e in expiries}

    if expiry:
        if expiry not in expiries:
            return {"error": f"expiry {expiry} not present in the chain"}
        chosen = expiry
    else:
        chosen = min(expiries, key=lambda e: abs(dte_by_expiry[e] - target_dte))
    chosen_dte = dte_by_expiry[chosen]

    exp_rows = [row for row in rows if row["expiry"] == chosen]
    strikes = sorted({row["strike"] for row in exp_rows})

    table: List[Dict[str, Any]] = []
    for strike in strikes:
        calls = [row for row in exp_rows if row["strike"] == strike and row["option_type"] == "CALL"]
        puts = [row for row in exp_rows if row["strike"] == strike and row["option_type"] == "PUT"]
        call = calls[0] if calls else None
        put = puts[0] if puts else None
        row: Dict[str, Any] = {"strike": strike}
        call_side = _side(call)
        put_side = _side(put)
        if call_side:
            row["call"] = call_side
        if put_side:
            row["put"] = put_side
        if call_side and put_side and call_side["open_interest"] > 0:
            row["put_call_oi_ratio"] = round(put_side["open_interest"] / call_side["open_interest"], 3)
        table.append(row)

    summary = _desk_summary(table, exp_rows, spot, chosen, chosen_dte)
    return {
        "underlying": round(spot, 2),
        "expiry": chosen,
        "dte": chosen_dte,
        "expiries": [{"expiry": e, "dte": dte_by_expiry[e]} for e in expiries],
        "table": table,
        "summary": summary,
    }


def _desk_summary(
    table: List[Dict[str, Any]],
    exp_rows: List[Dict[str, Any]],
    spot: float,
    expiry: str,
    dte: int,
) -> Dict[str, Any]:
    """Per-expiry readings a trader checks before selling or buying premium."""
    analysis = OptionsAnalytics()
    max_pain = analysis.max_pain(exp_rows)

    # ATM straddle mid for the expected-move read.
    atm_row = min(table, key=lambda row: abs(row["strike"] - spot))
    atm_call_mid = (atm_row.get("call") or {}).get("mid", 0)
    atm_put_mid = (atm_row.get("put") or {}).get("mid", 0)
    straddle = round(atm_call_mid + atm_put_mid, 2)

    atm_call_iv = (atm_row.get("call") or {}).get("iv", 0)
    atm_put_iv = (atm_row.get("put") or {}).get("iv", 0)
    iv_sources = [value for value in (atm_call_iv, atm_put_iv) if value and value > 0]
    atm_iv = round(sum(iv_sources) / len(iv_sources), 4) if iv_sources else 0.0

    expected_move = analysis.expected_move(spot, atm_iv or 0.25, dte, straddle if straddle > 0 else None)

    call_oi_total = sum((row.get("call") or {}).get("open_interest", 0) for row in table)
    put_oi_total = sum((row.get("put") or {}).get("open_interest", 0) for row in table)
    call_vol_total = sum((row.get("call") or {}).get("volume", 0) for row in table)
    put_vol_total = sum((row.get("put") or {}).get("volume", 0) for row in table)

    skew = None
    try:
        skew = calculate_iv_skew(exp_rows)
    except Exception:
        skew = None

    summary: Dict[str, Any] = {
        "dte": dte,
        "atm_iv": atm_iv,
        "atm_straddle_mid": straddle,
        "expected_move_pct": expected_move["expected_move_pct"],
        "expected_move_1sd": expected_move["expected_move_1sd"],
        "expected_move_low": expected_move["lower_1sd"],
        "expected_move_high": expected_move["upper_1sd"],
        "max_pain_strike": max_pain.get("max_pain_strike"),
        "call_wall": max_pain.get("call_wall"),
        "put_floor": max_pain.get("put_floor"),
        "call_oi_total": call_oi_total,
        "put_oi_total": put_oi_total,
        "put_call_oi_ratio": round(put_oi_total / call_oi_total, 3) if call_oi_total > 0 else None,
        "call_volume_total": call_vol_total,
        "put_volume_total": put_vol_total,
        "put_call_volume_ratio": round(put_vol_total / call_vol_total, 3) if call_vol_total > 0 else None,
    }
    if skew:
        summary["iv_skew"] = skew
    return summary
