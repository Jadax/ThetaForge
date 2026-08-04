import importlib.util
from pathlib import Path

TE = Path(__file__).resolve().parent.parent / "agents" / "trade_engine" / "theoretical_edge.py"
HB = Path(__file__).resolve().parent.parent / "agents" / "trade_engine" / "historical_backtest.py"
BS = Path(__file__).resolve().parent.parent / "agents" / "trade_engine" / "background_scanner.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


theoretical_edge = _load(TE, "theoretical_edge")
historical_backtest = _load(HB, "historical_backtest")
background_scanner = _load(BS, "background_scanner")


def test_estimate_structure_value_credit_spread():
    # Two put legs, IV ~0.20, stock well above strikes so the short put is
    # rich relative to fair value (market pays more than model).
    legs = [
        {"action": "SELL", "option_type": "put", "strike": 595,
         "dte": 37, "iv": 0.20, "mid": 5.00},
        {"action": "BUY", "option_type": "put", "strike": 580,
         "dte": 37, "iv": 0.20, "mid": 3.00},
    ]
    result = theoretical_edge.estimate_structure_value(legs, stock_price=600)
    assert result is not None
    assert result["market_net"] == 2.0
    assert result["model_net"] > 0
    assert result["theoretical_edge_pct"] != 0


def test_estimate_structure_value_fails_closed_on_missing_data():
    assert theoretical_edge.estimate_structure_value([], stock_price=600) is None
    legs = [{"action": "SELL", "option_type": "put", "strike": 595,
             "dte": 37, "iv": None, "mid": 5.00}]
    assert theoretical_edge.estimate_structure_value(legs, stock_price=600) is None
    assert theoretical_edge.estimate_structure_value([{"a": 1}], None) is None


def test_credit_spread_pnl():
    # Untouched short put (expiry above short strike) pays full credit.
    assert historical_backtest.credit_spread_pnl(expiry_price=610, short_strike=595,
                                                 long_strike=580, credit=2.0) == 200.0
    # Breached but not through the long strike: some loss, not max.
    # (595-585)*100 = 1000 intrinsic on the short; P&L = 200 - 1000 = -800.
    net = historical_backtest.credit_spread_pnl(expiry_price=585, short_strike=595,
                                                long_strike=580, credit=2.0)
    assert net == -800
    # Full max loss at/through the long strike: width*100 - credit*100 = -1300.
    net_max = historical_backtest.credit_spread_pnl(expiry_price=575, short_strike=595,
                                                    long_strike=580, credit=2.0)
    assert net_max == -1300


def test_summarize_outcomes():
    stats = historical_backtest.summarize_outcomes([300, -800, 300, -800, 300])
    assert stats["n"] == 5
    assert stats["wins"] == 3
    assert stats["losses"] == 2
    assert stats["win_rate"] == 60.0
    assert stats["net_pnl"] == -700
    assert stats["profit_factor"] == 900.0 / 1600.0
    empty = historical_backtest.summarize_outcomes([])
    assert empty["n"] == 0 and empty["win_rate"] == 0.0


def test_backtest_credit_spread():
    events = [
        {"expiry_price": 610, "short_strike": 595, "long_strike": 580, "credit": 2.0},
        {"expiry_price": 575, "short_strike": 595, "long_strike": 580, "credit": 2.0},
    ]
    stats = historical_backtest.backtest_credit_spread(events)
    assert stats["n"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 50.0


def test_rv_band_helper():
    assert background_scanner._rv_band(0.30, 0.20) == "very_rich"
    assert background_scanner._rv_band(None, 0.20) is None


def test_flow_signals_from_chain():
    chain = [
        {"strike": 100, "volume": 9000, "open_interest": 50},
        {"strike": 110, "volume": 10, "open_interest": 200},
        {"strike": 120, "volume": 5, "open_interest": 50},
    ]
    signals = background_scanner._flow_signals(chain)
    assert signals is not None
    assert signals["hottest_strike"] == 100
    assert signals["unusual_volume"]["tier"] in {"extreme", "high", "elevated"}
    assert signals["oi_center_of_mass"]["center_of_mass"] > 100
    assert background_scanner._flow_signals([]) is None


def test_scan_galleries():
    results = {
        "SPY": {"iv_rank": 60, "strategy": "cash_secured_put",
                "rv_band": "rich",
                "flow_signals": {"unusual_volume": {"tier": "high"}},
                "expected_move_pct": 2.5},
        "QQQ": {"iv_rank": 20, "strategy": "no_trade",
                "rv_band": "cheap", "flow_signals": None,
                "expected_move_pct": 0.8},
    }
    assert "SPY" in background_scanner.gallery_symbols("wheel_candidates", results)
    assert "QQQ" not in background_scanner.gallery_symbols("wheel_candidates", results)
    assert "SPY" in background_scanner.gallery_symbols("premium_flow", results)
    assert "QQQ" not in background_scanner.gallery_symbols("premium_flow", results)
    assert "SPY" in background_scanner.gallery_symbols("earnings_window", results)
    assert background_scanner.gallery_symbols("does_not_exist", results) == []
    assert background_scanner.SCAN_GALLERIES["high_iv_movers"]["label"]
