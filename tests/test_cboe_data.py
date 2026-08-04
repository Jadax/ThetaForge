"""Tests for the free CBOE delayed-quotes provider."""
import pytest
from unittest.mock import AsyncMock, patch

from agents.data_ingestion.cboe_data import CBOEDataProvider

CHAIN_PAYLOAD = {
    "data": {
        "options": [
            {
                "option": "AAPL 2026-08-15 C 200",
                "strike": 200,
                "expiry": "2099-12-31",
                "type": "call",
                "bid": 5.0,
                "ask": 5.2,
                "last_trade_price": 5.1,
                "volume": 1200,
                "open_interest": 800,
                "iv": 0.31,
                "delta": 0.48,
                "gamma": 0.02,
                "theta": -0.15,
                "vega": 0.55,
                "rho": 0.01,
            },
            {
                "option": "AAPL 2026-08-15 P 200",
                "strike": 200,
                "expiry": "2099-12-31",
                "type": "put",
                "bid": 5.3,
                "ask": 5.5,
                "last_trade_price": 5.4,
                "volume": 300,
                "open_interest": 900,
                "iv": 0.30,
                "delta": -0.52,
                "gamma": 0.02,
                "theta": -0.12,
                "vega": 0.50,
                "rho": -0.01,
            },
            {"strike": 0, "expiry": "", "type": "call"},  # malformed, dropped
        ]
    }
}


@pytest.mark.asyncio
async def test_option_chain_normalization():
    provider = CBOEDataProvider(min_request_interval=0)
    with patch.object(provider, "_get_json", new=AsyncMock(return_value=CHAIN_PAYLOAD)):
        chain = await provider.get_option_chain("aapl")

    assert len(chain) == 2
    call = chain[0]
    assert call["symbol"] == "AAPL"
    assert call["option_type"] == "CALL"
    assert call["strike"] == 200.0
    assert call["implied_volatility"] == pytest.approx(0.31)
    assert call["delta"] == pytest.approx(0.48)
    assert call["bid"] == 5.0
    assert call["volume"] == 1200
    assert call["open_interest"] == 800


@pytest.mark.asyncio
async def test_option_chain_empty_on_malformed_payload():
    provider = CBOEDataProvider(min_request_interval=0)
    with patch.object(provider, "_get_json", new=AsyncMock(return_value=None)):
        assert await provider.get_option_chain("AAPL") == []


@pytest.mark.asyncio
async def test_quote_normalization():
    provider = CBOEDataProvider(min_request_interval=0)
    payload = {"data": {"symbol": "AAPL", "current_price": 201.5, "bid": 201.4,
                        "ask": 201.6, "volume": 12345, "percent_change": 1.2}}
    with patch.object(provider, "_get_json", new=AsyncMock(return_value=payload)):
        quote = await provider.get_quote("aapl")

    assert quote["price"] == pytest.approx(201.5)
    assert quote["symbol"] == "AAPL"
    assert quote["change_pct"] == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_vix_term_structure_uses_latest_close():
    provider = CBOEDataProvider(min_request_interval=0)
    payload = {"data": [
        {"date": "2026-07-30", "close": 12.1},
        {"date": "2026-08-01", "close": 12.4},
        {"date": "2026-07-31", "close": 12.2},
    ]}
    with patch.object(provider, "_get_json", new=AsyncMock(return_value=payload)):
        structure = await provider.get_vix_term_structure()

    assert set(structure) == {"VIX9D", "VIX3M", "VIX6M", "VIX1Y"}
    assert all(value == pytest.approx(12.4) for value in structure.values())


@pytest.mark.asyncio
async def test_vix_term_structure_skips_missing_index():
    provider = CBOEDataProvider(min_request_interval=0)
    with patch.object(provider, "_get_json", new=AsyncMock(return_value={"data": []})):
        assert await provider.get_vix_term_structure() == {}


def test_finite_number_defensive_parse():
    from agents.data_ingestion.cboe_data import _finite_number
    assert _finite_number(None) == 0.0
    assert _finite_number(float("nan")) == 0.0
    assert _finite_number("12.5") == 12.5
    assert _finite_number("nonsense") == 0.0
