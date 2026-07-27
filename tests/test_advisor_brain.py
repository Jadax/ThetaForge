"""Integration tests for the Brain's normalized advisor data path."""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from orchestrator.routes import advisor
from agents.trade_engine.ai_brain import AIBrain, SignalStrength, TimeHorizon


class _Tracker:
    def record_prediction(self, **kwargs):
        self.prediction = kwargs

    def get_performance_summary(self):
        return {"by_source": {}, "dynamic_weights": {}}


@pytest.mark.asyncio
async def test_brain_analyze_uses_async_provider_and_all_available_signals(monkeypatch):
    prices = [100 + index * 0.1 for index in range(40)]
    history = pd.DataFrame({
        "Close": prices,
        "High": [price + 1 for price in prices],
        "Low": [price - 1 for price in prices],
    })
    chain = [
        {
            "symbol": "TESTC", "strike": 100, "expiry": "2026-08-15",
            "option_type": "CALL", "bid": 2.0, "ask": 2.2, "last": 2.1,
            "volume": 5_000, "open_interest": 1_000, "implied_volatility": 0.35,
        },
        {
            "symbol": "TESTP", "strike": 100, "expiry": "2026-08-15",
            "option_type": "PUT", "bid": 2.0, "ask": 2.2, "last": 2.1,
            "volume": 100, "open_interest": 1_000, "implied_volatility": 0.35,
        },
    ]
    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMock(return_value=103.9))
    monkeypatch.setattr(advisor.provider, "get_option_chain", AsyncMock(return_value=chain))
    monkeypatch.setattr(advisor.provider, "get_historical_prices", AsyncMock(return_value=history))
    monkeypatch.setattr(advisor.provider, "get_vix", AsyncMock(return_value=24.0))
    monkeypatch.setattr(advisor.provider, "get_put_call_ratio", AsyncMock(return_value=1.4))
    monkeypatch.setattr(advisor, "SignalTracker", _Tracker)

    response = await advisor.brain_analyze(advisor.BrainAnalysisRequest(symbol="test"))

    assert response["symbol"] == "TEST"
    assert response["stock_price"] == 103.9
    # VIX is a volatility input, not a directional bearish signal.
    assert response["regime"] == "high_vol"
    assert {signal["source"] for signal in response["all_signals"]} >= {
        "cpr", "iv", "technical", "sideways", "sentiment", "flow", "gex",
    }
    assert response["cpr_signal"]
    assert response["recommendations_1m"]


@pytest.mark.asyncio
async def test_brain_rejects_missing_market_price(monkeypatch):
    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMock(return_value=None))
    monkeypatch.setattr(advisor.provider, "get_option_chain", AsyncMock(return_value=[]))
    monkeypatch.setattr(advisor.provider, "get_historical_prices", AsyncMock(return_value=pd.DataFrame()))
    monkeypatch.setattr(advisor.provider, "get_vix", AsyncMock(return_value=None))
    monkeypatch.setattr(advisor.provider, "get_put_call_ratio", AsyncMock(return_value=None))

    with pytest.raises(advisor.HTTPException) as error:
        await advisor.brain_analyze(advisor.BrainAnalysisRequest(symbol="MISSING"))

    assert error.value.status_code == 502


def test_brain_rejects_unsafe_or_event_driven_default_strategies():
    brain = AIBrain()
    base = dict(
        signal=SignalStrength.STRONG_BUY,
        regime="high_vol",
        iv_signal={"iv_rank": 80},
        sideways={"is_sideways": True},
        symbol="TEST",
        existing_positions=[],
        confidence=80,
    )

    earnings = brain._select_best_strategy(days_to_earnings=6, vix=20, **base)
    extreme_vix = brain._select_best_strategy(days_to_earnings=None, vix=31, **base)

    assert earnings["strategy"] == "avoid_new_positions"
    assert extreme_vix["strategy"] == "no_trade"
    assert brain.HORIZON_STRATEGIES[TimeHorizon.SWING_1W] == []
    assert "short_strangle" not in brain.HORIZON_STRATEGIES[TimeHorizon.MONTHLY_1M]
