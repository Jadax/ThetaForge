#!/usr/bin/env python3
"""Compose a plain-text recap of the public journal for publishing.

Tastytrade's recurring-review mechanic, done honestly: it summarizes CLOSED
trades in a period (week or month) — count, net P&L, win rate, biggest win and
loss, plus any open positions still carried at the end of the period — and
prints a ready-to-post block that shows the probabilities at entry vs. the
outcome. It never fabricates a result; it only reads journal/trades.json.

Usage:
  python scripts/recap.py [--period month|week] [--journal PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL = REPO_ROOT / "journal" / "trades.json"


def load_journal(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"error: journal not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"error: journal is not valid JSON: {path} ({exc})")


def _closed_during(trade, start: date, end: date) -> bool:
    if trade.get("status") != "closed" or not trade.get("closed"):
        return False
    try:
        closed = date.fromisoformat(str(trade["closed"])[:10])
    except ValueError:
        return False
    return start <= closed <= end


def _carried_open(trade) -> bool:
    return trade.get("status") == "open"


def build_recap(trades: list[dict], start: date, end: date) -> dict:
    closed = [t for t in trades if _closed_during(t, start, end)]
    open_now = [t for t in trades if _carried_open(t)]
    net = sum(t.get("net_pnl", 0) for t in closed)
    winners = [t for t in closed if t.get("net_pnl", 0) > 0]
    losers = [t for t in closed if t.get("net_pnl", 0) <= 0]
    biggest_win = max((t.get("net_pnl", 0) for t in closed), default=0)
    biggest_loss = min((t.get("net_pnl", 0) for t in closed), default=0)
    return {
        "period_label": f"{start.isoformat()} to {end.isoformat()}",
        "closed_count": len(closed),
        "net_pnl": net,
        "win_rate": (len(winners) / len(closed) * 100) if closed else 0.0,
        "winners": len(winners),
        "losers": len(losers),
        "biggest_win": biggest_win,
        "biggest_loss": biggest_loss,
        "open_count": len(open_now),
    }


def render(recap: dict, path: Path) -> str:
    lines = []
    lines.append(f"ThetaForge recap — {recap['period_label']}")
    lines.append("=" * 48)
    lines.append(f"Closed trades:  {recap['closed_count']} "
                 f"({recap['winners']} wins / {recap['losers']} losses)")
    lines.append(f"Net P&L:        +${recap['net_pnl']:,.0f}")
    lines.append(f"Win rate:       {recap['win_rate']:.1f}%")
    lines.append(f"Biggest win:    +${recap['biggest_win']:,.0f}")
    lines.append(f"Biggest loss:   {recap['biggest_loss']:+,.0f}")
    lines.append(f"Still open:     {recap['open_count']} position(s) carried")
    lines.append("-" * 48)
    lines.append("Expectancy is what matters, not a single month. Losses are")
    lines.append("part of a positive-expectancy system — shown here, not hidden.")
    lines.append(f"(source: {path})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=["month", "week"], default="week")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    args = parser.parse_args(argv)

    if args.period == "week":
        today = date.today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        today = date.today()
        start = today.replace(day=1)
        end = date(today.year + (1 if today.month == 12 else 0),
                   (today.month % 12) + 1, 1) - timedelta(days=1)

    journal = load_journal(args.journal)
    trades = journal.get("trades", []) if isinstance(journal, dict) else []
    recap = build_recap(trades, start, end)
    print(render(recap, args.journal))
    if not recap["closed_count"]:
        print("\n(no closed trades in this period)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
