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
    monkeypatch.setattr(advisor.provider, "get_short_interest", AsyncMock(return_value=None))
    monkeypatch.setattr(advisor.provider, "get_earnings_dates", AsyncMock(return_value=[]))
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


def test_brain_consumes_desk_analytics_and_emits_new_signals():
    brain = AIBrain()
    prices = [100.0] * 60
    skew = {
        "expiry": "2026-09-18", "atm_iv": 0.30, "iv_call_25": 0.27, "iv_put_25": 0.40,
        "rr25": 0.13, "bf25": 0.02, "rr25_norm": 0.43, "bf25_norm": 0.07,
        "regime": "fear", "reasoning": "Put skew extreme — heavy hedging demand",
    }
    short_interest = {
        "short_percent_of_float": 32.0, "days_to_cover": 12.0, "shares_short": 1000,
    }
    earnings_move = {
        "implied_move_pct": 6.0, "median_historical_move_pct": 3.5,
        "edge_pct": 2.5, "events_used": 8, "read": "sell_iv", "signal": "premium_sell_edge",
    }

    output = brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=prices, high_prices=[101.0] * 60, low_prices=[99.0] * 60,
        current_iv=0.30, hv_20=0.18, vix=20.0,
        iv_skew=skew, short_interest=short_interest, earnings_move=earnings_move,
    )

    sources = {signal["source"] for signal in output.all_signals}
    assert "skew" in sources
    assert "short_interest" in sources
    assert output.iv_signal["iv_skew"] == skew
    assert output.iv_signal["short_interest"] == short_interest
    assert output.iv_signal["earnings_move"] == earnings_move
    # Rich earnings IV attached to a premium-selling edge shows up in reasoning.
    iv = next(signal for signal in output.all_signals if signal["source"] == "iv")
    assert "sell the move" in iv["reasoning"]


def test_brain_missing_desk_analytics_stays_neutral():
    brain = AIBrain()
    prices = [100.0] * 60

    output = brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=prices, high_prices=[101.0] * 60, low_prices=[99.0] * 60,
        current_iv=0.30, hv_20=0.18, vix=20.0,
    )

    assert output.iv_signal.get("iv_skew") is None
    assert output.iv_signal.get("short_interest") is None
    assert output.iv_signal.get("earnings_move") is None
    sources = {signal["source"] for signal in output.all_signals}
    assert "skew" not in sources
    assert "short_interest" not in sources


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
