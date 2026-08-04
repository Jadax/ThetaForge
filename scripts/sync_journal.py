#!/usr/bin/env python3
"""Regenerate the public journal from the TWS paper-order ledger.

The public journal (journal/trades.json) only ever shows trades that were
recommended by ThetaForge and placed on TWS — i.e. records in the paper-order
ledger (data/paper_order_ledger.json). This script:

  * builds a fresh entry for every ledger record (requiring a
    recommendation_id, and excluding cancelled/never-executed orders),
  * overlays the human narrative (thesis, exit note, tags, P&L, close date)
    that was authored via `add_trade.py --from-ledger <id>` by matching
    `source_id` on the existing journal,
  * keeps user-authored entries that have no `source_id` (manual trades added
    via the CLI) untouched,
  * recomputes nothing stored (metrics are computed at render time),
  * writes journal/trades.json with `as_of` set to today.

Usage:
  python scripts/sync_journal.py [--journal PATH] [--ledger PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL = REPO_ROOT / "journal" / "trades.json"
DEFAULT_LEDGER = REPO_ROOT / "data" / "paper_order_ledger.json"

RIGHT_TO_TYPE = {"C": "CALL", "P": "PUT"}
EXCLUDED_STATUSES = {"ApiCancelled", "Cancelled", "Inactive", "PendingCancel"}

DEFAULT_TRADER = {
    "name": "Tushant Sharma",
    "handle": "@thetaforge",
    "tagline": "Defined-risk option seller. Recommendations placed, receipts always.",
}
REPO_URL = "https://github.com/Jadax/ThetaForge"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _opened_date(submitted_at: str) -> str:
    if not submitted_at:
        return date.today().isoformat()
    try:
        return datetime.fromisoformat(submitted_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return submitted_at[:10]


def _days_until(expiry: str | None, opened: str) -> int | None:
    if not expiry:
        return None
    try:
        opened_date = datetime.fromisoformat(opened).date() if "T" in opened else date.fromisoformat(opened)
        return max((date.fromisoformat(expiry) - opened_date).days, 0)
    except ValueError:
        return None


def _order_block(record: dict) -> dict:
    """Raw order facts from the ledger, surfaced as the receipt."""
    return {
        "status": record.get("status"),
        "filled": record.get("filled"),
        "remaining": record.get("remaining"),
        "average_fill_price": record.get("average_fill_price"),
        "limit_price": record.get("limit_price"),
        "net_credit": record.get("net_credit"),
        "quantity": record.get("quantity"),
        "submitted_at": record.get("submitted_at"),
        "updated_at": record.get("updated_at"),
    }


def _default_management_plan(strategy: str) -> dict:
    """TastyTrade-flavoured exit/management rules attached to every trade.

    Stored as data and rendered on the card so the audience sees a repeatable,
    rule-driven management plan rather than a whim. The short-leg strategies
    use 50% max-profit targets and 21 DTE management; debit (long) structures
    use breakeven stops and a fixed hold window.
    """
    premium_selling = strategy in {
        "iron_condor", "bull_put_credit", "bear_call_credit",
        "cash_secured_put", "covered_call", "short_strangle",
    }
    if strategy == "cash_secured_put":
        return {
            "target": "close at 50% of max credit",
            "stop": "roll or close if short strike is breached decisively",
            "time": "manage at 21 DTE",
            "event": "close before earnings",
        }
    if premium_selling:
        return {
            "target": "take profit at 50% of max credit",
            "stop": "roll the tested side at 2x the original credit",
            "time": "manage/close at 21 DTE",
            "event": "close before earnings if within 7 days",
        }
    return {
        "target": "take profit at +50-100% of debit paid",
        "stop": "exit if the spread breaks break-even",
        "time": "hold to expiry or 50% of DTE",
        "event": "exit before earnings",
    }


def build_entry(record: dict, overlay: dict) -> dict:
    ledger_id = str(record.get("id", ""))
    symbol = str(record.get("symbol", "")).upper()
    strategy = str(record.get("strategy", ""))
    submitted_at = str(record.get("submitted_at", ""))
    opened = _opened_date(submitted_at)

    legs = []
    for leg in record.get("legs", []):
        expiry = leg.get("expiry") or None
        dte = _days_until(expiry, opened)
        legs.append({
            "action": str(leg.get("action", "SELL")).upper(),
            "type": RIGHT_TO_TYPE.get(str(leg.get("right")), "CALL"),
            "strike": float(leg["strike"]) if leg.get("strike") else None,
            "expiry": expiry,
            "dte": dte,
        })

    base = {
        "id": ledger_id,
        "source_id": ledger_id,
        "source": "ledger",
        "ledger_ref": ledger_id,
        "order": _order_block(record),
        "symbol": symbol,
        "opened": opened,
        "closed": None,
        "status": "open",
        "strategy": strategy,
        "legs": legs,
        "entry_ivr": None,
        "expected_move_pct": None,
        "dte_at_entry": max((leg["dte"] for leg in legs if leg["dte"]), default=None),
        "capital_at_risk": float(record.get("max_loss_total") or record.get("max_loss_per_combo") or 0),
        "max_profit": float(record.get("net_credit") or 0),
        "net_pnl": 0.0,
        "net_pnl_pct": 0.0,
        "management_plan": _default_management_plan(strategy),
        "reason": f"{strategy.replace('_', ' ').title()} on {symbol} — "
                  f"placed on TWS from the ThetaForge recommendation.",
        "research": [],
        "tags": [],
        "exit_note": "Open — monitoring the position in the TWS terminal.",
        "timestamp": record.get("updated_at") or submitted_at,
    }
    for key in ("entry_ivr", "expected_move_pct", "dte_at_entry", "net_pnl",
                "net_pnl_pct", "capital_at_risk", "max_profit"):
        if overlay.get(key) is not None:
            base[key] = overlay[key]
    for key in ("closed", "status", "reason", "research", "tags", "exit_note",
                "timestamp", "management_plan"):
        if overlay.get(key):
            base[key] = overlay[key]
    return base


def _ledger_sha(consumed: list[dict]) -> str:
    """SHA-256 over the exact ledger records the journal was built from.

    Publishing this lets a reader recompute the journal from the same ledger
    state and detect any silent edit (FTMO/Myfxbook-style verification).
    """
    canonical = json.dumps(consumed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sync(args: argparse.Namespace) -> int:
    ledger = load_json(args.ledger, [])
    if not isinstance(ledger, list):
        raise SystemExit("error: ledger must be a JSON list")

    current = load_json(args.journal, {})
    existing = current.get("trades", []) if isinstance(current, dict) else []
    overlay_by_source = {
        str(trade.get("source_id")): trade
        for trade in existing
        if trade.get("source_id")
    }

    entries = []
    seen_sources = set()
    consumed = []
    for record in ledger:
        status = str(record.get("status", ""))
        if not record.get("recommendation_id"):
            continue
        if status in EXCLUDED_STATUSES:
            continue
        source = str(record.get("id", ""))
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)
        consumed.append(record)
        entries.append(build_entry(record, overlay_by_source.get(source, {})))

    for trade in existing:
        if not trade.get("source_id"):
            trade = dict(trade)
            trade.setdefault("source", "manual")
            entries.append(trade)

    entries.sort(key=lambda trade: (trade.get("opened", ""), trade.get("timestamp", "")))
    entries.reverse()

    journal = {
        "trader": (current.get("trader") if isinstance(current, dict)
                   else None) or DEFAULT_TRADER,
        "as_of": date.today().isoformat(),
        "account_equity": (current.get("account_equity") if isinstance(current, dict)
                           else None) or 25000,
        "verification": {
            "generator": "scripts/sync_journal.py",
            "ledger_source": str(args.ledger),
            "ledger_sha": _ledger_sha(consumed),
            "entries_from_ledger": len(consumed),
            "entries_manual": len([e for e in entries if not e.get("source_id")]),
        },
        "trades": entries,
    }
    args.journal.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")

    print(f"Journal rebuilt at {args.journal}")
    print(f"  {len(entries)} trades ({len(entries) - len([e for e in entries if not e.get('source_id')])} from ledger, "
          f"{len([e for e in entries if not e.get('source_id')])} manual)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)
    return sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
