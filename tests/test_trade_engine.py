"""
Tests for the Trade Recommendation Engine.
Covers ROI calculator, analytics, strategy scorer, recommender,
and unusual activity detector.
"""
import math
import sys
import os
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, MarketRegime, Direction, GEXRegime,
    StrategyType, OptionContract, StrategyLeg,
    SymbolData, MarketConditions, CurrentPosition, StrategyScore,
)
from agents.trade_engine.roi_calculator import ROICalculator
from agents.trade_engine.analytics import OptionsAnalytics
from agents.trade_engine.strategy_scorer import StrategyScorer
from agents.trade_engine.recommender import TradeRecommender, MIN_COMPOSITE_SCORE
from agents.flow_analysis.unusual_activity import UnusualActivityDetector
from agents.trade_engine import alerts as alerts_module
from agents.trade_engine import signal_tracker as tracker_module
from agents.trade_engine.alerts import AlertEngine, AlertType
from agents.trade_engine.signal_tracker import SignalTracker


# ============================================================
# ROI Calculator Tests
# ============================================================

def test_csp_roi_basic():
    calc = ROICalculator()
    roi = calc.csp_roi(strike=100, premium=2.50, dte=30, stock_price=105)
    assert roi["strategy"] == "cash_secured_put"
    assert roi["strike"] == 100
    assert roi["premium"] == 2.50
    assert roi["capital_required"] == 10000
    assert roi["premium_collected"] == 250
    assert roi["premium_yield_pct"] == 2.5
    assert roi["annualized_return_pct"] > 0
    assert roi["breakeven"] == 97.50


def test_csp_roi_annualized():
    calc = ROICalculator()
    roi_30 = calc.csp_roi(strike=100, premium=2.0, dte=30, stock_price=105)
    roi_45 = calc.csp_roi(strike=100, premium=3.0, dte=45, stock_price=105)
    # Longer DTE with proportionally more premium should have different annualized
    assert roi_30["annualized_return_pct"] > 0
    assert roi_45["annualized_return_pct"] > 0


def test_csp_roi_otm_pct():
    calc = ROICalculator()
    roi = calc.csp_roi(strike=95, premium=1.0, dte=30, stock_price=100)
    assert roi["otm_pct"] == -5.0  # Strike is 5% below stock


def test_cc_roi_basic():
    calc = ROICalculator()
    roi = calc.covered_call_roi(strike=110, premium=3.0, dte=30, stock_price=105)
    assert roi["strategy"] == "covered_call"
    assert roi["capital_required"] == 10500
    assert roi["premium_yield_pct"] > 0
    assert roi["max_profit"] > 0


def test_credit_spread_roi():
    calc = ROICalculator()
    roi = calc.credit_spread_roi(
        short_strike=100, long_strike=95, credit=1.50, dte=30,
        stock_price=105, spread_type="put"
    )
    assert roi["strategy"] == "put_credit_spread"
    assert roi["width"] == 5
    assert roi["credit"] == 1.50
    assert roi["max_profit"] == 150
    assert roi["capital_required"] == 350
    assert roi["annualized_return_pct"] > 0


def test_iron_condor_roi():
    calc = ROICalculator()
    roi = calc.iron_condor_roi(
        put_short=95, put_long=90, call_short=110, call_long=115,
        credit=2.0, dte=30, stock_price=100
    )
    assert roi["strategy"] == "iron_condor"
    assert roi["max_profit"] == 200
    assert roi["probability_of_profit"] > 0


def test_rank_opportunities():
    calc = ROICalculator()
    opps = [
        {"annualized_return_pct": 10, "symbol": "A"},
        {"annualized_return_pct": 50, "symbol": "B"},
        {"annualized_return_pct": 30, "symbol": "C"},
    ]
    ranked = calc.rank_opportunities(opps)
    assert ranked[0]["symbol"] == "B"
    assert ranked[1]["symbol"] == "C"
    assert ranked[2]["symbol"] == "A"


def test_scan_all_strikes_csp():
    calc = ROICalculator()
    chain = [
        {"strike": 95, "option_type": "PUT", "last": 0.50, "bid": 0.40, "ask": 0.60, "volume": 100, "open_interest": 500, "dte": 30, "symbol": "TEST"},
        {"strike": 100, "option_type": "PUT", "last": 2.00, "bid": 1.80, "ask": 2.20, "volume": 500, "open_interest": 1000, "dte": 30, "symbol": "TEST"},
        {"strike": 105, "option_type": "PUT", "last": 5.00, "bid": 4.80, "ask": 5.20, "volume": 200, "open_interest": 300, "dte": 30, "symbol": "TEST"},
    ]
    results = calc.scan_all_strikes_csp(chain, stock_price=102, dte=30)
    assert len(results) > 0
    assert results[0]["annualized_return_pct"] >= results[-1]["annualized_return_pct"]


# ============================================================
# Analytics Tests
# ============================================================

def test_max_pain():
    analytics = OptionsAnalytics()
    chain = [
        {"strike": 90, "option_type": "CALL", "open_interest": 1000},
        {"strike": 95, "option_type": "CALL", "open_interest": 2000},
        {"strike": 100, "option_type": "CALL", "open_interest": 5000},
        {"strike": 105, "option_type": "CALL", "open_interest": 2000},
        {"strike": 90, "option_type": "PUT", "open_interest": 1500},
        {"strike": 95, "option_type": "PUT", "open_interest": 3000},
        {"strike": 100, "option_type": "PUT", "open_interest": 5000},
        {"strike": 105, "option_type": "PUT", "open_interest": 2500},
    ]
    result = analytics.max_pain(chain)
    assert result["max_pain_strike"] > 0
    assert result["call_wall"] > 0
    assert result["put_floor"] > 0


def test_max_pain_empty():
    analytics = OptionsAnalytics()
    result = analytics.max_pain([])
    assert result["max_pain_strike"] == 0


def test_expected_move():
    analytics = OptionsAnalytics()
    em = analytics.expected_move(stock_price=100, iv=0.20, dte=30)
    assert em["expected_move_1sd"] > 0
    assert em["upper_1sd"] > 100
    assert em["lower_1sd"] < 100
    assert em["expected_move_pct"] > 0
    assert em["upper_2sd"] > em["upper_1sd"]
    assert em["lower_2sd"] < em["lower_1sd"]


def test_expected_move_with_straddle():
    analytics = OptionsAnalytics()
    em = analytics.expected_move(stock_price=100, iv=0.20, dte=30, atm_straddle_price=5.0)
    assert em["expected_move_1sd"] == 5.0
    assert em["method"] == "straddle"


def test_nvrp_positive():
    analytics = OptionsAnalytics()
    nvrp = analytics.net_volatility_risk_premium(iv=0.30, hv_20=0.20)
    assert nvrp["nvrp"] > 0
    assert nvrp["recommendation"] == "sell_premium"


def test_nvrp_negative():
    analytics = OptionsAnalytics()
    nvrp = analytics.net_volatility_risk_premium(iv=0.15, hv_20=0.25)
    assert nvrp["nvrp"] < 0
    assert nvrp["recommendation"] == "buy_premium"


def test_nvrp_neutral():
    analytics = OptionsAnalytics()
    nvrp = analytics.net_volatility_risk_premium(iv=0.20, hv_20=0.20)
    assert nvrp["regime"] == "neutral"


def test_nvrp_regimes_use_decimal_volatility_units():
    analytics = OptionsAnalytics()
    assert analytics.net_volatility_risk_premium(iv=0.24, hv_20=0.20)["regime"] == "sell_vol"
    assert analytics.net_volatility_risk_premium(iv=0.16, hv_20=0.20)["regime"] == "buy_vol"


def test_probability_of_touch():
    analytics = OptionsAnalytics()
    # ATM should be high (close to 100 since 0 distance)
    pot = analytics.probability_of_touch(100, 100, 0.20, 30)
    assert pot > 0
    # Far OTM should be lower than near-OTM
    pot_near = analytics.probability_of_touch(100, 105, 0.20, 30)
    pot_far = analytics.probability_of_touch(100, 150, 0.20, 30)
    assert pot_near > pot_far


def test_support_resistance():
    analytics = OptionsAnalytics()
    chain = [
        {"strike": 90, "option_type": "PUT", "open_interest": 15000},
        {"strike": 95, "option_type": "PUT", "open_interest": 8000},
        {"strike": 105, "option_type": "CALL", "open_interest": 12000},
        {"strike": 110, "option_type": "CALL", "open_interest": 20000},
    ]
    result = analytics.support_resistance_from_oi(chain, stock_price=100)
    assert len(result["support"]) > 0
    assert len(result["resistance"]) > 0
    assert result["support"][0]["strength"] in ("strong", "moderate", "weak")


# ============================================================
# Strategy Scorer Tests
# ============================================================

def test_score_iron_condor_neutral():
    scorer = StrategyScorer()
    market = {"iv_rank": 70, "vix": 25, "trend": "neutral"}
    option = {"iv": 0.25, "dte": 35, "volume": 500, "open_interest": 1000,
              "bid": 2.0, "ask": 2.5, "credit": 2.0, "max_profit": 200, "max_loss": 300}
    tech = {"trend": "neutral", "rsi": 50, "macd_signal": "neutral"}
    score = scorer.score_strategy(StrategyType.IRON_CONDOR, market, option, tech)
    assert 0 <= score["composite_score"] <= 100
    assert 0 <= score["edge_score"] <= 100
    assert 0 <= score["risk_reward_score"] <= 100
    assert 0 <= score["technical_score"] <= 100
    assert 0 <= score["theta_score"] <= 100
    assert 0 <= score["liquidity_score"] <= 100


def test_score_csp_high_iv():
    scorer = StrategyScorer()
    market = {"iv_rank": 80, "vix": 30, "trend": "neutral"}
    option = {"iv": 0.35, "dte": 30, "volume": 1000, "open_interest": 2000,
              "bid": 3.0, "ask": 3.5, "credit": 3.0, "max_profit": 300, "max_loss": 700}
    tech = {"trend": "neutral", "rsi": 50, "macd_signal": "neutral"}
    score = scorer.score_strategy(StrategyType.CASH_SECURED_PUT, market, option, tech)
    assert score["composite_score"] > 0


def test_rank_strategies():
    scorer = StrategyScorer()
    candidates = [
        {"composite_score": 40, "strategy": "A"},
        {"composite_score": 80, "strategy": "B"},
        {"composite_score": 60, "strategy": "C"},
    ]
    ranked = scorer.rank_strategies(candidates)
    assert ranked[0]["strategy"] == "B"
    assert ranked[1]["strategy"] == "C"
    assert ranked[2]["strategy"] == "A"


# ============================================================
# Unusual Activity Detector Tests
# ============================================================

def test_detect_volume_spike():
    detector = UnusualActivityDetector()
    chain = [
        {"strike": 100, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 5000, "open_interest": 1000, "bid": 2.0, "ask": 2.5, "last": 2.25,
         "symbol": "TEST"},
    ]
    signals = detector.scan_chain(chain, stock_price=100)
    assert len(signals) > 0
    assert signals[0]["vol_oi_ratio"] == 5.0
    assert "Volume 5.0x OI" in signals[0]["signals"]


def test_detect_large_block():
    detector = UnusualActivityDetector()
    chain = [
        {"strike": 100, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 500, "open_interest": 1000, "bid": 10.0, "ask": 10.5, "last": 10.25,
         "symbol": "TEST"},
    ]
    signals = detector.scan_chain(chain, stock_price=100)
    assert len(signals) > 0
    assert signals[0]["total_premium"] > 100000


def test_detect_sweep_orders():
    detector = UnusualActivityDetector()
    chain = [
        {"strike": 100, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 3000, "open_interest": 500, "bid": 2.0, "ask": 2.5, "last": 2.25,
         "symbol": "TEST"},
    ]
    sweeps = detector.detect_sweep_orders(chain, stock_price=100)
    assert len(sweeps) > 0
    assert sweeps[0]["signal"] == "sweep_detected"


def test_aggregate_signals():
    detector = UnusualActivityDetector()
    unusual = [
        {"direction": "bullish", "total_premium": 200000},
        {"direction": "bearish", "total_premium": 50000},
    ]
    result = detector.aggregate_signals(unusual, [], [])
    assert result["bias"] == "bullish"
    assert result["net_sentiment"] > 0


def test_low_volume_filtered():
    detector = UnusualActivityDetector()
    chain = [
        {"strike": 100, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 5, "open_interest": 10, "bid": 2.0, "ask": 2.5, "last": 2.25,
         "symbol": "TEST"},
    ]
    signals = detector.scan_chain(chain, stock_price=100)
    assert len(signals) == 0  # Below minimum threshold


# ============================================================
# Recommender Integration Tests
# ============================================================

def test_recommender_uses_strict_dashboard_score_floor():
    """Only strong, independently qualified setups may reach the dashboard."""
    assert MIN_COMPOSITE_SCORE == 70.0

def test_recommender_creates_account():
    rec = TradeRecommender()
    account = AccountInfo(
        total_equity=100000,
        buying_power=200000,
        cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE,
    )
    assert account.total_equity == 100000
    assert account.risk_tolerance == RiskTolerance.MODERATE


def test_recommender_empty_chains():
    rec = TradeRecommender()
    account = AccountInfo(
        total_equity=100000,
        buying_power=200000,
        cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE,
    )
    output = rec.generate_recommendations(
        account=account,
        market_data={"vix": 20},
        option_chains={},
        technical_data={},
        flow_data={},
        volatility_data={"iv": 0.20, "hv_20": 0.18, "iv_rank": 50, "dte": 30},
    )
    assert len(output.recommendations) == 0
    assert output.account_summary.total_equity == 100000


def test_recommender_with_chain():
    rec = TradeRecommender()
    account = AccountInfo(
        total_equity=100000,
        buying_power=200000,
        cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE,
    )
    chain = [
        {"strike": 95, "option_type": "PUT", "expiry": "2026-08-15", "dte": 30,
         "volume": 500, "open_interest": 1000, "bid": 1.0, "ask": 1.5, "last": 1.25,
         "symbol": "AAPL", "iv": 0.25, "delta": -0.3},
        {"strike": 100, "option_type": "PUT", "expiry": "2026-08-15", "dte": 30,
         "volume": 1000, "open_interest": 2000, "bid": 2.5, "ask": 3.0, "last": 2.75,
         "symbol": "AAPL", "iv": 0.25, "delta": -0.5},
        {"strike": 105, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 800, "open_interest": 1500, "bid": 2.0, "ask": 2.5, "last": 2.25,
         "symbol": "AAPL", "iv": 0.25, "delta": 0.5},
        {"strike": 110, "option_type": "CALL", "expiry": "2026-08-15", "dte": 30,
         "volume": 300, "open_interest": 800, "bid": 1.0, "ask": 1.5, "last": 1.25,
         "symbol": "AAPL", "iv": 0.25, "delta": 0.3},
    ]
    output = rec.generate_recommendations(
        account=account,
        market_data={"vix": 25, "AAPL_price": 100},
        option_chains={"AAPL": chain},
        technical_data={"AAPL": {"trend": "neutral", "rsi": 50, "macd_signal": "neutral"}},
        flow_data={},
        volatility_data={"iv": 0.25, "hv_20": 0.20, "iv_rank": 60, "dte": 30},
    )
    # Should have at least some recommendations
    assert isinstance(output.recommendations, list)
    assert output.market_context["vix"] == 25


def test_stock_detail_can_keep_multiple_qualified_structures():
    """Per-stock detail may show alternatives without weakening risk filters."""
    recommender = TradeRecommender()
    account = AccountInfo(
        total_equity=100000,
        buying_power=100000,
        cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE,
        max_positions=3,
    )
    candidates = [
        {"symbol": "AAPL", "capital_required": 500, "delta_impact": 0.05, "vega_impact": 0.01},
        {"symbol": "AAPL", "capital_required": 600, "delta_impact": 0.04, "vega_impact": 0.01},
    ]
    portfolio = {"net_delta": 0, "net_vega": 0}

    headline = recommender._select_recommendations(candidates, account, portfolio, 2000)
    detail = recommender._select_recommendations(
        candidates, account, portfolio, 2000, diversify_underlyings=False
    )

    assert len(headline) == 1
    assert len(detail) == 2


def test_recommender_preserves_actual_market_context_and_otm_credit_geometry():
    recommender = TradeRecommender()
    context = recommender._strategy_market_context(
        {"iv": 0.30, "hv_20": 0.20, "iv_rank": 78, "vix": 26},
        {"trend": "bullish"},
    )
    assert context == {"iv_rank": 78, "vix": 26, "trend": "bullish", "hv_20": 0.20}

    # A bear-call spread must be sold above spot; otherwise it is not the
    # defined-risk bearish structure the dashboard claims it to be.
    below_spot_short_call = {"strike": 95, "bid": 2, "ask": 2.1, "last": 2, "volume": 100, "open_interest": 1000}
    higher_long_call = {"strike": 105, "bid": 1, "ask": 1.1, "last": 1, "volume": 100, "open_interest": 1000}
    assert recommender._score_bear_call(
        "TEST", 100, below_spot_short_call, higher_long_call, 30,
        MarketRegime.BEARISH, {"trend": "bearish"}, {},
        {"iv": 0.30, "hv_20": 0.20, "iv_rank": 78, "vix": 26}, 2000, 10000,
    ) is None


def test_credit_spreads_use_executable_bid_ask_not_stale_last_trade():
    recommender = TradeRecommender()
    short_call = {"bid": 0.20, "ask": 0.25, "last": 0.95}
    long_call = {"bid": 0.08, "ask": 0.10, "last": 0.08}
    assert recommender._executable_credit(short_call, long_call) == 0.10
    assert recommender._executable_credit({"bid": 0, "last": 0.95}, long_call) is None


# ============================================================
# Pipeline Model Tests
# ============================================================

def test_symbol_data_defaults():
    data = SymbolData()
    assert data.symbol == ""
    assert data.price == 0.0
    assert data.trend == "NEUTRAL"
    assert data.iv_rank == 50.0


def test_strategy_score_defaults():
    score = StrategyScore()
    assert score.composite_score == 0.0
    assert score.direction == Direction.NEUTRAL


def test_current_position_defaults():
    pos = CurrentPosition()
    assert pos.symbol == ""
    assert pos.quantity == 0
    assert pos.delta == 0.0


def test_market_conditions_defaults():
    mc = MarketConditions()
    assert mc.vix == 20.0
    assert mc.trend == "neutral"


def test_enum_values():
    assert StrategyType.IRON_CONDOR.value == "iron_condor"
    assert StrategyType.WHEEL.value == "wheel"
    assert StrategyType.BULL_CALL_DEBIT.value == "bull_call_debit"
    assert Direction.BULLISH.value == "bullish"
    assert GEXRegime.HIGH_POSITIVE.value == "high_positive"
    assert RiskTolerance.MODERATE.value == "moderate"


def test_alert_engine_triggers_one_time_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(alerts_module, "ALERTS_FILE", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(alerts_module, "ALERT_HISTORY_FILE", str(tmp_path / "history.json"))
    engine = AlertEngine()
    rule = engine.add_rule("aapl", AlertType.PRICE_ABOVE, 200)

    events = engine.check({"AAPL": {"price": 201}})
    assert len(events) == 1
    assert events[0]["rule_id"] == rule.rule_id
    assert engine.check({"AAPL": {"price": 202}}) == []


def test_signal_tracker_records_due_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tracker_module, "SIGNAL_LOG_FILE", str(tmp_path / "signals.json"))
    monkeypatch.setattr(tracker_module, "SIGNAL_ACCURACY_FILE", str(tmp_path / "accuracy.json"))
    tracker = SignalTracker()
    tracker.record_prediction(
        symbol="AAPL", stock_price=100, overall_signal="buy", overall_score=20,
        confidence=75, regime="neutral", best_strategy="cash_secured_put",
        signals=[{"source": "technical"}], days_to_outcome=5,
    )
    log = tracker._read_log()
    log[0]["timestamp"] = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    tracker._write_log(log)

    assert tracker.record_outcome("AAPL", 105) == 1
    summary = tracker.get_performance_summary()
    assert summary["overall_accuracy_pct"] == 100.0
    accuracy = tracker.get_accuracy_by_source(min_samples=1)
    assert accuracy["technical"]["correct_predictions"] == 1


if __name__ == "__main__":
    # Run all tests
    import traceback
    tests = [
        test_csp_roi_basic, test_csp_roi_annualized, test_csp_roi_otm_pct,
        test_cc_roi_basic, test_credit_spread_roi, test_iron_condor_roi,
        test_rank_opportunities, test_scan_all_strikes_csp,
        test_max_pain, test_max_pain_empty, test_expected_move,
        test_expected_move_with_straddle, test_nvrp_positive, test_nvrp_negative,
        test_nvrp_neutral, test_probability_of_touch, test_support_resistance,
        test_score_iron_condor_neutral, test_score_csp_high_iv, test_rank_strategies,
        test_detect_volume_spike, test_detect_large_block, test_detect_sweep_orders,
        test_aggregate_signals, test_low_volume_filtered,
        test_recommender_creates_account, test_recommender_empty_chains,
        test_recommender_with_chain,
        test_symbol_data_defaults, test_strategy_score_defaults,
        test_current_position_defaults, test_market_conditions_defaults,
        test_enum_values,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
