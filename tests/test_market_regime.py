"""Market direction filter (agents/trade_engine/market_regime.py)."""
import asyncio

from agents.equity_trader.equity_brain import EquityBrain
from agents.trade_engine import market_regime as mr
from agents.trade_engine import recommender as rec_module


def _falling(n=80, start=765.0, step=-1.0):
    return [start + i * step for i in range(n)]


def test_spy_below_its_50_day_is_a_downtrend():
    read = mr.market_trend(_falling())
    assert read["state"] == "downtrend" and read["pct_vs_sma50"] < 0
    assert mr.market_trend(list(reversed(_falling())))["state"] == "uptrend"


def test_too_little_history_gives_no_read():
    assert mr.market_trend(_falling(n=30)) is None


def test_downtrend_blocks_bullish_structures_only():
    for bullish in ("cash_secured_put", "csp", "bull_put_credit", "call_debit_spread"):
        assert mr.blocks(bullish, "downtrend")
    for allowed in ("bear_call_credit", "iron_condor", "covered_call", "put_debit_spread"):
        assert not mr.blocks(allowed, "downtrend")
    assert not mr.blocks("cash_secured_put", "uptrend")
    assert not mr.blocks("cash_secured_put", None)          # no read never vetoes


def test_unreadable_spy_fails_open():
    class Broken:
        async def get_historical_prices(self, *a, **k):
            raise RuntimeError("feed down")

    mr._CACHE.update(at=0.0, trend=None)
    assert asyncio.run(mr.get_market_trend(Broken())) is None
    mr._CACHE.update(at=0.0, trend=None)


def _strong_long_closes():
    # A clean uptrend: above 50d and 200d, momentum, breakout.
    return [50 + i * 0.2 for i in range(260)]


def test_equity_brain_buys_nothing_in_a_market_downtrend():
    brain = EquityBrain()
    closes = _strong_long_closes()
    up = brain.analyze("XYZ", closes, benchmark_return_6m=0.0, market_trend="uptrend")
    down = brain.analyze("XYZ", closes, benchmark_return_6m=0.0, market_trend="downtrend")
    assert down.signal == "no_trade" and down.no_trade_reason == "market_downtrend"
    assert up.no_trade_reason != "market_downtrend"


def test_recommender_never_returns_a_bullish_structure_in_a_downtrend():
    from tests.test_trade_engine import _wheel_chain
    from agents.trade_engine.models import AccountInfo, RiskTolerance, StrategyType

    def run(trend):
        return rec_module.TradeRecommender().generate_recommendations(
            account=AccountInfo(total_equity=5000, buying_power=5000, cash_available=5000,
                                risk_tolerance=RiskTolerance.MODERATE, max_positions=3),
            market_data={"vix": 19, "XYZ_price": 14.0}, option_chains={"XYZ": _wheel_chain(14.0)},
            technical_data={"XYZ": {"trend": "neutral"}}, flow_data={},
            volatility_data={"XYZ": {"iv": 0.50, "hv_20": 0.35, "iv_rank": 70, "dte": 45}},
            diversify_underlyings=False, market_trend=trend,
        )

    assert any(r.strategy_type == StrategyType.CASH_SECURED_PUT for r in run("uptrend").recommendations)
    down = run("downtrend")
    assert not any(r.strategy_type == StrategyType.CASH_SECURED_PUT for r in down.recommendations)
    assert any("50-day" in reason for reason in str(down.rejections).split("'"))


def test_debit_spreads_are_not_bought():
    assert rec_module.BUY_DEBIT_SPREADS is False
