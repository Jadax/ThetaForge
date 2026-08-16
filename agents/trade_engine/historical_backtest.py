"""Lightweight historical outcome backtester (ORATS / Option Alpha pattern).

Replays real credit/debit structures against recorded entry/expiry prices to
produce an empirical win rate, expectancy, profit factor, and drawdown — the
"did this actually work" stat that sits alongside model POP. It never invents
data: the caller supplies realized events, so the numbers are only as honest as
the history they came from.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def _round_to(value: float, multiple: float) -> float:
    if multiple <= 0:
        return float(value)
    return round(value / multiple) * multiple


def credit_spread_pnl(
    expiry_price: float,
    short_strike: float,
    long_strike: float,
    credit: float,
    right: str = "put",
    contracts: int = 1,
) -> float:
    """Net P&L of a closed vertical credit spread (per contract, incl. *100).

    Pays out the full credit when the short strike is untouched; the loss
    grows linearly once the spread is breached, capped at (width - credit).
    Returns dollar P&L for the given number of contracts.
    """
    is_put = right.lower() == "put"
    payout = credit * 100  # keep the credit to a per-contract basis first
    if is_put:
        if expiry_price > short_strike:
            intrinsic_loss = 0.0
        else:
            intrinsic_loss = (short_strike - expiry_price) * 100
    else:
        if expiry_price < short_strike:
            intrinsic_loss = 0.0
        else:
            intrinsic_loss = (expiry_price - short_strike) * 100
    width_risk = (abs(short_strike - long_strike) * 100 - credit * 100) if abs(short_strike - long_strike) > 0 else 0.0
    loss = min(intrinsic_loss, width_risk + credit * 100)
    pnl_per_contract = (credit * 100) - loss
    return round(pnl_per_contract * contracts, 2)


def summarize_outcomes(outcomes: List[float]) -> Dict:
    """Aggregate stats over an ordered list of realized P&L values (chronological)."""
    if not outcomes:
        return {
            "n": 0, "win_rate": 0.0, "expectancy": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
            "wins": 0, "losses": 0, "net_pnl": 0.0,
        }
    wins = [o for o in outcomes if o > 0]
    losses = [o for o in outcomes if o <= 0]
    net = sum(outcomes)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in outcomes:
        running += value
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    return {
        "n": len(outcomes),
        "win_rate": len(wins) / len(outcomes) * 100,
        "expectancy": net / len(outcomes),
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "max_drawdown": round(max_drawdown, 2),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(net, 2),
    }


def backtest_credit_spread(events: List[Dict]) -> Dict:
    """Backtest a list of credit-spread events.

    Each event: {'expiry_price', 'short_strike', 'long_strike', 'credit',
                 'right'='put'}
    Returns summarize_outcomes() over the per-event P&L.
    """
    outcomes = [
        credit_spread_pnl(
            expiry_price=e["expiry_price"],
            short_strike=e["short_strike"],
            long_strike=e["long_strike"],
            credit=e["credit"],
            right=e.get("right", "put"),
        )
        for e in events
    ]
    return summarize_outcomes(outcomes)


def backtest_credit_spread_detailed(events: List[Dict]) -> Dict:
    """Backtest credit-spread events with per-event rows and a monthly split.

    Same input shape as backtest_credit_spread; additionally returns each
    event's computed P&L, a chronological equity curve, and per-month
    aggregation (Option Alpha style "where did it win" breakdown).
    """
    rows = []
    outcomes = []
    for event in events:
        pnl = credit_spread_pnl(
            expiry_price=event["expiry_price"],
            short_strike=event["short_strike"],
            long_strike=event["long_strike"],
            credit=event["credit"],
            right=event.get("right", "put"),
        )
        outcomes.append(pnl)
        rows.append({
            "expiry_price": event["expiry_price"],
            "short_strike": event["short_strike"],
            "long_strike": event["long_strike"],
            "credit": event["credit"],
            "right": event.get("right", "put"),
            "expiry_date": event.get("expiry_date"),
            "pnl": pnl,
        })

    by_month: Dict[str, List[float]] = {}
    for row in rows:
        month = str(row.get("expiry_date") or "")[:7] or "unknown"
        by_month.setdefault(month, []).append(row["pnl"])

    running = 0.0
    curve = []
    for row in rows:
        running += row["pnl"]
        curve.append({"expiry_date": row.get("expiry_date"), "pnl": row["pnl"], "cumulative_pnl": round(running, 2)})

    return {
        "overall": summarize_outcomes(outcomes),
        "by_month": {
            month: summarize_outcomes(monthly)
            for month, monthly in sorted(by_month.items())
        },
        "curve": curve,
        "events": rows,
    }


def backtest_strategy_series(
    closes: List[float],
    *,
    dates: Optional[List[str]] = None,
    right: str = "put",
    dte: int = 14,
    otm_pct: float = 0.02,
    width_pct: float = 0.05,
    credit_fraction: float = 0.25,
    contracts: int = 1,
    round_to: float = 5.0,
    max_trades: int = 500,
) -> Dict:
    """Rolling-window proxy backtest of a short vertical over daily closes.

    TradeStation/EasyLanguage-style: for every day *i* with a close
    ``dte`` trading days later, opens a short vertical whose strikes are
    derived from that day's close (short strike OTM by ``otm_pct``, width
    ``width_pct`` of spot), holds to the later close, and realizes the P&L.

    HONESTY NOTE: free data has no historical option mids, so the credit is
    MODELED as ``width * credit_fraction`` per contract — a proxy, not a
    filled price. Every result carries ``proxy: true`` and the assumptions it
    was computed under, so the output is never mistaken for a backtest of
    real fills.
    """
    if not closes or len(closes) < dte + 1:
        return {
            "proxy": True,
            "assumptions": {"dte": dte, "otm_pct": otm_pct, "width_pct": width_pct,
                            "credit_fraction": credit_fraction, "right": right, "contracts": contracts},
            "overall": summarize_outcomes([]),
            "by_month": {},
            "curve": [],
            "events": [],
            "error": "insufficient price history",
        }

    is_put = right.lower() == "put"
    rows = []
    for i in range(len(closes) - dte):
        if len(rows) >= max_trades:
            break
        entry_price = closes[i]
        expiry_price = closes[i + dte]
        if entry_price <= 0:
            continue

        if is_put:
            short_strike = _round_to(entry_price * (1 - otm_pct), round_to)
            long_strike = _round_to(short_strike - entry_price * width_pct, round_to)
        else:
            short_strike = _round_to(entry_price * (1 + otm_pct), round_to)
            long_strike = _round_to(short_strike + entry_price * width_pct, round_to)
        if short_strike <= 0 or long_strike <= 0 or short_strike == long_strike:
            continue

        width = abs(short_strike - long_strike)
        credit = round(width * credit_fraction, 2)
        pnl = credit_spread_pnl(
            expiry_price=expiry_price,
            short_strike=short_strike,
            long_strike=long_strike,
            credit=credit,
            right=right,
            contracts=contracts,
        )
        entry_date = dates[i] if dates and i < len(dates) else None
        expiry_date = dates[i + dte] if dates and i + dte < len(dates) else None
        rows.append({
            "entry_date": entry_date,
            "expiry_date": expiry_date,
            "entry_price": round(entry_price, 2),
            "expiry_price": round(expiry_price, 2),
            "short_strike": short_strike,
            "long_strike": long_strike,
            "width": width,
            "credit": credit,
            "right": right,
            "pnl": pnl,
        })

    outcomes = [row["pnl"] for row in rows]
    by_month: Dict[str, List[float]] = {}
    for row in rows:
        month = str(row.get("expiry_date") or "")[:7] or "unknown"
        by_month.setdefault(month, []).append(row["pnl"])

    running = 0.0
    curve = []
    for row in rows:
        running += row["pnl"]
        curve.append({
            "expiry_date": row.get("expiry_date"),
            "pnl": row["pnl"],
            "cumulative_pnl": round(running, 2),
        })

    return {
        "proxy": True,
        "assumptions": {
            "dte": dte,
            "otm_pct": otm_pct,
            "width_pct": width_pct,
            "credit_fraction": credit_fraction,
            "round_to": round_to,
            "right": right,
            "contracts": contracts,
            "note": "credit is modeled as width * credit_fraction per contract; "
                    "free historical data has no option mids, so this is a proxy, not real fills",
        },
        "overall": summarize_outcomes(outcomes),
        "by_month": {
            month: summarize_outcomes(monthly)
            for month, monthly in sorted(by_month.items())
        },
        "curve": curve,
        "events": rows,
    }
