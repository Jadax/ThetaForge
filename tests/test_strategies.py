"""
Test suite for ThetaForge production signal engines.
Tests GEX engine, technical indicators, risk management, IV metrics,
greeks, unusual activity, and portfolio limits.
"""
import pytest

from agents.risk_management.kelly_calculator import calculate_kelly, calculate_position_size
from agents.risk_management.portfolio_limits import RiskManager
from agents.volatility.iv_metrics import calculate_iv_rank, calculate_iv_percentile
from agents.volatility.greeks import calculate_greeks
from agents.flow_analysis.gex_engine import GEXEngine
from agents.flow_analysis.unusual_activity import UnusualActivityDetector


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
# Technical Indicators Tests
# ==========================================

def test_technical_indicators():
    import numpy as np
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
    import numpy as np
    import pandas as pd
    from agents.technical.indicators import TechnicalEngine

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


def test_risk_manager_greeks_check():
    rm = RiskManager()
    assert rm.check_portfolio_greeks(0.15, 0.03) is False  # Within limits
    assert rm.check_portfolio_greeks(0.25, 0.03) is True   # Delta breach
    assert rm.check_portfolio_greeks(0.15, 0.06) is True   # Vega breach


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
