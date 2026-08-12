"""Tests for the general-trader market overview (stocks/ETFs/bonds)."""
from datetime import datetime

import pandas as pd
import pytest

from agents.general_trader.market_overview import MarketOverview


def _frame(closes, volume=None):
    index = pd.date_range(end=datetime(2026, 8, 12), periods=len(closes), freq="B")
    data = {
        "Close": closes,
        "High": [value * 1.01 for value in closes],
        "Low": [value * 0.99 for value in closes],
    }
    if volume is not None:
        data["Volume"] = volume
    return pd.DataFrame(data, index=index)


class _Provider:
    """Stands in for FreeDataProvider with scripted history frames."""

    def __init__(self, frames, sectors=None):
        self.frames = frames
        self.sectors = sectors or {"Technology": 1.5, "Financials": -0.5}

    async def get_historical_prices(self, symbol, period="1y", interval="1d"):
        return self.frames.get(symbol, pd.DataFrame())

    async def get_sector_performance(self):
        return dict(self.sectors)


def _uptrend_frame(n=260, base=100.0, step=2.0):
    return _frame([base + index * step for index in range(n)])


def test_overview_builds_all_asset_groups():
    frames = {symbol: _uptrend_frame() for symbol in ("^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^IRX", "^TNX", "TLT", "GLD")}
    overview = MarketOverview(_Provider(frames)).overview

    output = _run(overview())

    assert set(output["indices"]) == {"^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"}
    assert "^TNX" in output["bonds"]
    assert "GLD" in output["commodities"]
    assert output["sectors"] == {"Technology": 1.5, "Financials": -0.5}
    assert output["yield_curve"]["long"] is not None
    assert output["yield_curve"]["shape"] in ("normal", "inverted", None)


def test_overview_drops_missing_assets_fail_closed():
    frames = {"^GSPC": _uptrend_frame(), "BAD": pd.DataFrame()}
    overview = MarketOverview(_Provider(frames)).overview

    output = _run(overview())

    assert "^GSPC" in output["indices"]
    assert "^IXIC" not in output["indices"]


def test_risk_tilt_risk_on_when_equity_and_credit_agree():
    up = _uptrend_frame()
    tlt_down = _frame([100 - index * 0.5 for index in range(260)])
    frames = {symbol: up for symbol in ("^GSPC", "^IXIC", "^DJI")}
    frames["TLT"] = tlt_down
    output = _run(MarketOverview(_Provider(frames)).overview())

    assert output["risk_tilt"]["tilt"] == "risk_on"
    assert output["risk_tilt"]["indices_up"] == 3


def test_risk_tilt_stays_mixed_without_credit_agreement():
    up = _uptrend_frame()
    frames = {symbol: up for symbol in ("^GSPC", "^IXIC", "^DJI")}
    frames["TLT"] = _uptrend_frame()  # bonds up too — no risk-on confirmation
    output = _run(MarketOverview(_Provider(frames)).overview())

    assert output["risk_tilt"]["tilt"] == "mixed"


def test_analyze_symbol_full_read():
    closes = [100 + index * 0.1 for index in range(260)]
    volumes = [1_000_000] * 259 + [2_000_000]
    frames = {"AAPL": _frame(closes, volumes)}
    read = _run(MarketOverview(_Provider(frames)).analyze_symbol("AAPL"))

    assert read["symbol"] == "AAPL"
    assert read["read"] in ("bullish", "bearish", "neutral")
    assert read["rsi_14"] > 0
    assert read["percent_off_52w_high"] <= 0
    assert read["volume_ratio"] == pytest.approx(2.0)
    assert read["sma_200"] is not None


def test_analyze_symbol_fails_closed_on_short_history():
    frames = {"BAD": _frame([100.0, 101.0, 102.0])}
    read = _run(MarketOverview(_Provider(frames)).analyze_symbol("BAD"))
    assert read is None


def test_analyze_symbols_drops_failures():
    good = _frame([100 + index * 0.1 for index in range(260)])
    frames = {"AAPL": good, "BAD": pd.DataFrame()}
    reads = _run(MarketOverview(_Provider(frames)).analyze_symbols(["AAPL", "BAD"]))
    assert set(reads) == {"AAPL"}


def _run(awaitable):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(awaitable)
