#!/usr/bin/env python3
"""Regenerate the public journal from the TWS paper-order ledger.

The public journal (journal/trades.json) only ever shows trades that were
recommended by ThetaForge and placed on the paper account — i.e. records in the
paper-order ledger (data/paper_order_ledger.json). This script:

  * builds a fresh entry for every ledger record (requiring a
    recommendation_id, and excluding cancelled/never-executed orders),
  * collapses closing orders (ledger records carrying a `close_of` id) into
    their parent entry, marking it `closed` with the exit receipt and
    realized P&L — a close is a lifecycle event on the position, never a
    phantom new "open" trade,
  * retains previously published ledger entries whose record is absent from
    the current ledger file entirely (marked `carried: true`) — e.g. after the
    executor VM migrated to a fresh Bridge ledger — so a ledger that starts
    over can never silently wipe already-published receipts; a record that is
    still present but filtered out (cancelled / never filled) is still dropped,
  * overlays the human narrative (thesis, exit note, tags, P&L, close date)
    that was authored via `add_trade.py --from-ledger <id>` by matching
    `source_id` on the existing journal, plus any entry `conditions` captured
    at recommendation time when the ledger record does not carry its own,
  * keeps user-authored entries that have no `source_id` (manual trades added
    via the CLI) untouched,
  * with `--refresh-prices`, fetches a delayed quote for every open position
    (stock price for equity legs, leg-scored chain mark for option spreads)
    via the same free-provider stack the scanners use, and overlays it as
    `live` plus an appended `price_trail` snapshot history. Snapshots are
    appended at most every 30 minutes or when the price moves >= 0.1%, and
    the trail is capped, so the public site can render current P&L, the
    stop/target journey, and a since-entry sparkline without any client-side
    quote API. A failed fetch keeps the previously published values — a
    mark is never fabricated.
  * recomputes nothing stored (metrics are computed at render time),
  * writes journal/trades.json with `as_of` set to today.

Usage:
  python scripts/sync_journal.py [--journal PATH] [--ledger PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from journal_common import journal_legs, ledger_capital_at_risk  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL = REPO_ROOT / "journal" / "trades.json"
DEFAULT_LEDGER = REPO_ROOT / "data" / "paper_order_ledger.json"

EXCLUDED_STATUSES = {"ApiCancelled", "Cancelled", "Inactive", "PendingCancel"}

# Live-mark refresh (--refresh-prices). The trail feeds the public journal's
# current-P&L overlay, the stop/target journey and the since-entry sparkline;
# points are appended hourly-ish by the VM's journal timer, so dedupe to
# market-meaningful movement only.
PRICE_TRAIL_CAP = 240          # ~10 market days of in-session hourly points
TRAIL_MIN_GAP_SECONDS = 1800   # append anyway after 30 min, else only on movement
TRAIL_MIN_DELTA_PCT = 0.001    # 0.1% price movement counts as movement


def _default_provider_factory():
    """Builds the shared free-data provider (IBKR proxy > CBOE > yfinance).

    Imported lazily so the plain journal rebuild never pays the yfinance
    import. Tests monkeypatch `provider_factory` instead.
    """
    from agents.data_ingestion.free_data import FreeDataProvider
    return FreeDataProvider()


provider_factory = _default_provider_factory

# Liquid broad-market/sector ETFs — the equity universe's rotation core
# (agents/equity_trader/equity_universe.py LIQUID_ETF_CORE). Kept in sync so the
# journal can label an equity leg as ETF vs single stock from data alone.
ETF_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLB", "XLC", "XLU", "XLRE",
}

DEFAULT_TRADER = {
    "name": "Tushant Sharma",
    "handle": "@thetaforge",
    "tagline": "Two engines, one ledger: defined-risk option seller and momentum equity trader. Receipts always.",
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


def _opened_as_date(opened: str):
    return datetime.fromisoformat(opened).date() if "T" in opened else date.fromisoformat(opened)


def _order_block(record: dict) -> dict:
    """Raw order facts from the ledger, surfaced as the receipt."""
    return {
        "status": record.get("status"),
        "filled": record.get("filled"),
        "remaining": record.get("remaining"),
        "average_fill_price": record.get("average_fill_price"),
        "limit_price": record.get("limit_price"),
        "net_credit": record.get("net_credit"),
        "cost_to_close": record.get("cost_to_close"),
        "realized_pnl": record.get("realized_pnl"),
        "reason": record.get("reason"),
        "quantity": record.get("quantity"),
        "submitted_at": record.get("submitted_at"),
        "updated_at": record.get("updated_at"),
    }


def _equity_management_plan(record: dict) -> dict:
    """Exit/management rules for a long equity (stock/ETF) position, taken from
    the actual stop/target the Bridge wrote into the ledger record."""
    stop = record.get("stop_price")
    target = record.get("target_price")
    return {
        "target": f"take profit at 2R target {target}" if target else "take profit at +2R",
        "stop": f"hard stop at {stop}" if stop else "hard stop at 2x ATR below entry",
        "time": "re-examine after 60 days",
        "event": "close before earnings or scheduled macro if inside the blackout window",
    }


def _default_management_plan(strategy: str, asset_class: str = "options") -> dict:
    """TastyTrade-flavoured exit/management rules attached to every trade.

    Stored as data and rendered on the card so the audience sees a repeatable,
    rule-driven management plan rather than a whim. The short-leg strategies
    use 50% max-profit targets and 21 DTE management; debit (long) structures
    use breakeven stops and a fixed hold window; equity longs use the ATR
    stop / 2R target written into the ledger.
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


def _instrument_type(asset_class: str, symbol: str) -> str:
    """option | stock | etf — what the dashboard breaks P&L down by."""
    if asset_class == "equity":
        return "etf" if symbol in ETF_TICKERS else "stock"
    return "option"


def build_entry(record: dict, overlay: dict) -> dict:
    ledger_id = str(record.get("id", ""))
    symbol = str(record.get("symbol", "")).upper()
    strategy = str(record.get("strategy", ""))
    asset_class = str(record.get("asset_class") or "options")
    submitted_at = str(record.get("submitted_at", ""))
    opened = _opened_date(submitted_at)

    legs = journal_legs(
        record.get("legs", []),
        as_of=_opened_as_date(opened),
    )

    if asset_class == "equity":
        management_plan = _equity_management_plan(record)
        reason = (
            f"Long {symbol}, momentum/trend long placed on the paper account "
            "from the ThetaForge equity recommendation."
        )
    else:
        management_plan = _default_management_plan(strategy, asset_class)
        reason = (f"{strategy.replace('_', ' ').title()} on {symbol}, "
                  f"placed on the paper account from the ThetaForge recommendation.")

    base = {
        "id": ledger_id,
        "source_id": ledger_id,
        "source": "ledger",
        "ledger_ref": ledger_id,
        "order": _order_block(record),
        "symbol": symbol,
        "asset_class": asset_class,
        "instrument_type": _instrument_type(asset_class, symbol),
        "shares": record.get("quantity") if asset_class == "equity" else None,
        "opened": opened,
        "closed": None,
        "status": "open",
        "strategy": strategy,
        "legs": legs,
        "entry_ivr": None,
        "expected_move_pct": None,
        "dte_at_entry": max((leg["dte"] for leg in legs if leg["dte"]), default=None),
        "entry_price": record.get("entry_price") or record.get("average_fill_price"),
        "stop_price": record.get("stop_price"),
        "target_price": record.get("target_price"),
        "capital_at_risk": ledger_capital_at_risk(record) or 0.0,
        "max_profit": float(record.get("net_credit") or 0),
        "net_pnl": 0.0,
        "net_pnl_pct": 0.0,
        "conditions": record.get("conditions"),
        "management_plan": management_plan,
        "reason": reason,
        "research": [],
        "tags": [],
        "exit_note": "Open · monitoring the position.",
        "timestamp": record.get("updated_at") or submitted_at,
    }
    for key in ("entry_ivr", "expected_move_pct", "dte_at_entry", "net_pnl",
                "net_pnl_pct", "capital_at_risk", "max_profit",
                "live", "price_trail"):
        if overlay.get(key) is not None:
            base[key] = overlay[key]
    for key in ("closed", "status", "reason", "research", "tags", "exit_note",
                "timestamp", "management_plan", "conditions"):
        if overlay.get(key):
            # The ledger record's own captured conditions win over a previously
            # stored copy — the record is the source of truth for its trade.
            if key == "conditions" and base.get("conditions"):
                continue
            base[key] = overlay[key]
    return base


def _ledger_sha(consumed: list[dict]) -> str:
    """SHA-256 over the exact ledger records the journal was built from.

    Publishing this lets a reader recompute the journal from the same ledger
    state and detect any silent edit (FTMO/Myfxbook-style verification).
    """
    canonical = json.dumps(consumed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _exit_note(reason: str) -> str:
    """Readable exit note for an automatically managed close."""
    key = (reason or "").lower()
    if "close_profit" in key or "profit" in key:
        return "Auto-closed at the 50% of max-credit take-profit target."
    if "close_time" in key or "dte" in key:
        return "Auto-closed at the 21-DTE gamma management window."
    if "close_loss" in key or "loss" in key:
        return "Auto-closed at the 2x-credit loss stop."
    if "close_pre_earnings" in key or "earnings" in key:
        return "Auto-closed before the earnings event."
    if "close_pre_macro" in key or "macro" in key:
        return "Auto-closed before the scheduled macro event (FOMC/CPI/NFP)."
    if "close_stop" in key:
        return "Auto-closed at the 2x-ATR hard stop."
    if "close_trail" in key:
        return "Auto-closed at the trailing chandelier stop after the position went +1R."
    return "Closed by the ThetaForge management loop."


def _apply_close(entry: dict, close: dict) -> None:
    """Fold a closing ledger record into its parent entry."""
    close_submitted = str(close.get("submitted_at", ""))
    close_date = _opened_date(close_submitted) if close_submitted else date.today().isoformat()
    reason = str(close.get("reason") or "")
    realized = close.get("realized_pnl")
    if isinstance(realized, (int, float)):
        entry["net_pnl"] = round(float(realized), 2)
        capital = float(entry.get("capital_at_risk") or 0)
        entry["net_pnl_pct"] = round(float(realized) / capital, 4) if capital > 0 else None
    entry["closed"] = close_date
    entry["status"] = "closed"
    entry["exit_note"] = _exit_note(reason)
    entry["close_order"] = _order_block(close)
    entry["timestamp"] = close.get("updated_at") or close_submitted


def _entry_mark_reference(entry: dict) -> tuple[Optional[float], Optional[str]]:
    """The (price, timestamp) the trail should be seeded from: the actual
    fill for an equity leg, the per-contract entry credit for a spread."""
    if entry.get("asset_class") == "equity":
        price = entry.get("entry_price")
        if price:
            return float(price), str(entry.get("opened") or "")
        return None, None
    order = entry.get("order") or {}
    credit = order.get("net_credit")
    qty = float(order.get("quantity") or 0)
    if credit and qty > 0:
        return abs(float(credit)) / qty, str(entry.get("opened") or "")
    return None, None


def _should_trail(existing: list, price: float, now: datetime) -> bool:
    """Trail sparsely: skip a point when nothing moved and the gap is short."""
    if not existing:
        return True
    last = existing[-1]
    last_price = last.get("price")
    try:
        moved = abs(float(price) - float(last_price)) >= TRAIL_MIN_DELTA_PCT * abs(float(last_price))
    except (TypeError, ValueError):
        moved = True
    if moved:
        return True
    last_at = str(last.get("at") or "")
    try:
        gap = (now - datetime.fromisoformat(last_at.replace("Z", "+00:00"))).total_seconds()
        return gap >= TRAIL_MIN_GAP_SECONDS
    except ValueError:
        return True


def _option_mark(chain: list, legs: list) -> Optional[float]:
    """Per-contract mid mark of a vertical spread from the merged chain:
    SELL legs add, BUY legs subtract. Any unquotable leg yields None —
    a partial mark would silently misstate the position."""
    if not chain or not legs:
        return None
    mark = 0.0
    for leg in legs:
        expiry = str(leg.get("expiry") or "")
        try:
            strike = float(leg.get("strike") or 0)
        except (TypeError, ValueError):
            return None
        option_type = str(leg.get("type") or "").upper()
        row = None
        for candidate in chain:
            if (str(candidate.get("expiry") or "") == expiry
                    and abs(float(candidate.get("strike") or 0) - strike) < 1e-6
                    and str(candidate.get("option_type") or "").upper() == option_type):
                row = candidate
                break
        if row is None:
            return None
        try:
            bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else float(row.get("last") or 0)
        except (TypeError, ValueError):
            return None
        if mid <= 0:
            return None
        mark += mid if str(leg.get("action", "SELL")).upper() == "SELL" else -mid
    return mark


async def _refresh_entries(entries: list) -> tuple[int, int]:
    """Overlay `live` marks and append `price_trail` points on open entries.

    Returns (refreshed, skipped). A skipped entry keeps its previously
    published `live`/`price_trail` overlay verbatim — never a fabricated mark.
    """
    now = datetime.now(timezone.utc)
    provider = provider_factory()
    refreshed = skipped = 0
    for entry in entries:
        if entry.get("status") == "closed" or not entry.get("symbol"):
            continue
        symbol = str(entry["symbol"]).upper()
        try:
            if entry.get("asset_class") == "equity":
                price = await provider.get_stock_price(symbol)
                mark = float(price) if price and float(price) > 0 else None
                kind = "stock"
            else:
                chain = await provider.get_option_chain(symbol)
                mark = _option_mark(chain, entry.get("legs") or [])
                kind = "spread"
        except Exception:
            skipped += 1
            continue
        if mark is None or mark <= 0:
            skipped += 1
            continue
        rounded = round(mark, 4)
        entry["live"] = {"price": rounded, "at": now.isoformat(timespec="seconds"), "kind": kind}
        trail = list(entry.get("price_trail") or [])
        if not trail:
            seed_price, seed_at = _entry_mark_reference(entry)
            if seed_price:
                trail.append({"at": seed_at, "price": round(seed_price, 4)})
        if _should_trail(trail, rounded, now):
            trail.append({"at": now.isoformat(timespec="seconds"), "price": rounded})
        entry["price_trail"] = trail[-PRICE_TRAIL_CAP:]
        refreshed += 1
    return refreshed, skipped


def _refresh_live_marks(entries: list) -> tuple[int, int]:
    """Sync entry point for the price refresh (the generators above are async)."""
    import asyncio
    return asyncio.run(_refresh_entries(entries))


def _standalone_close_entry(close: dict) -> dict:
    """Emit a close record as its own entry when its parent is absent."""
    symbol = str(close.get("symbol", "")).upper()
    strategy = str(close.get("strategy", ""))
    entry = build_entry(close, {})
    entry["status"] = "closed"
    entry["exit_note"] = _exit_note(str(close.get("reason") or ""))
    entry["close_order"] = _order_block(close)
    entry["reason"] = (
        f"Closing order for {symbol} ({strategy.replace('_', ' ').title()}): "
        "the parent entry was not found in the ledger."
    )
    return entry


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
    closes = []
    seen_sources = set()
    consumed = []
    for record in ledger:
        status = str(record.get("status", ""))
        if status in EXCLUDED_STATUSES:
            continue
        # An order that never executed (still "Submitted" with no fill, or a
        # day order that lapsed unfilled) is not a trade. Only ledger records
        # with an actual fill belong in the public journal — anything else is
        # an intent, not a receipt. The bridge always writes `filled` (0 for a
        # never-executed order), so only an explicitly-set 0 is filtered; a
        # record that omits the field is treated as filled (mirrors the
        # executor's own fetch_open_equity_symbols guard).
        if "filled" in record and float(record.get("filled") or 0) <= 0:
            continue
        source = str(record.get("id", ""))
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)
        consumed.append(record)
        if record.get("close_of"):
            closes.append(record)
            continue
        if not record.get("recommendation_id"):
            continue
        entries.append(build_entry(record, overlay_by_source.get(source, {})))

    # Ids that exist anywhere in the current ledger file, including records the
    # journal filters out (cancelled, never filled). Physical presence decides
    # carried-vs-dropped: a missing id means this ledger never knew that trade.
    ledger_ids = {str(record.get("id", "")) for record in ledger}

    for trade in existing:
        if not trade.get("source_id"):
            trade = dict(trade)
            trade.setdefault("source", "manual")
            entries.append(trade)
        elif str(trade["source_id"]) not in ledger_ids:
            # Published entry whose ledger record is absent from this ledger
            # file — the executor VM migrated to a fresh Bridge ledger. Keep the
            # receipt; a ledger that starts over must not erase real trades.
            trade = dict(trade)
            trade["carried"] = True
            entries.append(trade)

    entries_by_source = {str(entry.get("source_id")): entry for entry in entries if entry.get("source_id")}
    for close in closes:
        parent = entries_by_source.get(str(close.get("close_of")))
        if parent is not None:
            _apply_close(parent, close)
        else:
            entries.append(_standalone_close_entry(close))

    refreshed = 0
    if args.refresh_prices:
        try:
            refreshed, skipped = _refresh_live_marks(entries)
            print(f"  live marks: {refreshed} refreshed, {skipped} kept from the last snapshot")
        except Exception as error:
            print(f"  live mark refresh failed (kept previous overlays): {error}")

    entries.sort(key=lambda trade: (trade.get("opened", ""), trade.get("timestamp", "")))
    entries.reverse()

    new_sha = _ledger_sha(consumed)
    carried_entries = [trade for trade in entries if trade.get("carried")]
    prior_sha = None
    if carried_entries:
        prior_verification = current.get("verification") if isinstance(current, dict) else None
        if isinstance(prior_verification, dict):
            prior_sha = prior_verification.get("prior_ledger_sha") or prior_verification.get("ledger_sha")
        if prior_sha == new_sha:
            prior_sha = None

    verification = {
        "generator": "scripts/sync_journal.py",
        "ledger_source": str(args.ledger),
        "ledger_sha": new_sha,
        "entries_from_ledger": len(consumed),
        "entries_manual": len([e for e in entries if not e.get("source_id")]),
        "entries_carried": len(carried_entries),
    }
    if prior_sha:
        verification["prior_ledger_sha"] = prior_sha

    journal = {
        "trader": (current.get("trader") if isinstance(current, dict)
                   else None) or DEFAULT_TRADER,
        "as_of": date.today().isoformat(),
        "account_equity": (current.get("account_equity") if isinstance(current, dict)
                           else None) or 25000,
        "verification": verification,
        "trades": entries,
    }
    args.journal.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")

    print(f"Journal rebuilt at {args.journal}")
    manual_count = len([e for e in entries if not e.get("source_id")])
    print(f"  {len(entries)} trades ({len(entries) - manual_count - len(carried_entries)} from ledger, "
          f"{manual_count} manual, {len(carried_entries)} carried)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--refresh-prices", action="store_true",
        help="Fetch delayed marks for open positions (stock price / leg-scored "
             "option chain mid) and overlay live + trail price history onto the "
             "journal. Used by the VM's hourly journal timer; a failed fetch "
             "keeps the previously published values.")
    args = parser.parse_args(argv)
    return sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
