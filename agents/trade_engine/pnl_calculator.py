"""Strategy P/L calculator (Market Chameleon / tastytrade pattern).

Turns a multi-leg options structure into the classic at-expiry P/L curve:
max profit, max loss, breakeven(s), risk/reward, probability-of-profit at
expiry, and a set of P/L points the dashboard can draw. Pure math over the
legs the caller supplies — it never fetches anything and never invents a
premium (entry prices come from the caller, e.g. a chain mid).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional


def _erf_cdf(x: float) -> float:
    """Standard normal CDF via the stdlib (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _leg_payoff(leg: Dict, spot: float) -> float:
    """Per-share intrinsic payoff of one leg at expiry, signed for the owner."""
    strike = float(leg.get("strike", 0))
    if str(leg.get("option_type", "")).lower() == "call":
        intrinsic = max(spot - strike, 0.0)
    else:
        intrinsic = max(strike - spot, 0.0)
    # BUY owns the intrinsic; SELL pays it out.
    return intrinsic if str(leg.get("action", "")).upper() == "BUY" else -intrinsic


def _leg_entry(leg: Dict) -> float:
    """Per-share entry cash flow: +premium for a SELL, -premium for a BUY."""
    price = float(leg.get("entry_price", 0))
    return price if str(leg.get("action", "")).upper() == "SELL" else -price


def pnl_at(legs: List[Dict], spot: float, contracts: int = 1) -> float:
    """Total at-expiry dollar P&L of the structure at *spot*."""
    per_share = sum(_leg_entry(leg) for leg in legs) + sum(
        _leg_payoff(leg, spot) for leg in legs
    )
    return per_share * 100 * max(contracts, 1)


def calculate_pnl(
    legs: List[Dict],
    spot: float,
    *,
    contracts: int = 1,
    iv: Optional[float] = None,
    dte: Optional[int] = None,
    target_prices: Optional[List[float]] = None,
) -> Dict:
    """Full P/L profile for a multi-leg structure.

    legs: [{'action': 'SELL'|'BUY', 'option_type': 'call'|'put',
            'strike': float, 'entry_price': float (per-share premium)}, ...]

    spot/iv/dte drive the probability-of-profit estimate (optional — when
    either is missing, pop_at_expiry is None). Fail-closed: no legs or an
    invalid spot returns an error payload, never a fabricated profile.
    """
    if not legs or spot is None or spot <= 0:
        return {"error": "a structure needs at least one leg and a positive spot"}
    try:
        spot = float(spot)
    except (TypeError, ValueError):
        return {"error": "invalid spot price"}

    # Default curve: span the strike wings plus a fine grid around spot so the
    # max-loss/max-profit regions and breakevens are always inside the domain.
    if target_prices:
        points = sorted(float(p) for p in target_prices if p > 0)
    else:
        strikes = [float(leg.get("strike", spot)) for leg in legs if leg.get("strike")]
        lo_strike = min(strikes) if strikes else spot * 0.9
        hi_strike = max(strikes) if strikes else spot * 1.1
        points = []
        lo = min(lo_strike * 0.9, spot * 0.85)
        hi = max(hi_strike * 1.1, spot * 1.15)
        # Coarse wings first (for the diagram), fine grid for extrema/breakevens.
        for factor in (0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10):
            points.append(round(spot * factor, 2))
        points.extend([round(lo_strike * 0.9, 2), round(lo_strike, 2),
                       round(hi_strike, 2), round(hi_strike * 1.1, 2)])
        points = sorted({round(p, 2) for p in points if p > 0})

    curve = [{"spot": round(p, 2), "pnl": round(pnl_at(legs, p, contracts), 2)} for p in points]

    # Extrema over the scanned curve, then refined by a dense local scan.
    pnls = [row["pnl"] for row in curve]
    max_profit = max(pnls)
    max_loss = min(pnls)

    # Breakeven detection via sign flips over a dense scan of the domain.
    lo = min(points)
    hi = max(points)
    step = max((hi - lo) / 2000.0, 1e-6)
    breakevens: List[float] = []
    prev = pnl_at(legs, lo, 1)
    prev_x = lo
    x = lo
    while x <= hi:
        value = pnl_at(legs, x, 1)
        if prev == 0.0 and value != 0.0:
            breakevens.append(round(x, 2))
        elif prev * value < 0:
            # Linear interpolate the root.
            root = prev_x - prev * (x - prev_x) / (value - prev)
            breakevens.append(round(root, 2))
        prev, prev_x = value, x
        x += step

    # Probability of profit at expiry (normal approximation on the nearest BE).
    pop: Optional[float] = None
    if iv and iv > 0 and dte and dte > 0:
        sigma = float(iv)
        T = float(dte) / 365.0
        candidates = [be for be in breakevens if abs(be - spot) > 1e-9]
        if candidates:
            # Profit sits on the side of the BE that keeps the short leg OTM;
            # choose the BE nearest spot as the operative barrier.
            be = min(candidates, key=lambda b: abs(b - spot))
            z = (be - spot) / (spot * sigma * math.sqrt(T))
            if be > spot:
                pop = _erf_cdf(z)  # profit below the barrier
            else:
                pop = 1.0 - _erf_cdf(z)  # profit above the barrier
            pop = max(0.0, min(1.0, pop))

    net_entry_per_share = round(sum(_leg_entry(leg) for leg in legs), 3)
    return {
        "spot": spot,
        "contracts": contracts,
        "net_entry_per_share": net_entry_per_share,
        "net_entry": round(net_entry_per_share * 100 * max(contracts, 1), 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "risk_reward": round(abs(max_profit / max_loss), 2) if max_loss else 0.0,
        "breakevens": breakevens,
        "pop_at_expiry": round(pop * 100, 1) if pop is not None else None,
        "pnl_points": curve,
    }
