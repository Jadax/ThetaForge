"""Portfolio analytics over the paper-order ledger (the Bridge's shared ledger).

The Bridge ledger is the single source of truth for every paper trade: entry
records carry `strategy`/`symbol`/`max_loss_total`/`status`/timestamps, and
closing records carry `close_of` plus a `realized_pnl`. This module folds
closes into their parents (the same lifecycle view scripts/sync_journal.py
uses) and answers the "how is the book doing" questions — realized P&L and
drawdown, per-strategy and per-sector outcomes, and open-risk concentration —
without inventing data. Anything absent from the ledger is reported as zero or
None, never fabricated.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.trade_engine.recommender import MAX_CORRELATED_POSITIONS, SYMBOL_SECTOR
from agents.trade_engine.trade_manager import MAX_CAPITAL_SLICE_PCT, MAX_POSITIONS


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # NaN guard


def _month_key(timestamp: Any) -> str:
    text = str(timestamp or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return "unknown"


def _position_risk(record: Dict[str, Any]) -> float:
    """Max dollar risk an open entry represents (defined-risk options or stock)."""
    risk = _as_float(record.get("max_loss_total"))
    if risk > 0:
        return risk
    # Long equity: stop-defined risk.
    return _as_float(record.get("risk_per_share")) * _as_float(record.get("quantity", 1), 1)


def fold_ledger(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold closing records into their parent entries.

    Returns {entries, opens, closes} where:
      * entries — entry records (never close records), each gaining the
        realized P&L and close timestamp of its close when one exists;
      * opens   — entries that have no closing record (still live);
      * closes  — chronological list of dicts {symbol, strategy, realized_pnl,
        closed_at, parent_id, reason}.
    """
    close_by_parent: Dict[str, Dict[str, Any]] = {}
    for record in records or []:
        parent_id = record.get("close_of")
        if parent_id:
            close_by_parent[parent_id] = record

    entries: List[Dict[str, Any]] = []
    for record in records or []:
        if record.get("close_of"):
            continue
        entry = dict(record)
        close = close_by_parent.get(record.get("id"))
        if close:
            entry["closed_at"] = close.get("submitted_at") or close.get("closed_at")
            entry["realized_pnl"] = _as_float(close.get("realized_pnl"))
            entry["close_reason"] = close.get("reason", "")
        entries.append(entry)

    opens = [entry for entry in entries if not entry.get("closed_at")]
    closes = [
        {
            "symbol": str(entry.get("symbol", "")).upper(),
            "strategy": str(entry.get("strategy", "")),
            "realized_pnl": entry.get("realized_pnl", 0.0),
            "closed_at": entry.get("closed_at"),
            "parent_id": entry.get("id"),
            "reason": entry.get("close_reason", ""),
        }
        for entry in entries
        if entry.get("closed_at")
    ]
    closes.sort(
        key=lambda c: (str(c["closed_at"] or ""), str(c["parent_id"] or ""))
    )
    return {"entries": entries, "opens": opens, "closes": closes}


def analyze_ledger(
    records: List[Dict[str, Any]],
    capital: float,
    *,
    starting_capital: Optional[float] = None,
) -> Dict[str, Any]:
    """Full portfolio analytics over a Bridge paper-order ledger.

    ``capital`` is current account equity; ``starting_capital`` seeds the
    drawdown math when the account has grown/shrunk away from its starting
    value. All P&L figures are realized dollars from closing records.
    """
    folded = fold_ledger(records)
    entries = folded["entries"]
    opens = folded["opens"]
    closes = folded["closes"]

    # ── realized P&L aggregate ──
    pnls = [close["realized_pnl"] for close in closes]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    net_pnl = sum(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # ── equity curve / drawdown (over the close order) ──
    base = _as_float(starting_capital) if starting_capital else capital
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    curve = []
    for close in closes:
        running += close["realized_pnl"]
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        curve.append({
            "closed_at": close["closed_at"],
            "symbol": close["symbol"],
            "strategy": close["strategy"],
            "realized_pnl": round(close["realized_pnl"], 2),
            "cumulative_pnl": round(running, 2),
        })

    # ── per-strategy ──
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for close in closes:
        key = close["strategy"] or "unknown"
        stats = by_strategy.setdefault(
            key, {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        )
        stats["trades"] += 1
        stats["net_pnl"] += close["realized_pnl"]
        if close["realized_pnl"] > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    # ── per-symbol + per-sector (open risk + realized) ──
    by_symbol: Dict[str, Dict[str, Any]] = {}
    by_sector: Dict[str, Dict[str, Any]] = {}
    for entry in opens:
        symbol = str(entry.get("symbol", "")).upper()
        risk = _position_risk(entry)
        sector = SYMBOL_SECTOR.get(symbol, symbol)  # unknown = own singleton
        sym = by_symbol.setdefault(
            symbol, {"open_risk": 0.0, "realized_pnl": 0.0, "sector": sector, "positions": 0}
        )
        sym["open_risk"] += risk
        sym["positions"] += 1
        sec = by_sector.setdefault(sector, {"open_risk": 0.0, "realized_pnl": 0.0, "symbols": set()})
        sec["open_risk"] += risk
        sec["symbols"].add(symbol)
    for close in closes:
        symbol = close["symbol"]
        sector = SYMBOL_SECTOR.get(symbol, symbol)
        by_symbol.setdefault(
            symbol, {"open_risk": 0.0, "realized_pnl": 0.0, "sector": sector, "positions": 0}
        )["realized_pnl"] += close["realized_pnl"]
        sec = by_sector.setdefault(sector, {"open_risk": 0.0, "realized_pnl": 0.0, "symbols": set()})
        sec["realized_pnl"] += close["realized_pnl"]
        sec["symbols"].add(symbol)
    for sec in by_sector.values():
        sec["symbols"] = len(sec["symbols"])

    # ── monthly realized P&L ──
    by_month: Dict[str, Dict[str, Any]] = {}
    for close in closes:
        month = _month_key(close["closed_at"])
        stats = by_month.setdefault(month, {"trades": 0, "net_pnl": 0.0})
        stats["trades"] += 1
        stats["net_pnl"] += close["realized_pnl"]

    # ── concentration / limits ──
    total_risk = sum(sym["open_risk"] for sym in by_symbol.values())
    sector_counts = {
        sector: sum(
            1 for sym in by_symbol.values()
            if sym["sector"] == sector and sym["open_risk"] > 0
        )
        for sector in {sym["sector"] for sym in by_symbol.values()}
    }
    violations: List[str] = []
    if len(opens) >= MAX_POSITIONS:
        violations.append(f"{len(opens)} open positions at the {MAX_POSITIONS} cap")
    max_symbol_slice = capital * MAX_CAPITAL_SLICE_PCT if capital > 0 else 0.0
    over_allocated = [
        symbol for symbol, sym in by_symbol.items()
        if sym["open_risk"] > max_symbol_slice
    ]
    if over_allocated:
        violations.append(
            "over-allocated: " + ", ".join(sorted(over_allocated))
            + f" (> {MAX_CAPITAL_SLICE_PCT:.0%} of equity each)"
        )
    correlated = [
        sector for sector, count in sector_counts.items()
        if sector in SYMBOL_SECTOR.values() and count > MAX_CORRELATED_POSITIONS
    ]
    if correlated:
        violations.append(
            "sector cap exceeded: " + ", ".join(sorted(correlated))
            + f" (> {MAX_CORRELATED_POSITIONS} positions)"
        )

    by_strategy_out = {
        key: {**stats, "win_rate": (stats["wins"] / stats["trades"] * 100) if stats["trades"] else 0.0}
        for key, stats in sorted(by_strategy.items())
    }
    by_month_out = {
        key: stats for key, stats in sorted(by_month.items())
    }

    return {
        "summary": {
            "total_entries": len(entries),
            "open_positions": len(opens),
            "closed_positions": len(closes),
            "realized_pnl": round(net_pnl, 2),
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "win_rate": (len(wins) / len(pnls) * 100) if pnls else 0.0,
            "expectancy": (net_pnl / len(pnls)) if pnls else 0.0,
            "profit_factor": (
                gross_win / gross_loss if gross_loss > 0
                else float("inf") if gross_win > 0 else 0.0
            ),
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown / base * 100, 2) if base > 0 else 0.0,
            "open_risk": round(total_risk, 2),
            "open_risk_pct_of_equity": round(total_risk / capital * 100, 1) if capital > 0 else 0.0,
        },
        "by_strategy": by_strategy_out,
        "by_symbol": {sym: {k: round(v, 2) if isinstance(v, float) else v for k, v in s.items()} for sym, s in sorted(by_symbol.items())},
        "by_sector": {sec: {k: round(v, 2) if isinstance(v, float) else v for k, v in s.items()} for sec, s in sorted(by_sector.items())},
        "by_month": by_month_out,
        "equity_curve": curve,
        "concentration": {
            "max_positions": MAX_POSITIONS,
            "max_symbol_slice_pct": MAX_CAPITAL_SLICE_PCT,
            "max_correlated_positions": MAX_CORRELATED_POSITIONS,
            "sector_counts": dict(sorted(sector_counts.items())),
            "violations": violations,
        },
    }
