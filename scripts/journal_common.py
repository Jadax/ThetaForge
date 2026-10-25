"""Shared helpers for translating TWS paper-order ledger records into journal
trade entries. Used by both `add_trade.py` and `sync_journal.py` so the leg
mapping and capital-at-risk rules live in exactly one place.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

RIGHT_TO_TYPE = {"C": "CALL", "P": "PUT"}


def contract_multiplier(record: Dict[str, Any]) -> float:
    """Dollars per 1.00 of option price per contract: the qualified
    contract's multiplier the Bridge recorded (MES 5, MNQ 2), else the equity
    option standard 100 (records predating the field are all equity options)."""
    try:
        return float(record.get("multiplier") or 100)
    except (TypeError, ValueError):
        return 100.0


def ledger_contracts(record: Dict[str, Any]) -> float:
    """Contracts actually held: the filled count, else the ordered quantity."""
    try:
        return float(record.get("filled") or record.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def ledger_max_profit(record: Dict[str, Any]) -> float:
    """Max profit in dollars for an option entry. Ledger prices are per unit
    of the underlying, so dollars need multiplier x contracts -- the journal
    used to publish the raw per-unit credit (0.50) as the max profit."""
    credit = float(record.get("net_credit") or 0)
    contracts = ledger_contracts(record)
    if credit > 0:
        return round(credit * contract_multiplier(record) * contracts, 2)
    legs = record.get("legs") or []
    if credit < 0 and len(legs) == 2:   # debit vertical: width minus the debit paid
        width = abs(float(legs[0].get("strike") or 0) - float(legs[1].get("strike") or 0))
        return round(max(width + credit, 0.0) * contract_multiplier(record) * contracts, 2)
    return 0.0


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
