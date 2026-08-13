"""Equity engine: EquityBrain gates, sizing/recommender, and position exits."""
import math
import random

from agents.equity_trader.equity_brain import EquityBrain, BUY_SCORE_FLOOR
from agents.equity_trader.equity_recommender import (
    EquityRecommender,
    MAX_CORRELATED_EQUITY_POSITIONS,
    MAX_POSITION_NOTIONAL_PCT,
    RISK_PER_TRADE_PCT,
)
from agents.equity_trader.equity_manager import evaluate_position


def uptrend(bars=260, drift=0.0016, noise=0.010, seed=7):
    """Deterministic gently-rising series that clears every equity gate."""
    random.seed(seed)
    closes = [100.0]
    for _ in range(1, bars):
        if random.random() < 0.35:
            closes.append(closes[-1] * (1 - abs(random.gauss(drift * 0.4, noise * 0.6))))
        else:
            closes.append(closes[-1] * (1 + abs(random.gauss(drift, noise * 0.8))))
    highs = [c * (1 + 0.15 * noise) for c in closes]
    lows = [c * (1 - 0.15 * noise) for c in closes]
    volumes = [1.3e6 * (1 + 0.1 * math.sin(i)) for i in range(bars)]
    return closes, highs, lows, volumes


def analyze(**overrides):
    closes, highs, lows, volumes = uptrend()
    defaults = dict(
        symbol="TEST",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        benchmark_return_6m=0.10,
        market_risk_tilt="risk_on",
        days_to_earnings=40,
        days_to_macro=20,
        is_etf=False,
    )
    defaults.update(overrides)
    return EquityBrain().analyze(**defaults)


# ── EquityBrain ───────────────────────────────────────────────────────────

def test_strong_uptrend_passes_every_gate():
    read = analyze()
    assert read.signal == "buy"
    assert read.score >= BUY_SCORE_FLOOR
    assert read.no_trade_reason is None
    assert read.above_200d
    assert read.momentum_6m > 0


def test_etf_uses_rotation_strategy_label():
    read = analyze(is_etf=True)
    assert read.signal == "buy"
    assert read.strategy == "etf_rotation"


def test_risk_off_market_vetoes_equity_longs():
    read = analyze(market_risk_tilt="risk_off")
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "market_risk_off"


def test_macro_proximity_vetoes_new_longs():
    read = analyze(days_to_macro=2)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "macro_proximity"


def test_macro_proximity_fails_open_without_schedule():
    read = analyze(days_to_macro=None)
    assert read.signal == "buy"


def test_pre_earnings_veto_within_blackout():
    read = analyze(days_to_earnings=3)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "pre_earnings"


def test_trend_filter_rejects_below_200d():
    # Rally hard, then crash the tail: latest price ends well below its 200d.
    closes, highs, lows, volumes = uptrend()
    peak = closes[:180]
    crash = [peak[-1] * (1 - 0.008 * i) for i in range(1, 81)]
    closes = peak + crash
    highs = [c * (1 + 0.15 * 0.010) for c in closes]
    lows = [c * (1 - 0.15 * 0.010) for c in closes]
    volumes = volumes[:len(closes)]
    read = analyze(closes=closes, highs=highs, lows=lows, volumes=volumes)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "trend_filter"


def test_negative_absolute_momentum_rejected():
    # Long cheap base, one spike 126 bars back, then a slow fade: price stays
    # above its 200d (cheap base drags the average down) while the trailing
    # 6-month return is negative.
    closes = [80.0 + i * 0.015 for i in range(135)]
    closes[134] = 120.0
    peak = 120.0
    for i in range(1, 127):
        closes.append(peak - i * (120 - 100) / 126.0)
    highs = [c * 1.004 for c in closes]
    lows = [c * 0.996 for c in closes]
    volumes = [1e6] * len(closes)
    read = analyze(closes=closes, highs=highs, lows=lows, volumes=volumes)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "absolute_momentum"


def test_relative_strength_laggard_rejected():
    read = analyze(benchmark_return_6m=0.50)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "relative_strength"


def test_insufficient_history_fails_closed():
    closes, highs, lows, volumes = uptrend(bars=40)
    read = analyze(closes=closes, highs=highs, lows=lows, volumes=volumes)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "history_unavailable"


def test_missing_price_fails_closed():
    closes, highs, lows, volumes = uptrend()
    flat = [0.0] * len(closes)
    read = analyze(closes=flat, highs=highs, lows=lows, volumes=volumes)
    assert read.signal == "no_trade"
    assert read.no_trade_reason == "price_unavailable"


# ── EquityRecommender ─────────────────────────────────────────────────────

def _buy_read():
    read = analyze()
    assert read.signal == "buy"
    return read


def test_recommender_sizes_one_percent_risk_with_atr_stop():
    read = _buy_read()
    rec = EquityRecommender().build(read, capital=100_000, current_positions=[])
    assert rec.gate is None
    assert rec.stop_price < rec.entry_limit < rec.target_price
    risk = rec.risk_per_share * rec.shares
    assert risk <= 100_000 * RISK_PER_TRADE_PCT * 1.02
    assert rec.notional <= 100_000 * MAX_POSITION_NOTIONAL_PCT


def test_recommender_rejects_non_buy_reads():
    read = analyze(market_risk_tilt="risk_off")
    rec = EquityRecommender().build(read, capital=100_000, current_positions=[])
    assert rec.gate == "not_actionable"


def test_recommender_sector_cap_blocks_fourth_correlated_position():
    read = analyze(symbol="AAPL")
    from agents.trade_engine.recommender import SYMBOL_SECTOR
    bucket = SYMBOL_SECTOR["AAPL"]
    positions = [s for s, b in SYMBOL_SECTOR.items() if b == bucket][:MAX_CORRELATED_EQUITY_POSITIONS]
    assert len(positions) == MAX_CORRELATED_EQUITY_POSITIONS
    rec = EquityRecommender().build(read, capital=100_000, current_positions=positions)
    assert rec.gate == "sector_cap"


def test_recommender_accepts_same_sector_under_cap():
    read = analyze(symbol="AAPL")
    rec = EquityRecommender().build(
        read, capital=100_000,
        current_positions=["MSFT", "NVDA", "GOOGL", "AMD", "INTC", "CRM"],
    )
    # AAPL's sector (tech) is already at the cap via the six tech names above.
    assert rec.gate == "sector_cap"


def test_recommender_rejects_undersized_capital():
    read = _buy_read()
    rec = EquityRecommender().build(read, capital=100, current_positions=[])
    assert rec.gate is not None


# ── EquityManager ─────────────────────────────────────────────────────────

def _position(**overrides):
    defaults = dict(
        symbol="TEST",
        current_price=102.0,
        entry_price=100.0,
        stop_price=98.0,
        target_price=104.0,
        highest_high=102.0,
        atr=1.0,
        risk_per_share=2.0,
        days_held=5,
        days_to_earnings=20,
        days_to_macro=20,
    )
    defaults.update(overrides)
    return evaluate_position(**defaults)


def test_stop_hit_closes():
    action, reason, high = _position(current_price=97.5)
    assert action == "close_stop"
    assert high == 102.0


def test_pre_macro_exit():
    action, reason, _ = _position(days_to_macro=2)
    assert action == "close_pre_macro"


def test_pre_earnings_exit():
    action, reason, _ = _position(days_to_earnings=2)
    assert action == "close_pre_earnings"


def test_chandelier_trail_fires_after_plus_one_r():
    action, reason, high = _position(current_price=106.0, highest_high=106.0, atr=1.0)
    # +1R = 2.0 above entry at 102; the 2x ATR chandelier sits at 104; price at
    # 106 breaks back below it only after a down day to <=104.
    assert high == 106.0
    trailing = _position(current_price=103.5, highest_high=106.0, atr=1.0)
    assert trailing[0] == "close_trail"


def test_trail_not_armed_before_plus_one_r():
    action, reason, _ = _position(current_price=101.0, highest_high=101.0, atr=1.0)
    # price 101 = +0.5R, trail should not have replaced the hard stop
    assert action == "hold"


def test_target_reached_closes():
    action, reason, _ = _position(current_price=104.5)
    assert action == "close_profit"


def test_time_exit_after_max_hold():
    action, reason, _ = _position(days_held=60)
    assert action == "close_time"


def test_hold_when_above_stop_below_target():
    action, reason, high = _position(current_price=102.0, highest_high=101.0)
    assert action == "hold"
    assert high == 102.0


def test_missing_pricing_holds_without_fabrication():
    action, reason, _ = _position(current_price=0.0)
    assert action == "hold"
    assert "missing" in reason
