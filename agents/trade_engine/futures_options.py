"""CME micro futures options (/MES, /MNQ) on the standard recommendation path.

Futures options reuse the Brain's read and the Recommender's scoring and
gates rather than a second scoring path:

- Direction/regime: the options scanner's own analysis of the futures'
  ETF proxy (SPY for /MES, QQQ for /MNQ) -- the same index exposure.
- Implied vol: the chain's own ATM IV; IV rank from the matching CBOE vol
  index (VIX for the S&P 500, VXN for the Nasdaq-100), which IS the index's
  30-day implied vol; realized vol from the proxy ETF.
- Structures: defined-risk spreads and condors only. The Bridge rejects any
  naked/single-leg futures option (a short futures put is margin, not cash
  secured), and so does this filter before anything is recommended.

The Recommender's dollar math assumes the equity multiplier (x100). Futures
option rows are therefore expressed in x100-equivalent units -- every price
and strike scaled by multiplier/100 -- so max loss, credit and capital come
out in exact dollars; recommendation legs are scaled back to real strikes and
carry the contract identity (FOP, trading class, multiplier) to the Bridge.
"""
from typing import Any, Dict, List, Optional, Tuple

FUTURES_OPTION_ROOTS: Dict[str, Dict[str, str]] = {
    "MES": {"proxy_etf": "SPY", "vol_index": "^VIX"},
    "MNQ": {"proxy_etf": "QQQ", "vol_index": "^VXN"},
}
FUTURES_ALLOWED_TYPES = {"bull_put", "bear_call", "iron_condor"}
# Brain strategy on the proxy ETF -> the defined-risk futures equivalent.
# A CSP read becomes a put credit spread (naked futures puts are refused).
_PROXY_TO_FUTURES_STRATEGY = {
    "cash_secured_put": "bull_put_credit",
    "bull_put_credit": "bull_put_credit",
    "bear_call_credit": "bear_call_credit",
    "iron_condor": "iron_condor",
}
_SCALED_FIELDS = ("strike", "bid", "ask", "last", "vega", "theta", "gamma")


def is_futures_root(symbol: str) -> bool:
    return str(symbol or "").upper() in FUTURES_OPTION_ROOTS


def futures_strategy_for(proxy_strategy: Optional[str]) -> Optional[str]:
    return _PROXY_TO_FUTURES_STRATEGY.get(str(proxy_strategy or ""))


def scale_chain(chain: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float, float]:
    """(scaled rows, factor, underlying price in scaled units)."""
    if not chain:
        return [], 1.0, 0.0
    multiplier = float(chain[0].get("multiplier") or 0)
    spot = float(chain[0].get("underlying_price") or 0)
    if multiplier <= 0 or spot <= 0:
        return [], 1.0, 0.0
    factor = multiplier / 100.0
    scaled = []
    for row in chain:
        copy = dict(row)
        for field in _SCALED_FIELDS:
            if isinstance(copy.get(field), (int, float)):
                copy[field] = copy[field] * factor
        copy["_real_strike"] = row.get("strike")
        scaled.append(copy)
    return scaled, factor, spot * factor


def restore_legs(legs: List[Dict[str, Any]], chain: List[Dict[str, Any]], factor: float) -> List[Dict[str, Any]]:
    """Map serialized recommendation legs (scaled strikes) back to the real
    contracts, attaching FOP identity. Raises if any leg can't be matched --
    a futures order must never go out against a guessed contract."""
    restored = []
    for leg in legs:
        real_strike = float(leg["strike"]) / factor
        match = next(
            (row for row in chain
             if str(row.get("expiry")) == str(leg.get("expiry"))
             and str(row.get("option_type", "")).upper() == str(leg.get("type", "")).upper()
             and abs(float(row.get("strike") or 0) - real_strike) < 1e-6),
            None,
        )
        if match is None:
            raise ValueError(f"futures leg {leg} has no matching contract")
        restored.append({
            **leg,
            "strike": match["strike"],
            "bid": match.get("bid"),
            "ask": match.get("ask"),
            "sec_type": "FOP",
            "trading_class": match.get("trading_class"),
            "multiplier": int(match.get("multiplier") or 0),
        })
    return restored


def atm_implied_vol(chain: List[Dict[str, Any]], spot: float) -> Optional[float]:
    """ATM IV of the standard (>= 21 DTE) expiry, averaged over put and call."""
    standard = [r for r in chain if int(r.get("dte") or 0) >= 21 and float(r.get("implied_volatility") or 0) > 0]
    if not standard or spot <= 0:
        return None
    nearest = min(abs(float(r.get("strike") or 0) - spot) for r in standard)
    ivs = [float(r["implied_volatility"]) for r in standard
           if abs(float(r.get("strike") or 0) - spot) == nearest]
    return sum(ivs) / len(ivs) if ivs else None


def index_iv_rank(history_closes: List[float]) -> Optional[float]:
    """52-week IV rank of a CBOE vol index from its daily closes."""
    closes = [float(c) for c in history_closes if c and c > 0]
    if len(closes) < 60:
        return None
    current, low, high = closes[-1], min(closes), max(closes)
    if high <= low:
        return None
    return round((current - low) / (high - low) * 100, 1)
