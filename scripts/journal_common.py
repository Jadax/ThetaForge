"""Shared helpers for translating TWS paper-order ledger records into journal
trade entries. Used by both `add_trade.py` and `sync_journal.py` so the leg
mapping and capital-at-risk rules live in exactly one place.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

RIGHT_TO_TYPE = {"C": "CALL", "P": "PUT"}


def ledger_capital_at_risk(record: Dict[str, Any]) -> Optional[float]:
    """Maximum capital at risk carried on a ledger order."""
    capital = record.get("max_loss_total") or record.get("max_loss_per_combo")
    return float(capital) if capital else None


def journal_legs(
    record_legs: List[Dict[str, Any]],
    *,
    as_of: date,
) -> List[Dict[str, Any]]:
    """Map raw ledger legs to the journal leg schema, DTE relative to *as_of*."""
    out: List[Dict[str, Any]] = []
    for leg in record_legs:
        expiry = leg.get("expiry") or None
        dte = None
        if expiry:
            try:
                dte = max((date.fromisoformat(expiry) - as_of).days, 0)
            except ValueError:
                dte = None
        out.append({
            "action": str(leg.get("action", "SELL")).upper(),
            "type": RIGHT_TO_TYPE.get(str(leg.get("right")), "CALL"),
            "strike": float(leg["strike"]) if leg.get("strike") else None,
            "expiry": expiry,
            "dte": dte,
        })
    return out
