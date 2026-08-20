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
