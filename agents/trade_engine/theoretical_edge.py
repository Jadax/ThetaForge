"""Theoretical-value edge model (Market Chameleon / ORATS pattern).

Rank candidates by how far the CBOE mid (market) sits from our own
Black-Scholes model value of the structure. A credit spread paid MORE than its
model value is rich for the seller; a debit spread cheaper than model is rich
for the buyer. Fail-closed: if any leg can't be priced (missing IV, DTE, or
underlying), the edge is computed as None — a missing model price never
fabricates an edge.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from agents.volatility.black_scholes import BlackScholes, OptionType

RISK_FREE_RATE = 0.045


def estimate_structure_value(
    legs: List[Dict],
    stock_price: Optional[float],
    r: float = RISK_FREE_RATE,
) -> Optional[Dict]:
    """Compare the market net credit/debit to a Black-Scholes model value.

    legs: [{'action': 'SELL'|'BUY', 'option_type': 'call'|'put',
            'strike': float, 'dte': int, 'iv': float, 'mid': float}, ...]

    For a credit structure the per-share net is signed SELL=+1 / BUY=-1 so the
    result is the net credit; for a debit structure (BUY is the wider side) the
    same convention yields the net debit (positive). theoretical_edge_pct is
    the signed % of model value the market over- (or under-) pays:
      * credit spread: positive edge = market pays MORE than fair → selling edge
      * debit spread:  negative edge = market charges LESS than fair → buying edge
    """
    if not legs or stock_price is None or stock_price <= 0:
        return None
    market_net = 0.0
    model_net = 0.0
    for leg in legs:
        strike = leg.get("strike")
        dte = leg.get("dte")
        iv = leg.get("iv")
        mid = leg.get("mid")
        if strike is None or dte is None or iv is None or mid is None:
            return None
        try:
            strike = float(strike)
            dte = int(dte)
            iv = float(iv)
            mid = float(mid)
        except (TypeError, ValueError):
            return None
        if iv <= 0 or dte <= 0:
            return None
        sign = 1.0 if str(leg.get("action", "")).upper() == "SELL" else -1.0
        option_type = (
            OptionType.CALL if str(leg.get("option_type", "")).lower() == "call"
            else OptionType.PUT
        )
        result = BlackScholes.price(
            S=stock_price, K=strike, T=dte / 365.0, r=r,
            sigma=iv, option_type=option_type,
        )
        market_net += sign * mid
        model_net += sign * result.price

    if abs(model_net) < 1e-9:
        return None
    edge_pct = (market_net - model_net) / abs(model_net) * 100.0
    return {
        "market_net": round(market_net, 3),
        "model_net": round(model_net, 3),
        "theoretical_edge_pct": round(edge_pct, 2),
    }
