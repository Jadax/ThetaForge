"""Long-equity momentum backtest (TradeStation / EquiFlex Pro pattern).

Event-driven daily simulator over a stock's own daily closes: enters long
when the trend/momentum gates that the equity brain actually uses all agree
(price above SMA200, SMA50 above SMA200, RSI below the cap, positive
momentum), and exits on trend failure or a holding-period time stop. Output is
the same summary shape as the options backtest (win rate, expectancy, profit
factor, drawdown) so the dashboard can render both engines identically.

HONESTY: this is a simulated equity curve from free daily closes — no
commissions, slippage, or fill timing. Results carry ``proxy: true``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def backtest_momentum(
    closes: List[float],
    *,
    dates: Optional[List[str]] = None,
    rsi_max: float = 70.0,
    sma_fast: int = 50,
    sma_slow: int = 200,
    momentum_days: int = 126,
    max_holding_days: int = 90,
    rsi_exit_buffer: float = 5.0,
) -> Dict:
    """Backtest a simple trend-following long entry on daily closes.

    Entry (all must hold): close > SMA(slow), SMA(fast) > SMA(slow),
    RSI < ``rsi_max``, and positive ``momentum_days`` return.
    Exit (any): close < SMA(fast), RSI > ``rsi_max + buffer``, momentum turns
    negative, or ``max_holding_days`` elapsed. One position at a time.
    """
    if pd is None:
        return _empty_result("pandas unavailable")
    if not closes or len(closes) <= sma_slow + momentum_days + 1:
        return _empty_result("insufficient price history")

    series = pd.Series(closes, dtype="float64")
    sma_fast_s = series.rolling(sma_fast).mean()
    sma_slow_s = series.rolling(sma_slow).mean()
    rsi_s = _rsi(series, 14)
    momentum_s = series.pct_change(momentum_days) * 100.0

    trades: List[Dict] = []
    entry_idx: Optional[int] = None
    entry_price: Optional[float] = None
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    curve: List[Dict] = []

    n = len(series)
    for i in range(sma_slow, n):
        fast = sma_fast_s.iloc[i]
        slow = sma_slow_s.iloc[i]
        rsi = rsi_s.iloc[i]
        mom = momentum_s.iloc[i]
        if pd.isna(fast) or pd.isna(slow) or pd.isna(rsi) or pd.isna(mom):
            continue

        if entry_idx is None:
            close = series.iloc[i]
            if (close > slow and fast > slow and rsi < rsi_max and mom > 0):
                entry_idx = i
                entry_price = close
        else:
            close = series.iloc[i]
            held = i - entry_idx
            exit_signal = (
                close < fast
                or rsi > rsi_max + rsi_exit_buffer
                or mom < 0
                or held >= max_holding_days
            )
            if exit_signal:
                pnl_pct = (close / entry_price - 1.0) * 100.0
                cumulative += pnl_pct
                peak = max(peak, cumulative)
                max_drawdown = max(max_drawdown, peak - cumulative)
                trades.append({
                    "entry_date": dates[entry_idx] if dates and entry_idx < len(dates) else None,
                    "exit_date": dates[i] if dates and i < len(dates) else None,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(close, 2),
                    "holding_days": held,
                    "pnl_pct": round(pnl_pct, 2),
                })
                curve.append({"exit_date": trades[-1]["exit_date"], "cumulative_pnl_pct": round(cumulative, 2)})
                entry_idx = None
                entry_price = None

    # Flatten any still-open position at the final close.
    if entry_idx is not None and entry_price is not None:
        close = series.iloc[-1]
        pnl_pct = (close / entry_price - 1.0) * 100.0
        cumulative += pnl_pct
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        trades.append({
            "entry_date": dates[entry_idx] if dates and entry_idx < len(dates) else None,
            "exit_date": dates[-1] if dates else None,
            "entry_price": round(entry_price, 2),
            "exit_price": round(close, 2),
            "holding_days": n - 1 - entry_idx,
            "pnl_pct": round(pnl_pct, 2),
        })
        curve.append({"exit_date": trades[-1]["exit_date"], "cumulative_pnl_pct": round(cumulative, 2)})

    pnls = [trade["pnl_pct"] for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    holding_days = [trade["holding_days"] for trade in trades]

    return {
        "proxy": True,
        "assumptions": {
            "rsi_max": rsi_max,
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "momentum_days": momentum_days,
            "max_holding_days": max_holding_days,
            "note": "simulated equity curve from free daily closes; no commissions, slippage, or fill timing",
        },
        "overall": {
            "n": len(trades),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
            "expectancy_pct": sum(pnls) / len(pnls) if pnls else 0.0,
            "avg_win_pct": gross_win / len(wins) if wins else 0.0,
            "avg_loss_pct": gross_loss / len(losses) if losses else 0.0,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
            "max_drawdown_pct": round(max_drawdown, 2),
            "net_pnl_pct": round(sum(pnls), 2),
            "avg_holding_days": round(sum(holding_days) / len(holding_days), 1) if holding_days else 0.0,
            "wins": len(wins),
            "losses": len(losses),
        },
        "curve": curve,
        "trades": trades,
    }


def _empty_result(reason: str) -> Dict:
    return {
        "proxy": True,
        "error": reason,
        "overall": {"n": 0, "win_rate": 0.0, "expectancy_pct": 0.0,
                    "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "profit_factor": 0.0,
                    "max_drawdown_pct": 0.0, "net_pnl_pct": 0.0, "avg_holding_days": 0.0,
                    "wins": 0, "losses": 0},
        "curve": [],
        "trades": [],
    }
