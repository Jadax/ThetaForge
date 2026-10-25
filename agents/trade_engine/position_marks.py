"""Per-share mark of an open option position from a fresh chain.

One implementation shared by the exit-management endpoint (orchestrator) and
the journal's live-mark refresh (scripts/sync_journal.py): sold legs' mids
minus bought legs' mids — for a credit structure, its current cost to close.

Legs may come from the ledger (``right``: C/P) or the journal (``type``:
CALL/PUT). Any leg that can't be quoted yields None: a partial mark would
silently misstate the position, so callers keep their previous value instead.
"""
from typing import Any, Dict, List, Optional


def _leg_option_type(leg: Dict[str, Any]) -> str:
    raw = str(leg.get("type") or leg.get("option_type") or leg.get("right") or "").upper()
    return {"C": "CALL", "P": "PUT"}.get(raw, raw)


def leg_mid(chain: List[Dict[str, Any]], leg: Dict[str, Any]) -> Optional[float]:
    expiry = str(leg.get("expiry") or "")
    try:
        strike = float(leg.get("strike") or 0)
    except (TypeError, ValueError):
        return None
    option_type = _leg_option_type(leg)
    for row in chain:
        if (str(row.get("expiry") or "") == expiry
                and abs(float(row.get("strike") or 0) - strike) < 1e-6
                and str(row.get("option_type") or "").upper() == option_type):
            try:
                bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
                mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else float(row.get("last") or 0)
            except (TypeError, ValueError):
                return None
            return mid if mid > 0 else None
    return None


def net_mark(chain: List[Dict[str, Any]], legs: List[Dict[str, Any]]) -> Optional[float]:
    """SELL-leg mids minus BUY-leg mids, or None if any leg is unquotable."""
    if not chain or not legs:
        return None
    mark = 0.0
    for leg in legs:
        mid = leg_mid(chain, leg)
        if mid is None:
            return None
        mark += mid if str(leg.get("action", "SELL")).upper() == "SELL" else -mid
    return mark
