"""
Tests for the Trade Recommendation Engine.
Covers ROI calculator, analytics, strategy scorer, recommender,
and unusual activity detector.
"""
import math
import sys
import os
import pytest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, MarketRegime, Direction, GEXRegime,
    StrategyType, OptionContract, StrategyLeg,
)
from agents.trade_engine.roi_calculator import ROICalculator
from agents.trade_engine.analytics import OptionsAnalytics
from agents.trade_engine.strategy_scorer import StrategyScorer
from agents.trade_engine.recommender import (
    TradeRecommender, MIN_COMPOSITE_SCORE, MIN_IV_RANK_SELL,
    MIN_IV_RANK_BUY, MAX_VIX_SELL, MIN_IRON_CONDOR_CREDIT_TO_WIDTH,
    MIN_CREDIT_SPREAD_CREDIT_TO_WIDTH, MIN_CREDIT_SPREAD_LEG_OI,
    MIN_SINGLE_LEG_OI, MAX_CORRELATED_POSITIONS,
)
from agents.flow_analysis.unusual_activity import UnusualActivityDetector
from agents.trade_engine import alerts as alerts_module
from agents.trade_engine import signal_tracker as tracker_module
from agents.trade_engine import recommender as recommender_module
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


def test_iron_condor_uses_wider_wing_for_risk():
    calc = ROICalculator()
    roi = calc.iron_condor_roi(95, 92, 105, 104, 0.50, 30, 100, iv=0.20)
    assert roi["max_loss"] == 250.0


def test_iv_based_pop_respects_option_direction():
    calc = ROICalculator()
    assert calc._approx_pop_otm(100, 90, 30, "put", 0.20) > 50
    assert calc._approx_pop_otm(100, 110, 30, "call", 0.20) > 50


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
    assert MIN_COMPOSITE_SCORE == 75.0
    recommender = TradeRecommender()
    assert recommender._passes_quality_gate({"composite_score": 75, "edge_score": 60}, {"probability_of_profit": 55})
    assert not recommender._passes_quality_gate({"composite_score": 75, "edge_score": 59}, {"probability_of_profit": 80})
    assert not recommender._passes_quality_gate({"composite_score": 80, "edge_score": 80}, {"probability_of_profit": 54})

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
# TastyTrade / ORATS Professional Volatility Gates
# ============================================================

def test_volatility_gate_blocks_selling_when_iv_cheap():
    """Selling premium requires an elevated IV Rank (>= MIN_IV_RANK_SELL)."""
    recommender = TradeRecommender()
    for strategy in ("csp", "cc", "bull_put", "bear_call", "iron_condor"):
        assert not recommender._passes_volatility_gate(
            strategy, {"iv_rank": MIN_IV_RANK_SELL - 1, "vix": 20, "iv": 0.30, "hv_20": 0.20}
        )
        assert recommender._passes_volatility_gate(
            strategy, {"iv_rank": MIN_IV_RANK_SELL, "vix": 20, "iv": 0.30, "hv_20": 0.20}
        )


def test_volatility_gate_blocks_selling_in_vix_spike():
    """No premium selling above the crash-regime VIX ceiling."""
    recommender = TradeRecommender()
    assert not recommender._passes_volatility_gate(
        "csp", {"iv_rank": 80, "vix": MAX_VIX_SELL + 1, "iv": 0.40, "hv_20": 0.30}
    )
    assert recommender._passes_volatility_gate(
        "csp", {"iv_rank": 80, "vix": MAX_VIX_SELL, "iv": 0.40, "hv_20": 0.30}
    )


def test_volatility_gate_requires_iv_above_hv_for_selling():
    """Selling requires a positive volatility risk premium (IV > HV)."""
    recommender = TradeRecommender()
    assert not recommender._passes_volatility_gate(
        "bull_put", {"iv_rank": 70, "vix": 20, "iv": 0.15, "hv_20": 0.20}
    )
    assert recommender._passes_volatility_gate(
        "bull_put", {"iv_rank": 70, "vix": 20, "iv": 0.21, "hv_20": 0.20}
    )


def test_volatility_gate_only_buys_when_iv_cheap():
    """Debit spreads are only authorized when IV Rank is depressed."""
    recommender = TradeRecommender()
    assert not recommender._passes_volatility_gate(
        "call_debit", {"iv_rank": MIN_IV_RANK_BUY + 1, "vix": 20}
    )
    assert recommender._passes_volatility_gate(
        "call_debit", {"iv_rank": MIN_IV_RANK_BUY, "vix": 20}
    )


def test_quality_gate_applies_volatility_to_sell_structures():
    recommender = TradeRecommender()
    strong = {"composite_score": 80, "edge_score": 80}
    high_pop = {"probability_of_profit": 70}
    # Score passes but IV Rank is too low to sell premium.
    assert not recommender._passes_quality_gate(
        strong, high_pop, "csp", {"iv_rank": 10, "vix": 20, "iv": 0.30, "hv_20": 0.20}
    )
    assert recommender._passes_quality_gate(
        strong, high_pop, "csp", {"iv_rank": 60, "vix": 20, "iv": 0.30, "hv_20": 0.20}
    )


def test_spread_requires_liquid_short_leg():
    """Spreads must have a liquid short (executed) leg like singles do."""
    recommender = TradeRecommender()
    illiquid = {"strike": 90, "bid": 2, "ask": 2.1, "volume": 0, "open_interest": 0}
    long_put = {"strike": 85, "bid": 0.5, "ask": 0.6, "volume": 500, "open_interest": 1000}
    assert recommender._score_bull_put(
        "TEST", 100, illiquid, long_put, 30, MarketRegime.NEUTRAL,
        {"trend": "neutral"}, {}, {"iv": 0.30, "hv_20": 0.20, "iv_rank": 70, "vix": 20},
        2000, 10000,
    ) is None


def test_opscanbot_execution_floors_require_open_interest_and_credit_to_width():
    """Verticals require liquid legs and meaningful credit, not just a high score."""
    recommender = TradeRecommender()
    liquid = {"open_interest": MIN_CREDIT_SPREAD_LEG_OI}
    assert recommender._passes_credit_spread_execution_gate(
        liquid, liquid, MIN_CREDIT_SPREAD_CREDIT_TO_WIDTH * 5, 5
    )
    assert not recommender._passes_credit_spread_execution_gate(
        {"open_interest": MIN_CREDIT_SPREAD_LEG_OI - 1}, liquid, 1.5, 5
    )
    assert not recommender._passes_credit_spread_execution_gate(liquid, liquid, 1.24, 5)
    assert recommender._has_minimum_open_interest(
        {"open_interest": MIN_SINGLE_LEG_OI}, MIN_SINGLE_LEG_OI
    )
    assert not recommender._has_minimum_open_interest(
        {"open_interest": MIN_SINGLE_LEG_OI - 1}, MIN_SINGLE_LEG_OI
    )


def test_iron_condor_requires_credit_to_width():
    """Iron condors must collect at least 1/3 of the wing width."""
    recommender = TradeRecommender()
    def make_option(strike, bid, ask, delta=0.2):
        return {"strike": strike, "bid": bid, "ask": ask, "volume": 1000,
                "open_interest": 5000, "delta": delta}
    # Wings are 5 wide on each side but the combined credit is only 0.90
    # (0.18 of width) — far below the 1/3 threshold.
    thin_puts = [make_option(85, 0.25, 0.30, -0.1), make_option(90, 0.75, 0.80, -0.2), make_option(95, 1.75, 1.80, -0.35)]
    thin_calls = [make_option(105, 1.75, 1.80, 0.35), make_option(110, 0.75, 0.80, 0.2), make_option(115, 0.25, 0.30, 0.1)]
    assert recommender._score_iron_condor(
        "TEST", 100, thin_puts, thin_calls, 30, MarketRegime.NEUTRAL,
        {"trend": "neutral"}, {}, {"iv": 0.30, "hv_20": 0.20, "iv_rank": 70, "vix": 20},
        2000, 10000,
    ) is None


def test_kelly_uses_payoff_ratio_not_win_rate():
    """kelly_fraction is real half-Kelly, not a raw win-rate estimate."""
    recommender = TradeRecommender()
    # 80% POP with a 150/350 payoff: positive half-Kelly.
    kelly = recommender._calculate_kelly(
        {"credit": 1.5}, {"max_profit": 150, "max_loss": 350, "probability_of_profit": 80}
    )
    assert 0 < kelly <= 0.5
    # A CSP-like payoff (max profit << max loss) sizes to zero Kelly.
    assert recommender._calculate_kelly(
        {"premium": 3.0}, {"max_loss": 9700, "probability_of_profit": 85}
    ) == 0.0


def test_selection_respects_leg_greeks():
    """The portfolio Greeks gate uses short-leg delta/vega, not zeros."""
    recommender = TradeRecommender()
    account = AccountInfo(
        total_equity=100000, buying_power=200000, cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE, max_positions=3,
    )
    near_atm = {"symbol": "AAPL", "capital_required": 500,
                "legs": [{"action": "SELL", "delta": -0.60, "vega": -0.05}]}
    far_otm = {"symbol": "MSFT", "capital_required": 500,
               "legs": [{"action": "SELL", "delta": -0.15, "vega": -0.02}]}
    portfolio = {"net_delta": 0, "net_vega": 0}
    selected = recommender._select_recommendations([near_atm, far_otm], account, portfolio, 2000)
    assert len(selected) == 1
    assert selected[0]["symbol"] == "MSFT"


def test_risk_budget_binds_max_loss_not_capital_outlay():
    """The per-trade risk budget compares what the position can lose."""
    recommender = TradeRecommender()
    account = AccountInfo(
        total_equity=100000, buying_power=200000, cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE, max_positions=3,
    )
    candidate = {"symbol": "AAPL", "capital_required": 100000,
                 "max_loss": 1500, "delta_impact": 0.1, "vega_impact": 0.01}
    selected = recommender._select_recommendations([candidate], account, {"net_delta": 0, "net_vega": 0}, 2000)
    assert len(selected) == 1
    # A candidate whose max loss exceeds the budget is rejected.
    too_big = dict(candidate, max_loss=2500)
    assert recommender._select_recommendations([too_big], account, {"net_delta": 0, "net_vega": 0}, 2000) == []


def test_correlation_cap_limits_sector_concentration():
    """No more than MAX_CORRELATED_POSITIONS per sector bucket."""
    recommender = TradeRecommender()
    account = AccountInfo(
        total_equity=100000, buying_power=200000, cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE, max_positions=8,
    )
    candidates = [
        {"symbol": symbol, "capital_required": 500, "delta_impact": 0.01,
         "vega_impact": 0.001, "max_loss": 400,
         "legs": [{"action": "SELL", "delta": -0.1, "vega": -0.01}]}
        for symbol in ("AAPL", "MSFT", "NVDA", "AMD", "JPM")
    ]
    portfolio = {"net_delta": 0, "net_vega": 0}
    selected = recommender._select_recommendations(candidates, account, portfolio, 2000)

    # 4 tech names capped at MAX_CORRELATED_POSITIONS; the bank still fits.
    symbols = [cand["symbol"] for cand in selected]
    assert len(selected) == MAX_CORRELATED_POSITIONS + 1
    assert symbols.count("AMD") == 0  # the 4th tech name was refused
    assert "JPM" in symbols  # unrelated sector not punished


def test_correlation_cap_does_not_fabricate_correlations():
    """Unknown symbols are uncorrelated singletons — never lumped in."""
    recommender = TradeRecommender()
    account = AccountInfo(
        total_equity=100000, buying_power=200000, cash_available=100000,
        risk_tolerance=RiskTolerance.MODERATE, max_positions=8,
    )
    candidates = [
        {"symbol": symbol, "capital_required": 500, "delta_impact": 0.01,
         "vega_impact": 0.001, "max_loss": 400,
         "legs": [{"action": "SELL", "delta": -0.1, "vega": -0.01}]}
        for symbol in ("ZZZZ", "YYYY", "XXXX")
    ]
    portfolio = {"net_delta": 0, "net_vega": 0}
    selected = recommender._select_recommendations(candidates, account, portfolio, 2000)
    assert len(selected) == 3


# ── empirical outcome gate (realized journal evidence) ─────────────────────

def test_empirical_gate_fails_open_without_evidence(monkeypatch):
    """A fetch failure (or an empty journal) must never mint a rejection."""
    def boom(*args, **kwargs):
        raise OSError("network down")
    monkeypatch.setattr("httpx.get", boom)

    recommender = TradeRecommender()
    recommender_module._EMPIRICAL_CACHE["at"] = 0.0
    recommender_module._EMPIRICAL_CACHE["stats"] = None
    assert recommender._passes_empirical_gate({"type": "bull_put"}) is True


def test_empirical_gate_skips_non_sell_structures():
    recommender = TradeRecommender()
    recommender_module._EMPIRICAL_CACHE["at"] = 1000.0
    recommender_module._EMPIRICAL_CACHE["stats"] = {"win_rate": 10.0, "expectancy": -50.0, "n": 40}
    # Debit structures are not judged against the short-premium track record.
    assert recommender._passes_empirical_gate({"type": "call_debit"}) is True


def test_empirical_gate_rejects_persistently_losing_strategy():
    import time
    recommender = TradeRecommender()
    recommender_module._EMPIRICAL_CACHE["at"] = time.time()
    recommender_module._EMPIRICAL_CACHE["stats"] = {"win_rate": 30.0, "expectancy": -12.0, "n": 40}
    assert recommender._passes_empirical_gate({"type": "iron_condor"}) is False


def test_empirical_gate_passes_winning_strategy():
    import time
    recommender = TradeRecommender()
    recommender_module._EMPIRICAL_CACHE["at"] = time.time()
    recommender_module._EMPIRICAL_CACHE["stats"] = {"win_rate": 75.0, "expectancy": 40.0, "n": 40}
    assert recommender._passes_empirical_gate({"type": "bull_put"}) is True


# ============================================================
# Probability-of-Touch & Round-Trip Cost Entry Gates
# ============================================================

def test_touch_gate_rejects_short_strikes_likely_to_be_tested():
    """Sell structures whose short legs are likely to be touched are rejected."""
    recommender = TradeRecommender()
    far_otm = {"type": "bull_put", "stock_price": 100, "dte": 45,
               "nvrp": {"iv": 0.25}, "legs": [{"action": "SELL", "strike": 90}]}
    assert recommender._passes_touch_gate(far_otm)
    # Near-the-money short strike is almost certain to be touched before expiry.
    atm = {"type": "bull_put", "stock_price": 100, "dte": 45,
           "nvrp": {"iv": 0.25}, "legs": [{"action": "SELL", "strike": 100}]}
    assert not recommender._passes_touch_gate(atm)
    # A debit spread's short leg is a hedge, not a sell decision: not gated.
    debit = {"type": "call_debit", "stock_price": 100, "dte": 45,
             "nvrp": {"iv": 0.25}, "legs": [{"action": "SELL", "strike": 100}]}
    assert recommender._passes_touch_gate(debit)


def test_touch_gate_applies_to_every_short_wing():
    """Iron condors gate both short wings."""
    recommender = TradeRecommender()
    clean = {"type": "iron_condor", "stock_price": 100, "dte": 45,
             "nvrp": {"iv": 0.25},
             "legs": [{"action": "SELL", "strike": 85}, {"action": "SELL", "strike": 115}]}
    assert recommender._passes_touch_gate(clean)
    tested = {"type": "iron_condor", "stock_price": 100, "dte": 45,
              "nvrp": {"iv": 0.25},
              "legs": [{"action": "SELL", "strike": 85}, {"action": "SELL", "strike": 100}]}
    assert not recommender._passes_touch_gate(tested)


# ============================================================
# High-Win-Rate Context Gates (step 4c)
# ============================================================

def test_high_winrate_gate_rejects_bull_put_in_a_downtrend():
    recommender = TradeRecommender()
    candidate = {"type": "bull_put", "stock_price": 100, "short_strike": 90, "long_strike": 85,
                 "dte": 30, "nvrp": {"iv": 0.25, "trend": "bearish"}}
    assert not recommender._passes_high_winrate_gate(candidate)


def test_high_winrate_gate_rejects_short_strike_inside_the_expected_move():
    recommender = TradeRecommender()
    # 100 * 0.25 * sqrt(30/365) = 7.24 → the 95 strike is inside the 1-SD move.
    candidate = {"type": "bull_put", "stock_price": 100, "short_strike": 95, "long_strike": 90,
                 "dte": 30, "nvrp": {"iv": 0.25, "trend": "bullish"}}
    assert not recommender._passes_high_winrate_gate(candidate)


def test_high_winrate_gate_rejects_short_premium_inside_the_gamma_window():
    recommender = TradeRecommender()
    candidate = {"type": "bull_put", "stock_price": 100, "short_strike": 90, "long_strike": 85,
                 "dte": 18, "nvrp": {"iv": 0.25, "trend": "bullish"}}
    assert not recommender._passes_high_winrate_gate(candidate)


def test_high_winrate_gate_accepts_a_textbook_credit_spread():
    recommender = TradeRecommender()
    candidate = {"type": "bull_put", "stock_price": 100, "short_strike": 90, "long_strike": 85,
                 "dte": 35, "nvrp": {"iv": 0.25, "trend": "bullish"}}
    assert recommender._passes_high_winrate_gate(candidate)


def test_high_winrate_gate_ignores_debit_structures():
    recommender = TradeRecommender()
    candidate = {"type": "call_debit", "stock_price": 100, "short_strike": 105, "long_strike": 100,
                 "dte": 30, "nvrp": {"iv": 0.25, "trend": "bearish"}}
    assert recommender._passes_high_winrate_gate(candidate)


def test_credit_spreads_require_round_trip_credit_floor():
    """Spreads whose credit cannot cover round-trip costs are skipped."""
    recommender = TradeRecommender()
    tiny_short = {"strike": 100, "bid": 0.10, "ask": 0.11, "volume": 1000,
                  "open_interest": 5000, "delta": -0.2}
    tiny_long = {"strike": 95, "bid": 0.01, "ask": 0.02, "volume": 1000,
                 "open_interest": 5000}
    # Credit = 0.10 - 0.02 = 0.08, below MIN_SPREAD_CREDIT (0.15).
    assert recommender._score_bull_put(
        "TEST", 100, tiny_short, tiny_long, 30, MarketRegime.NEUTRAL,
        {"trend": "neutral"}, {}, {"iv": 0.30, "hv_20": 0.20, "iv_rank": 70, "vix": 20},
        2000, 10000,
    ) is None


# ============================================================
# Option Alpha EV / Alpha Metric
# ============================================================

def test_expected_value_uses_partial_zone():
    """The three-outcome EV matches Option Alpha's published example ($1.00)."""
    calc = ROICalculator()
    ev = calc.expected_value(max_profit=2, max_loss=3, probability_of_profit=0.7, probability_of_loss=0.1)
    # 2*0.7 + (2-3)/2*0.2 - 3*0.1 = 1.00
    assert ev == pytest.approx(1.0, abs=1e-4)


def test_expected_value_two_outcome_defaults_to_complement():
    calc = ROICalculator()
    ev = calc.expected_value(max_profit=2, max_loss=3, probability_of_profit=0.7)
    # Pure two-outcome: 2*0.7 - 3*0.3 = 0.50
    assert ev == pytest.approx(0.5, abs=1e-4)


def test_alpha_scores_ev_per_dollar_of_risk():
    calc = ROICalculator()
    alpha = calc.alpha_score(max_profit=2, max_loss=3, probability_of_profit=0.7, probability_of_loss=0.1)
    assert alpha == pytest.approx(1.0 / 3.0, abs=1e-4)
    assert calc.alpha_score(max_profit=2, max_loss=0, probability_of_profit=0.7) == 0.0


def test_structure_expected_value_defines_risk_boundaries():
    """Credit structures value the partial-profit zone, not just two outcomes."""
    recommender = TradeRecommender()
    roi = {"max_profit": 200, "max_loss": 300, "probability_of_profit": 70}
    cand = {"type": "bull_put", "stock_price": 100, "dte": 30, "nvrp": {"iv": 0.25},
            "short_strike": 95, "long_strike": 90}
    assert recommender._structure_expected_value(cand, roi) > 0
    # Structures without a defined max loss fall back to the two-outcome model.
    cc_roi = {"max_profit": 500, "probability_of_profit": 80}
    cc_cand = {"type": "cc", "stock_price": 100, "dte": 30, "nvrp": {"iv": 0.25}}
    assert recommender._structure_expected_value(cc_cand, cc_roi) == pytest.approx(400.0, abs=1e-4)


def test_alpha_wired_into_recommendation():
    """The recommendation carries the three-outcome EV and Alpha, not the naive EV."""
    recommender = TradeRecommender()
    candidate = {
        "type": "bull_put", "symbol": "TEST", "stock_price": 100, "dte": 30,
        "credit": 1.5, "short_strike": 95, "long_strike": 90, "expiry": "2026-09-15",
        "capital_required": 350, "max_profit": 150, "max_loss": 350,
        "nvrp": {"iv": 0.25, "hv_20": 0.20, "iv_rank": 60, "vix": 20},
        "score": {"composite_score": 80, "edge_score": 70},
        "roi": {"max_profit": 150, "max_loss": 350, "probability_of_profit": 75,
                "annualized_return_pct": 30},
        "legs": [
            {"symbol": "TEST", "strike": 95, "expiry": "2026-09-15", "option_type": "PUT",
             "bid": 1.6, "ask": 1.7, "volume": 100, "open_interest": 500,
             "iv": 0.25, "delta": -0.2, "dte": 30, "action": "SELL"},
            {"symbol": "TEST", "strike": 90, "expiry": "2026-09-15", "option_type": "PUT",
             "bid": 0.1, "ask": 0.11, "volume": 100, "open_interest": 500,
             "iv": 0.25, "delta": -0.1, "dte": 30, "action": "BUY"},
        ],
    }
    rec = recommender._build_recommendation(
        candidate, MarketRegime.NEUTRAL, {"vix": 20}, {"iv": 0.25, "iv_rank": 60}
    )
    naive_ev = rec.max_profit * rec.probability_of_profit / 100 - rec.max_loss * (100 - rec.probability_of_profit) / 100
    assert rec.expected_value > 0
    assert rec.alpha > 0
    assert rec.alpha == pytest.approx(rec.expected_value / rec.max_loss, abs=1e-4)
    assert rec.expected_value != naive_ev


# ============================================================
# Strategy- and Regime-Aware Exit Rules
# ============================================================

def test_exit_rules_are_strategy_and_regime_aware():
    recommender = TradeRecommender()
    thin_condor = recommender._generate_exit_rules(
        StrategyType.IRON_CONDOR,
        {"credit": 1.20, "wing_width": 5, "dte": 45, "nvrp": {"iv_rank": 55}},
    )
    assert "Close at 25%" in thin_condor["profit_target"]
    assert "2-3x its wing credit" in thin_condor["stop_loss"]
    assert thin_condor["close_rule"] == "Whichever comes first: profit target, 21 DTE, or hard stop"

    wide_condor = recommender._generate_exit_rules(
        StrategyType.IRON_CONDOR,
        {"credit": 2.50, "wing_width": 5, "dte": 45, "nvrp": {"iv_rank": 55}},
    )
    assert "Close at 50%" in wide_condor["profit_target"]

    high_iv = recommender._generate_exit_rules(
        StrategyType.BULL_PUT_CREDIT,
        {"credit": 1.50, "dte": 45, "nvrp": {"iv_rank": 65}},
    )
    assert "Close at 75%" in high_iv["profit_target"]

    normal = recommender._generate_exit_rules(
        StrategyType.BULL_PUT_CREDIT,
        {"credit": 1.50, "dte": 45, "nvrp": {"iv_rank": 40}},
    )
    assert "Close at 50%" in normal["profit_target"]
    assert "2-3x credit received" in normal["stop_loss"]
    assert "hold_to_expiry" in normal


# ============================================================
# Model Tests
# ============================================================

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
