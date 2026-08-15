"""Lightweight historical outcome backtester (ORATS / Option Alpha pattern).

Replays real credit/debit structures against recorded entry/expiry prices to
produce an empirical win rate, expectancy, profit factor, and drawdown — the
"did this actually work" stat that sits alongside model POP. It never invents
data: the caller supplies realized events, so the numbers are only as honest as
the history they came from.
"""
from __future__ import annotations

from typing import Dict, List


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
