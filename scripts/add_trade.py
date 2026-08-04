#!/usr/bin/env python3
"""Append or update a trade in the public journal (journal/trades.json).

Usage:
  python scripts/add_trade.py                          # interactive
  python scripts/add_trade.py --from-ledger <id>       # attach narrative to a TWS-placed trade
  python scripts/add_trade.py --symbol SPY --strategy bull_put_credit \
      --status closed --leg "SELL PUT 595 2026-08-21 37" \
      --capital-at-risk 59500 --max-profit 395 --net-pnl 95 \
      --entry-ivr 48 --reason "..." --exit-note "..." --tag theta --yes

Non-interactive when every required flag is supplied. Trades that come from the
TWS ledger carry a `source_id`; `sync_journal.py` regenerates the journal from
the ledger and preserves this narrative by `source_id`. Metrics are recomputed
with the same math the journal page uses, so the preview matches the deploy.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL = REPO_ROOT / "journal" / "trades.json"
DEFAULT_LEDGER = REPO_ROOT / "data" / "paper_order_ledger.json"

STRATEGIES = {
    "cash_secured_put", "covered_call", "bull_put_credit", "bear_call_credit",
    "iron_condor", "put_debit_spread", "call_debit_spread",
}
RIGHT_TO_TYPE = {"C": "CALL", "P": "PUT"}


class CliError(Exception):
    pass


def load_journal(path: Path) -> dict:
    if not path.exists():
        raise CliError(f"Journal not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"Journal is not valid JSON: {path} ({exc})") from exc


def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def next_trade_id(trades: list[dict]) -> str:
    year = date.today().year
    highest = 0
    pattern = re.compile(rf"^TF-{year}-(\d{{3,}})$")
    for trade in trades:
        match = pattern.match(str(trade.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"TF-{year}-{highest + 1:03d}"


def validate_trade(trade: dict) -> None:
    required = [
        "id", "symbol", "opened", "status", "strategy", "legs",
        "entry_ivr", "dte_at_entry", "capital_at_risk", "max_profit",
        "net_pnl", "net_pnl_pct", "reason", "research", "tags", "exit_note",
        "timestamp",
    ]
    for field in required:
        if field not in trade:
            raise CliError(f"Missing required field: {field}")
    if trade["status"] not in {"open", "closed"}:
        raise CliError("status must be 'open' or 'closed'")
    if trade["status"] == "closed" and not trade.get("closed"):
        raise CliError("A closed trade requires a 'closed' date")
    if trade["status"] == "open" and trade.get("closed"):
        raise CliError("An open trade must not have a 'closed' date")
    for key in ("entry_ivr", "dte_at_entry", "capital_at_risk", "max_profit",
                "net_pnl", "net_pnl_pct"):
        value = trade.get(key)
        if value is None:
            continue
        try:
            float(value)
        except (TypeError, ValueError) as exc:
            raise CliError(f"{key} must be a number, got {value!r}") from exc
    if not trade["legs"]:
        raise CliError("A trade requires at least one leg")
    for leg in trade["legs"]:
        if leg["action"] not in {"BUY", "SELL"}:
            raise CliError(f"Leg action must be BUY or SELL, got {leg['action']!r}")
        if leg["type"] not in {"CALL", "PUT", "STOCK"}:
            raise CliError(f"Leg type must be CALL, PUT, or STOCK, got {leg['type']!r}")
    if not trade["reason"] or not trade["exit_note"]:
        raise CliError("reason and exit_note are required")
    if not trade["timestamp"]:
        raise CliError("timestamp is required")


def parse_legs(raw_legs: list[str]) -> list[dict]:
    if not raw_legs:
        return []
    legs = []
    for raw in raw_legs:
        parts = raw.split()
        if len(parts) != 5:
            raise CliError(
                f"Leg must be 'ACTION TYPE STRIKE EXPIRY DTE', got: {raw}"
            )
        action, leg_type, strike, expiry, dte = parts
        if leg_type == "STOCK":
            strike = None
            expiry = None
            dte = None
        else:
            strike = float(strike)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
                raise CliError(f"Leg expiry must be YYYY-MM-DD, got: {expiry}")
            dte = int(dte)
        legs.append({
            "action": action.upper(),
            "type": leg_type.upper(),
            "strike": strike,
            "expiry": expiry,
            "dte": dte,
        })
    return legs


def parse_research(raw_links: list[str]) -> list[dict]:
    links = []
    for raw in raw_links:
        label, _, url = raw.partition(" ")
        if not url:
            raise CliError(f"Research link must be 'LABEL URL', got: {raw}")
        links.append({"label": label, "url": url})
    return links


def prefill_from_ledger(ledger_id: str, ledger: list[dict]) -> dict:
    record = next((item for item in ledger if item.get("id") == ledger_id), None)
    if record is None:
        raise CliError(f"No ledger record with id {ledger_id!r}")
    legs = []
    for leg in record.get("legs", []):
        right = leg.get("right")
        leg_type = RIGHT_TO_TYPE.get(str(right), "CALL")
        expiry = leg.get("expiry") or None
        dte = None
        if expiry:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date()
                       - date.today()).days, 0)
        legs.append({
            "action": str(leg.get("action", "SELL")).upper(),
            "type": leg_type,
            "strike": float(leg["strike"]) if leg.get("strike") else None,
            "expiry": expiry,
            "dte": dte,
        })
    capital = record.get("max_loss_total") or record.get("max_loss_per_combo")
    return {
        "symbol": str(record.get("symbol", "")).upper(),
        "strategy": str(record.get("strategy", "")),
        "legs": legs,
        "capital_at_risk": float(capital) if capital else None,
    }


def build_trade(args: argparse.Namespace, ledger: list[dict]) -> dict:
    prefill = {}
    if args.from_ledger:
        prefill = prefill_from_ledger(args.from_ledger, ledger)

    def pick(flag_value, prefill_value=None, prompt="", default=None):
        if flag_value is not None:
            return flag_value
        if prefill_value is not None:
            return prefill_value
        if args.interactive and prompt:
            suffix = f" [{default}]" if default is not None else ""
            answer = input(f"{prompt}{suffix}: ").strip()
            return answer or default
        return default

    symbol = str(pick(args.symbol, prefill.get("symbol"), "Symbol", "")).upper()
    strategy = str(pick(args.strategy, prefill.get("strategy"), "Strategy", ""))
    if strategy and strategy not in STRATEGIES:
        raise CliError(f"Unknown strategy {strategy!r}; choose from: "
                       + ", ".join(sorted(STRATEGIES)))
    status = str(pick(args.status, None, "Status (open/closed)", "open"))
    opened = str(pick(args.opened, None, "Opened (YYYY-MM-DD)", date.today().isoformat()))
    closed = pick(args.closed, None, "Closed (YYYY-MM-DD)", None) or None
    if status == "closed" and closed is None:
        closed = date.today().isoformat()
    legs = parse_legs(args.leg) if args.leg else None
    if legs is None and prefill.get("legs"):
        legs = prefill["legs"]
    if legs is None:
        if not args.interactive:
            raise CliError("Provide --leg entries or --from-ledger to build legs")
        count = int(input("Number of legs: ").strip())
        legs = []
        for index in range(1, count + 1):
            legs.append(parse_legs([input(
                f"Leg {index} (ACTION TYPE STRIKE EXPIRY DTE): "
            ).strip()])[0])

    entry_ivr = _float(pick(args.entry_ivr, None, "Entry IVR", 0))
    expected_move_pct = _float(
        pick(args.expected_move_pct, None, "Expected move % at entry", None))
    dte_at_entry = _int(pick(args.dte_at_entry, None, "DTE at entry", None))
    capital = _float(pick(args.capital_at_risk, prefill.get("capital_at_risk"),
                          "Capital at risk", None))
    max_profit = _float(pick(args.max_profit, None, "Max profit", None))
    net_pnl = _float(pick(args.net_pnl, None, "Net P&L", 0))
    net_pnl_pct = _float(
        pick(args.net_pnl_pct, None, "Net P&L % of max profit",
             round(net_pnl / max_profit * 100, 1) if max_profit else 0)
    )
    reason = str(pick(args.reason, None, "Thesis (the why)", "")).strip()
    exit_note = pick(args.exit_note, None, "What happened (exit note)", None)
    if exit_note is None and status == "open":
        exit_note = "Open — monitoring for 50% profit or 21 DTE management."
    elif exit_note is None:
        raise CliError("A closed trade requires --exit-note")
    tags = list(args.tag) if args.tag else []
    research = parse_research(args.research) if args.research else []
    management_plan = None
    if args.management_plan:
        management_plan = json.loads(args.management_plan)
    timestamp = args.timestamp or datetime.now().astimezone().isoformat(
        timespec="seconds")

    trade = {
        "id": next_trade_id(args.journal_trades),
        "source": "manual" if not args.from_ledger else "ledger",
        "symbol": symbol,
        "opened": opened,
        "closed": closed,
        "status": status,
        "strategy": strategy,
        "legs": legs,
        "entry_ivr": entry_ivr,
        "expected_move_pct": expected_move_pct,
        "dte_at_entry": dte_at_entry,
        "capital_at_risk": capital,
        "max_profit": max_profit,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "management_plan": management_plan,
        "reason": reason,
        "research": research,
        "tags": tags,
        "exit_note": exit_note,
        "timestamp": timestamp,
    }
    if args.from_ledger:
        trade["source_id"] = args.from_ledger
    validate_trade(trade)
    return trade


def _float(value):
    return None if value is None else float(value)


def _int(value):
    return None if value is None else int(value)


def compute_metrics(trades: list[dict]) -> dict:
    closed = [trade for trade in trades if trade["status"] == "closed"]
    winners = [trade for trade in closed if trade["net_pnl"] > 0]
    losers = [trade for trade in closed if trade["net_pnl"] <= 0]
    gross_win = sum(trade["net_pnl"] for trade in winners)
    gross_loss = abs(sum(trade["net_pnl"] for trade in losers))
    win_rate = len(winners) / len(closed) * 100 if closed else 0
    profit_factor = (
        gross_win / gross_loss if gross_loss > 0
        else float("inf") if gross_win > 0 else 0
    )
    avg_win = gross_win / len(winners) if winners else 0
    avg_loss = gross_loss / len(losers) if losers else 0
    net_pnl = sum(trade["net_pnl"] for trade in trades)
    expectancy = net_pnl / len(closed) if closed else 0

    ordered = sorted(trades, key=lambda trade: trade["opened"])
    running = 0
    cumulative = []
    for trade in ordered:
        running += trade["net_pnl"]
        cumulative.append(running)
    peak = float("-inf")
    max_drawdown = 0.0
    for value in cumulative:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    drawdown_from_peak = 0.0 if cumulative and peak > 0 else 0.0
    if cumulative:
        drawdown_from_peak = max(peak - cumulative[-1], 0)

    streak = 0
    for index in range(len(closed) - 1, -1, -1):
        if ((closed[index]["net_pnl"] > 0)
                == (closed[-1]["net_pnl"] > 0)):
            streak += 1
        else:
            break
    return {
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
        "drawdown_from_peak": drawdown_from_peak,
        "streak": streak,
    }


def print_metrics(metrics: dict, count: int) -> None:
    pf = f"{metrics['profit_factor']:.2f}" if metrics["profit_factor"] != float("inf") else "—"
    print(f"  net P&L:        +${metrics['net_pnl']:,.0f}")
    print(f"  win rate:       {metrics['win_rate']:.1f}%")
    print(f"  profit factor:  {pf}")
    print(f"  expectancy:     {metrics['expectancy']:+.0f} per closed trade")
    print(f"  avg win:        +${metrics['avg_win']:,.0f}")
    print(f"  avg loss:       -${metrics['avg_loss']:,.0f}")
    print(f"  max drawdown:   -${metrics['max_drawdown']:,.0f}")
    print(f"  drawdown/peak:  -${metrics['drawdown_from_peak']:,.0f}")
    print(f"  current streak: {metrics['streak']} "
          f"({count} trades logged)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL,
                        help="Path to journal/trades.json")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER,
                        help="Path to the paper-order ledger JSON")
    parser.add_argument("--from-ledger", metavar="ID", default=None,
                        help="Pre-fill legs/strategy from a paper-order ledger id")
    parser.add_argument("--symbol")
    parser.add_argument("--strategy")
    parser.add_argument("--status", choices=["open", "closed"])
    parser.add_argument("--opened")
    parser.add_argument("--closed")
    parser.add_argument("--leg", action="append",
                        help="Leg as 'ACTION TYPE STRIKE EXPIRY DTE' (repeatable)")
    parser.add_argument("--entry-ivr", type=float)
    parser.add_argument("--expected-move-pct", type=float,
                        help="1-SD expected move (%% of spot) at entry")
    parser.add_argument("--dte-at-entry", type=int)
    parser.add_argument("--capital-at-risk", type=float)
    parser.add_argument("--max-profit", type=float)
    parser.add_argument("--net-pnl", type=float)
    parser.add_argument("--net-pnl-pct", type=float)
    parser.add_argument("--management-plan", help="JSON object of exit rules")
    parser.add_argument("--reason")
    parser.add_argument("--exit-note")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--research", action="append",
                        help="Research link as 'LABEL URL' (repeatable)")
    parser.add_argument("--timestamp")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never prompt; require all needed flags")
    args = parser.parse_args(argv)
    args.interactive = not args.non_interactive

    try:
        journal = load_journal(args.journal)
        trades = journal.get("trades", [])
        if not isinstance(trades, list):
            raise CliError("Journal has no 'trades' list")
        args.journal_trades = trades
        ledger = load_ledger(args.ledger)
        trade = build_trade(args, ledger)

        if not args.yes and args.interactive:
            print(f"  {trade['id']} {trade['symbol']} · {trade['strategy']} "
                  f"({trade['status']})")
            if input("Write to journal? [y/N]: ").strip().lower() != "y":
                print("Aborted — nothing written.")
                return 1

        journal["as_of"] = date.today().isoformat()
        journal["trades"] = [trade] + trades
        args.journal.write_text(
            json.dumps(journal, indent=2) + "\n", encoding="utf-8")

        print(f"Wrote {trade['id']} to {args.journal}")
        print("Journal metrics now:")
        print_metrics(compute_metrics(journal["trades"]), len(journal["trades"]))
        return 0
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
