"""Advisor equity endpoints: recommendation payload, gating, and management."""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from orchestrator.routes import advisor


class _FakeScanner:
    def __init__(self, payload):
        self._payload = payload

    async def _analyze_one(self, symbol):
        return dict(self._payload), None


def _buy_payload(symbol="TEST"):
    return {
        "symbol": symbol,
        "signal": "buy",
        "score": 82.0,
        "strategy": "equity_momentum",
        "reasoning": "Momentum-trend long: above 200d; ADX 27.",
        "no_trade_reason": None,
        "price": 100.0,
        "rsi_14": 62.0,
        "adx": 27.0,
        "above_200d": True,
        "atr_value": 1.0,
        "is_etf": False,
    }


@pytest.mark.asyncio
async def test_equity_recommend_returns_full_ungated_payload(monkeypatch):
    scanner = _FakeScanner(_buy_payload())
    monkeypatch.setattr(advisor, "get_background_equity_scanner", AsyncMock(return_value=scanner))
    request = advisor.EquityRecommendRequest(symbol="TEST", capital=10_000, current_positions=[])

    response = await advisor.equity_recommend(request)

    assert response["reason"] == "ok"
    assert len(response["recommendations"]) == 1
    rec = response["recommendation"]
    assert rec["gate"] is None
    assert rec["symbol"] == "TEST"
    assert rec["shares"] > 0
    assert rec["stop_price"] < rec["entry_price"] < rec["target_price"]
    assert rec["score"] == 82.0
    assert rec["rsi_14"] == 62.0


@pytest.mark.asyncio
async def test_equity_recommend_returns_gate_when_brain_vetoes(monkeypatch):
    payload = _buy_payload()
    payload["signal"] = "no_trade"
    payload["no_trade_reason"] = "market_risk_off"
    scanner = _FakeScanner(payload)
    monkeypatch.setattr(advisor, "get_background_equity_scanner", AsyncMock(return_value=scanner))
    request = advisor.EquityRecommendRequest(symbol="TEST", capital=10_000, current_positions=[])

    response = await advisor.equity_recommend(request)

    assert response["recommendations"] == []
    assert response["recommendation"]["gate"] == "not_actionable"


@pytest.mark.asyncio
async def test_equity_recommend_fails_closed_on_missing_data(monkeypatch):
    scanner = _FakeScanner(None)
    scanner._analyze_one = AsyncMock(return_value=(None, "price_unavailable"))
    monkeypatch.setattr(advisor, "get_background_equity_scanner", AsyncMock(return_value=scanner))
    request = advisor.EquityRecommendRequest(symbol="TEST", capital=10_000, current_positions=[])

    response = await advisor.equity_recommend(request)

    assert response["recommendation"] is None
    assert response["reason"] == "price_unavailable"


@pytest.mark.asyncio
async def test_equity_management_stop_hit_closes(monkeypatch):
    history = pd.DataFrame({
        "Close": [100 + index * 0.1 for index in range(130)],
        "High": [101 + index * 0.1 for index in range(130)],
        "Low": [99 + index * 0.1 for index in range(130)],
    })
    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMock(return_value=97.5))
    monkeypatch.setattr(advisor.provider, "get_historical_prices", AsyncMock(return_value=history))
    monkeypatch.setattr(advisor.provider, "get_next_earnings_date", AsyncMock(return_value=None))
    monkeypatch.setattr(advisor, "macro_days_until", lambda: 20)

    request = advisor.EquityManagementRequest(
        capital=5_000,
        positions=[
            advisor.EquityPositionInput(
                symbol="TEST", entry_price=100.0, stop_price=98.0,
                target_price=106.0, highest_high=102.0,
                risk_per_share=2.0, shares=10,
            )
        ],
    )
    response = await advisor.equity_positions_management(request)

    assert response["actions"][0]["action"] == "close_stop"
    assert response["actions"][0]["symbol"] == "TEST"
    assert response["actions"][0]["shares"] == 10


@pytest.mark.asyncio
async def test_equity_management_holds_when_above_stop(monkeypatch):
    history = pd.DataFrame({
        "Close": [100 + index * 0.1 for index in range(130)],
        "High": [101 + index * 0.1 for index in range(130)],
        "Low": [99 + index * 0.1 for index in range(130)],
    })
    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMock(return_value=103.0))
    monkeypatch.setattr(advisor.provider, "get_historical_prices", AsyncMock(return_value=history))
    monkeypatch.setattr(advisor.provider, "get_next_earnings_date", AsyncMock(return_value=None))
    monkeypatch.setattr(advisor, "macro_days_until", lambda: 20)

    request = advisor.EquityManagementRequest(
        capital=5_000,
        positions=[
            advisor.EquityPositionInput(
                symbol="TEST", entry_price=100.0, stop_price=98.0,
                target_price=106.0, highest_high=103.0,
                risk_per_share=2.0, shares=10,
            )
        ],
    )
    response = await advisor.equity_positions_management(request)

    assert response["actions"][0]["action"] == "hold"
