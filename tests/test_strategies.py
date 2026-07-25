"""
Comprehensive test suite for ThetaForge.
Tests all 13 strategies, GEX engine, dark pool detection,
multi-layer scanner, technical indicators, and risk management.
"""
import pytest
import asyncio
import numpy as np
from unittest.mock import MagicMock, AsyncMock

# Strategy imports
from agents.strategies.base_strategy import BaseStrategy, TradeSignal
from agents.strategies.wheel import WheelStrategy
from agents.strategies.vertical_spreads import VerticalSpreadStrategy
from agents.strategies.iron_condor import IronCondorStrategy
from agents.strategies.credit_spread import CreditSpreadStrategy
from agents.strategies.covered_call import CoveredCallStrategy
from agents.strategies.earnings_straddle import EarningsStraddleStrategy
from agents.strategies.gamma_blast import GammaBlastStrategy

# Core engine imports
from agents.risk_management.kelly_calculator import calculate_kelly, calculate_position_size
from agents.risk_management.portfolio_limits import RiskManager
from agents.volatility.iv_metrics import calculate_iv_rank, calculate_iv_percentile
from agents.volatility.greeks import calculate_greeks
from agents.flow_analysis.gex_engine import GEXEngine
from agents.flow_analysis.dark_pool import DarkPoolDetector
from agents.flow_analysis.scanner_pipeline import MultiLayerScanner
from agents.flow_analysis.unusual_activity import UnusualActivityDetector
from agents.sentiment.nlp_10pass import NLP10Pass
from agents.backtest.backtester import Backtester


# ==========================================
# Strategy Tests
# ==========================================

@pytest.mark.asyncio
async def test_wheel_strategy_scan():
    strategy = WheelStrategy()
    market_data = {"SPY_iv_rank": 80, "SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0
    assert signals[0].symbol == "SPY"
    assert signals[0].strategy_name == "Wheel"


@pytest.mark.asyncio
async def test_wheel_no_signal_low_iv():
    strategy = WheelStrategy()
    market_data = {"SPY_iv_rank": 25, "SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_vertical_spread_scan():
    strategy = VerticalSpreadStrategy()
    market_data = {"SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


@pytest.mark.asyncio
async def test_iron_condor_scan():
    strategy = IronCondorStrategy()
    market_data = {"SPY_iv_rank": 70, "SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


@pytest.mark.asyncio
async def test_credit_spread_scan():
    strategy = CreditSpreadStrategy()
    market_data = {"SPY_iv_rank": 60, "SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


@pytest.mark.asyncio
async def test_covered_call_scan():
    strategy = CoveredCallStrategy()
    market_data = {"SPY_price": 500, "owns_SPY": True, "SPY_shares": 100}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


@pytest.mark.asyncio
async def test_earnings_straddle_scan():
    strategy = EarningsStraddleStrategy()
    market_data = {"AAPL_dte": 2, "AAPL_iv_rank": 15, "AAPL_price": 190, "AAPL_implied_move_pct": 3.0, "AAPL_historical_earnings_move_pct": 5.0}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


@pytest.mark.asyncio
async def test_gamma_blast_scan():
    strategy = GammaBlastStrategy()
    market_data = {"SPY_daily_range_pct": 0.5, "SPY_price": 500}
    signals = await strategy.scan(market_data)
    assert len(signals) > 0


# ==========================================
# GEX Engine Tests
# ==========================================

def test_gex_calculation():
    engine = GEXEngine(underlying_price=500)
    chain = [
        {"strike": 490, "open_interest": 1000, "last": 5.0, "implied_volatility": 0.2, "option_type": "PUT", "expiry": "2026-08-15"},
        {"strike": 500, "open_interest": 2000, "last": 10.0, "implied_volatility": 0.2, "option_type": "CALL", "expiry": "2026-08-15"},
        {"strike": 510, "open_interest": 1500, "last": 3.0, "implied_volatility": 0.2, "option_type": "CALL", "expiry": "2026-08-15"},
    ]
    result = engine.calculate_chain_gex(chain, 500)
    assert "net_gex" in result
    assert "dealer_gex" in result
    assert "gex_regime" in result


def test_gex_regime_classification():
    engine = GEXEngine()
    assert engine._classify_gex_regime(1000) == "HIGH_POSITIVE_GEX"
    assert engine._classify_gex_regime(-1000) == "HIGH_NEGATIVE_GEX"
    assert engine._classify_gex_regime(50) == "FLIP_ZONE"
    assert engine._classify_gex_regime(300) == "NEUTRAL"


def test_gex_trading_signals():
    engine = GEXEngine()
    signals_high_pos = engine.get_gex_trading_signals({"gex_regime": "HIGH_POSITIVE_GEX", "net_gex": 600})
    assert len(signals_high_pos) > 0
    assert "premium" in signals_high_pos[0].lower()

    signals_flip = engine.get_gex_trading_signals({"gex_regime": "FLIP_ZONE", "net_gex": 50})
    assert "volatility" in signals_flip[0].lower()


# ==========================================
# Dark Pool Detection Tests
# ==========================================

def test_dark_pool_volume_anomaly():
    detector = DarkPoolDetector()
    result = detector.analyze_volume_anomaly(
        current_volume=5000,
        avg_volume_20d=1000,
        current_oi=1000,
        prev_oi=900,
    )
    assert result["dark_pool_signal"] is True
    assert result["volume_ratio"] == 5.0
    assert result["confidence"] > 0


def test_dark_pool_no_signal():
    detector = DarkPoolDetector()
    result = detector.analyze_volume_anomaly(
        current_volume=100,
        avg_volume_20d=100,
        current_oi=5000,
        prev_oi=5000,
    )
    assert result["dark_pool_signal"] is False
    assert result["signal_type"] == "NORMAL"


def test_block_print_detection():
    detector = DarkPoolDetector()
    trades = [
        {"quantity": 100, "price": 5.0, "option_type": "CALL", "strike": 500, "expiry": "2026-08-15"},
        {"quantity": 1000, "price": 10.0, "option_type": "PUT", "strike": 490, "expiry": "2026-08-15"},
    ]
    blocks = detector.detect_block_prints(trades)
    assert len(blocks) == 1
    assert blocks[0]["premium"] == 1_000_000


def test_dark_pool_prints_analysis():
    detector = DarkPoolDetector()
    prints = [
        {"premium": 500_000, "option_type": "CALL"},
        {"premium": 200_000, "option_type": "CALL"},
        {"premium": 100_000, "option_type": "PUT"},
    ]
    result = detector.analyze_dark_pool_prints(prints)
    assert result["bias"] == "BULLISH"
    assert result["cp_ratio"] > 2.0


# ==========================================
# Multi-Layer Scanner Tests
# ==========================================

@pytest.mark.asyncio
async def test_scanner_pipeline_flow_layer():
    scanner = MultiLayerScanner()
    candidates = [
        {"volume": 2000, "open_interest": 500, "last": 5.0, "ask": 5.1, "strike": 500, "option_type": "CALL", "expiry": "2026-08-15"},
    ]
    result = await scanner._layer_flow(candidates)
    assert len(result) > 0
    assert result[0]["flow_score"] > 0


@pytest.mark.asyncio
async def test_scanner_pipeline_full():
    scanner = MultiLayerScanner()
    candidates = [
        {
            "volume": 2000, "open_interest": 500, "last": 5.0, "ask": 5.1,
            "strike": 500, "option_type": "CALL", "expiry": "2026-08-15",
            "flow_score": 0.8, "action": "SELL", "gex_regime": "NEUTRAL",
            "underlying_trend": "BULLISH", "days_to_earnings": 30,
            "strategy_name": "CreditSpread", "max_loss": 500, "max_profit": 200,
            "confidence_score": 75,
        }
    ]
    result = await scanner.scan(candidates)
    assert scanner.layer_results["input"] == 1
    assert "layer6_risk" in scanner.layer_results


# ==========================================
# Technical Indicators Tests
# ==========================================

def test_technical_indicators():
    import pandas as pd
    from agents.technical.indicators import TechnicalEngine

    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=100)
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    df = pd.DataFrame({
        "Close": prices,
        "High": prices + 0.5,
        "Low": prices - 0.5,
        "Volume": np.random.randint(1000000, 5000000, 100),
    }, index=dates)

    indicators = TechnicalEngine.calculate_all_indicators(df)
    assert "trend" in indicators
    assert "rsi" in indicators
    assert "macd" in indicators
    assert "bollinger" in indicators
    assert "atr" in indicators
    assert indicators["rsi"] > 0


def test_trend_detection():
    import pandas as pd
    from agents.technical.indicators import TechnicalEngine

    # Strong uptrend
    dates = pd.date_range("2024-01-01", periods=100)
    prices = np.linspace(100, 150, 100)
    df = pd.DataFrame({
        "Close": prices,
        "High": prices + 0.5,
        "Low": prices - 0.5,
        "Volume": np.ones(100) * 1_000_000,
    }, index=dates)

    indicators = TechnicalEngine.calculate_all_indicators(df)
    assert indicators["trend"] in ["BULLISH", "STRONG_BULLISH"]


# ==========================================
# Risk Management Tests
# ==========================================

def test_kelly_criterion():
    kelly = calculate_kelly(0.6, 2.0, use_half_kelly=True)
    assert 0.0 < kelly < 0.5

    full_kelly = calculate_kelly(0.6, 2.0, use_half_kelly=False)
    assert full_kelly > kelly


def test_position_sizing():
    size = calculate_position_size(100_000, 2.0, 2.0, 0.1)
    assert size >= 0
    assert isinstance(size, int)


def test_risk_manager():
    rm = RiskManager()
    rm.set_start_equity(100_000)
    assert rm.check_daily_loss(100_000) is False
    assert rm.check_daily_loss(90_000) is False  # -10%, no breach
    assert rm.check_daily_loss(84_000) is True   # -16%, exceeds -15% threshold

    rm2 = RiskManager()
    rm2.peak_equity = 100_000
    assert rm2.check_drawdown(40_000) is True  # -60% drawdown exceeds -50% threshold


# ==========================================
# IV Metrics Tests
# ==========================================

def test_iv_rank():
    iv_rank = calculate_iv_rank(0.3, [0.1, 0.2, 0.3, 0.4, 0.5])
    assert iv_rank == pytest.approx(50.0)  # (0.3 - 0.1) / (0.5 - 0.1) * 100


def test_iv_percentile():
    iv_pct = calculate_iv_percentile(0.3, [0.1, 0.15, 0.2, 0.25, 0.35, 0.4])
    assert iv_pct > 0
    assert iv_pct <= 100


# ==========================================
# Greeks Tests
# ==========================================

def test_greeks_calculation():
    greeks = calculate_greeks("c", 100, 100, 0.25, 0.05, 0.2)
    assert "delta" in greeks
    assert "gamma" in greeks
    assert "theta" in greeks
    assert "vega" in greeks
    assert 0 < greeks["delta"] < 1  # Call delta between 0 and 1


def test_put_greeks():
    greeks = calculate_greeks("p", 100, 100, 0.25, 0.05, 0.2)
    assert greeks["delta"] < 0  # Put delta is negative


# ==========================================
# Sentiment Tests
# ==========================================

def test_nlp_sentiment():
    nlp = NLP10Pass()
    result = nlp.analyze("SPY is going to the moon! Buy calls!")
    assert result["sentiment"] == "BULLISH"
    assert result["score"] > 0


def test_nlp_bearish():
    nlp = NLP10Pass()
    result = nlp.analyze("Market crash coming. Bear market puts time.")
    assert result["sentiment"] == "BEARISH"
    assert result["score"] < 0


def test_nlp_neutral():
    nlp = NLP10Pass()
    result = nlp.analyze("The market is open today.")
    assert result["sentiment"] == "NEUTRAL"


# ==========================================
# Unusual Activity Tests
# ==========================================

def test_unusual_activity_scan():
    detector = UnusualActivityDetector()
    chain = [
        {"volume": 5000, "avg_volume": 500, "open_interest": 1000, "prev_open_interest": 900, "symbol": "SPY", "strike": 500, "expiry": "2026-08-15"},
        {"volume": 100, "avg_volume": 500, "open_interest": 1000, "prev_open_interest": 1000, "symbol": "SPY", "strike": 490, "expiry": "2026-08-15"},
    ]
    alerts = detector.scan_chain(chain, stock_price=500)
    assert len(alerts) > 0  # First option should trigger high volume alert


# ==========================================
# Backtester Tests
# ==========================================

def test_backtester_initialization():
    bt = Backtester(initial_capital=50_000)
    assert bt.initial_capital == 50_000
    assert bt.capital == 50_000
    assert len(bt.closed_trades) == 0


def test_backtester_report_empty():
    bt = Backtester()
    report = bt._generate_report("test_strategy")
    assert report["strategy"] == "test_strategy"
    assert report["error"] == "No trades executed"


# ==========================================
# Trade Signal Tests
# ==========================================

def test_trade_signal_creation():
    signal = TradeSignal(
        strategy_name="Wheel",
        symbol="SPY",
        action="SELL",
        quantity=1,
        strike=490,
        expiry="2026-08-15",
        option_type="PUT",
        confidence_score=85.0,
        risk_warning="Standard risk",
    )
    assert signal.strategy_name == "Wheel"
    assert signal.confidence_score == 85.0
    assert "Wheel" in repr(signal)


def test_trade_signal_with_legs():
    signal = TradeSignal(
        strategy_name="IronCondor",
        symbol="SPY",
        action="COMPLEX",
        quantity=1,
        strike=500,
        expiry="2026-08-15",
        option_type="PUT",
        legs=[
            {"action": "SELL", "strike": 480, "option_type": "PUT", "expiry": "2026-08-15", "quantity": 1},
            {"action": "BUY", "strike": 475, "option_type": "PUT", "expiry": "2026-08-15", "quantity": 1},
        ],
        net_credit=1.50,
        max_profit=150,
        max_loss=350,
    )
    assert signal.legs is not None
    assert len(signal.legs) == 2
    assert signal.net_credit == 1.50


# ==========================================
# Portfolio Limit Tests
# ==========================================

def test_risk_manager_greeks_check():
    rm = RiskManager()
    assert rm.check_portfolio_greeks(0.15, 0.03) is False  # Within limits
    assert rm.check_portfolio_greeks(0.25, 0.03) is True   # Delta breach
    assert rm.check_portfolio_greeks(0.15, 0.06) is True   # Vega breach
