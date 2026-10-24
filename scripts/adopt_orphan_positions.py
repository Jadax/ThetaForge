"""Re-adopt carried journal entries whose shares IBKR still holds.

When the executor VM moved to a fresh Bridge ledger, the journal kept the old
receipts (marked ``carried``) but the new ledger never knew those positions:
the exit manager couldn't see them, so their stops were never enforced (Oct
2026: MARA, AXTI, AEHR, INTC, NOK, NVDA, QQQ, SPY, XLK and 24 XLV shares sat
unmanaged for four weeks, most far below their stops).

This tool reconciles them against the IBKR paper account itself. For each
symbol, the shares no live ledger record accounts for (IBKR quantity minus
open, filled ledger equity) are matched to that symbol's carried entries,
oldest first, and each fully covered entry is appended to the ledger as a
Filled equity record under its original id -- so the journal re-links it and
the exit manager starts managing it. IBKR is the source of truth for whether a
carried order filled: the old ledger froze working DAY orders at "Submitted",
so a carried "0 filled" receipt is not evidence of no fill.

Places no orders. Dry-run unless --apply; --apply backs the ledger up first.
Run it on the executor VM with the Bridge stopped (it rewrites the ledger).

    python scripts/adopt_orphan_positions.py \
        --ledger /opt/thetaforge-bridge/data/paper_order_ledger.json \
        --journal /opt/thetaforge-bridge/ThetaForge/journal/trades.json [--apply]
"""
import argparse
import asyncio
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def managed_shares(ledger: list[dict]) -> dict[str, float]:
    shares: dict[str, float] = defaultdict(float)
    for record in ledger:
        if record.get("asset_class") != "equity" or record.get("close_of") or record.get("closed_by"):
            continue
        shares[str(record.get("symbol", "")).upper()] += float(record.get("filled", 0) or 0)
    return shares


def adoption_records(
    ledger: list[dict], journal_trades: list[dict], ibkr_shares: dict[str, float], now: str
) -> list[dict]:
    """Ledger records to append for carried entries IBKR proves are held."""
    known_ids = {str(record.get("id")) for record in ledger}
    unmanaged = {
        symbol: quantity - managed_shares(ledger).get(symbol, 0.0)
        for symbol, quantity in ibkr_shares.items()
    }
    carried = sorted(
        (
            trade for trade in journal_trades
            if trade.get("carried") and trade.get("asset_class") == "equity"
            and str(trade.get("source_id")) not in known_ids
        ),
        key=lambda trade: str((trade.get("order") or {}).get("submitted_at") or trade.get("opened", "")),
    )
    adopted = []
    for trade in carried:
        symbol = str(trade.get("symbol", "")).upper()
        order = trade.get("order") or {}
        quantity = float(order.get("quantity") or trade.get("shares") or 0)
        if quantity <= 0 or unmanaged.get(symbol, 0.0) < quantity:
            continue
        entry = float(trade.get("entry_price") or order.get("average_fill_price") or 0)
        stop = float(trade.get("stop_price") or 0)
        if entry <= 0 or stop <= 0 or stop >= entry:
            continue
        unmanaged[symbol] -= quantity
        fill_price = float(order.get("average_fill_price") or 0) or entry
        adopted.append({
            "id": str(trade["source_id"]),
            "asset_class": "equity",
            "recommendation_id": f"adopted:{trade['source_id']}",
            "strategy": trade.get("strategy"),
            "symbol": symbol,
            "quantity": quantity,
            "conditions": trade.get("conditions"),
            "ibkr_order_id": order.get("ibkr_order_id"),
            "status": "Filled",
            "filled": quantity,
            "remaining": 0.0,
            "average_fill_price": fill_price,
            "limit_price": order.get("limit_price") or fill_price,
            "quote_quality": "adopted",
            "entry_price": entry,
            "stop_price": stop,
            "target_price": trade.get("target_price"),
            "highest_high": entry,
            "risk_per_share": round(entry - stop, 2),
            "net_credit": 0.0,
            "max_loss_per_combo": 0.0,
            "max_loss_total": round((entry - stop) * quantity, 2),
            # The original week: an adopted position must not reserve this
            # week's fresh capital budget.
            "week_key": order.get("week_key") or "adopted",
            "submitted_at": order.get("submitted_at") or f"{trade.get('opened')}T00:00:00+00:00",
            "updated_at": now,
            "adopted_at": now,
            "error_message": "re-adopted from a carried journal receipt after IBKR position reconciliation",
        })
    return adopted


async def fetch_ibkr_shares(host: str, port: int, client_id: int) -> dict[str, float]:
    from ib_insync import IB
    import ib_insync.connection as ib_connection

    ib_connection.getLoop = asyncio.get_running_loop
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id, timeout=15, readonly=True)
    try:
        await asyncio.sleep(2)
        if not any(account.upper().startswith("DU") for account in ib.managedAccounts()):
            raise SystemExit("refusing: connected account is not a paper (DU) account")
        return {
            position.contract.symbol.upper(): float(position.position)
            for position in ib.positions() if position.contract.secType == "STK"
        }
    finally:
        ib.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--positions", type=Path, help="JSON {symbol: shares} instead of querying IBKR")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=79)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    ledger = load_json(args.ledger, [])
    if not isinstance(ledger, list):
        raise SystemExit("error: ledger must be a JSON list")
    journal = load_json(args.journal, {}).get("trades", [])
    if args.positions:
        ibkr_shares = {k.upper(): float(v) for k, v in load_json(args.positions, {}).items()}
    else:
        ibkr_shares = asyncio.run(fetch_ibkr_shares(args.host, args.port, args.client_id))

    adopted = adoption_records(ledger, journal, ibkr_shares, datetime.now(timezone.utc).isoformat())
    for record in adopted:
        print(f"adopt {record['symbol']:6} {record['quantity']:>6g} sh  entry {record['entry_price']:.2f}"
              f"  stop {record['stop_price']:.2f}  id {record['id']}")
    if not adopted:
        print("nothing to adopt")
        return 0
    if not args.apply:
        print(f"dry run: {len(adopted)} record(s) would be appended (re-run with --apply)")
        return 0
    backup = args.ledger.with_name(args.ledger.name + f".pre-adopt-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(args.ledger, backup)
    args.ledger.write_text(json.dumps(ledger + adopted, indent=2), encoding="utf-8")
    print(f"appended {len(adopted)} record(s); backup at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
