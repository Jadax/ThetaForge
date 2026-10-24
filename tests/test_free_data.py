import pytest

from agents.data_ingestion.free_data import FreeDataProvider


@pytest.mark.asyncio
async def test_ibkr_history_matches_yfinance_ohlcv_contract(monkeypatch):
    provider = FreeDataProvider()
    bars = [
        {
            "date": "2026-08-19",
            "open": 99.0,
            "high": 101.0,
            "low": 98.5,
            "close": 100.0,
            "volume": 123456,
        },
        {
            "date": "2026-08-20",
            "open": 100.0,
            "high": 102.0,
            "low": 99.5,
            "close": 101.0,
            "volume": 234567,
        },
    ]

    async def proxy_history(_symbol, period="1y"):
        return bars

    monkeypatch.setattr(provider, "_get_ibkr_proxy_stock_history", proxy_history)
    frame = await provider.get_historical_prices("AAPL")

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame["Close"].tolist() == [100.0, 101.0]


@pytest.mark.asyncio
async def test_option_chain_merges_broker_quotes_with_free_oi(monkeypatch):
    provider = FreeDataProvider()

    broker_chain = [
        {"symbol": "AAPL", "strike": 350.0, "expiry": "2026-10-23", "dte": 3,
         "option_type": "PUT", "bid": 1.10, "ask": 1.20, "last": 1.15,
         "volume": 900, "open_interest": 0, "implied_volatility": 0.31,
         "delta": -0.35, "gamma": None, "theta": None, "vega": None},
    ]
    free_chain = [
        {"symbol": "AAPL", "strike": 350.0, "expiry": "2026-10-23", "dte": 3,
         "option_type": "PUT", "bid": 0.0, "ask": 0.0, "last": 0.0,
         "volume": 0, "open_interest": 4500, "implied_volatility": 0.30,
         "delta": -0.34, "gamma": 0.01, "theta": -0.02, "vega": 0.08},
        {"symbol": "AAPL", "strike": 345.0, "expiry": "2026-10-23", "dte": 3,
         "option_type": "PUT", "bid": 0.5, "ask": 0.6, "last": 0.55,
         "volume": 10, "open_interest": 1200, "implied_volatility": 0.29,
         "delta": -0.25, "gamma": 0.01, "theta": -0.02, "vega": 0.08},
    ]

    async def fake_broker(symbol):
        return broker_chain

    async def fake_cboe(symbol):
        return free_chain

    monkeypatch.setattr(provider, "_get_ibkr_proxy_chain", fake_broker)
    monkeypatch.setattr(provider.cboe, "get_option_chain", fake_cboe)

    merged = await provider.get_option_chain("AAPL")
    by_key = {(r["expiry"], r["strike"], r["option_type"]): r for r in merged}
    assert len(merged) == 2
    row = by_key[("2026-10-23", 350.0, "PUT")]
    assert row["bid"] == 1.10 and row["ask"] == 1.20
    assert row["open_interest"] == 4500
    assert row["implied_volatility"] == 0.31
    assert row["gamma"] == 0.01
    assert row["volume"] == 900
    assert by_key[("2026-10-23", 345.0, "PUT")]["bid"] == 0.5


@pytest.mark.asyncio
async def test_option_chain_falls_back_when_one_source_fails(monkeypatch):
    provider = FreeDataProvider()

    async def broken_broker(symbol):
        return None

    free_chain = [
        {"symbol": "AAPL", "strike": 345.0, "expiry": "2026-10-23", "dte": 3,
         "option_type": "PUT", "bid": 0.5, "ask": 0.6, "last": 0.55,
         "volume": 10, "open_interest": 1200, "implied_volatility": 0.29,
         "delta": -0.25, "gamma": None, "theta": None, "vega": None},
    ]

    async def fake_cboe(symbol):
        return free_chain

    monkeypatch.setattr(provider, "_get_ibkr_proxy_chain", broken_broker)
    monkeypatch.setattr(provider.cboe, "get_option_chain", fake_cboe)

    merged = await provider.get_option_chain("AAPL")
    assert merged == free_chain


def test_memo_persist_never_leaks_temp_files(tmp_path, monkeypatch):
    """53 orphaned provider_daily_memo.json.*.tmp files piled up on the
    Advisor VM: a failed dump left its temp behind, and killed workers'
    temps were never swept."""
    import os
    import time
    from datetime import date
    from agents.data_ingestion.free_data import FreeDataProvider

    memo = tmp_path / "provider_daily_memo.json"
    monkeypatch.setattr(FreeDataProvider, "MEMO_FILE", str(memo))
    provider = FreeDataProvider()
    provider._daily_memo = {"bad": (date.today(), object())}   # not JSON-serializable
    provider._persist_memo()                                    # must not raise
    assert list(tmp_path.glob("*.tmp")) == []

    stale = tmp_path / "provider_daily_memo.json.12345.tmp"
    fresh = tmp_path / "provider_daily_memo.json.67890.tmp"
    stale.write_text("{}"); fresh.write_text("{}")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    FreeDataProvider()._load_memo()
    assert not stale.exists() and fresh.exists()   # only dead workers' temps go
