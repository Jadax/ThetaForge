"""Tests for the desk analytics module (IV skew, earnings move edge)."""
import pandas as pd

from agents.volatility.desk_analytics import (
    calculate_iv_skew,
    implied_earnings_move,
    historical_earnings_moves,
    earnings_move_edge,
)

EXPIRY = "2026-09-18"


def _chain() -> list:
    """One-expiry chain with a pronounced put skew.

    Calls: 18Δ@0.26, 30Δ@0.28, 50Δ@0.30
    Puts:  -18Δ@0.42, -30Δ@0.40, -50Δ@0.31
    Interpolated 25Δ call ≈ 0.272, 25Δ put ≈ 0.408, ATM ≈ 0.305.
    """
    calls = [
        (120.0, 0.18, 0.26),
        (110.0, 0.30, 0.28),
        (100.0, 0.50, 0.30),
    ]
    puts = [
        (110.0, -0.70, 0.29),
        (100.0, -0.50, 0.31),
        (90.0, -0.30, 0.40),
        (80.0, -0.18, 0.42),
    ]
    chain = []
    for strike, delta, iv in calls:
        chain.append({
            "symbol": "TESTC", "strike": strike, "expiry": EXPIRY,
            "option_type": "CALL", "delta": delta, "implied_volatility": iv,
            "volume": 500, "open_interest": 1000,
        })
    for strike, delta, iv in puts:
        chain.append({
            "symbol": "TESTP", "strike": strike, "expiry": EXPIRY,
            "option_type": "PUT", "delta": delta, "implied_volatility": iv,
            "volume": 500, "open_interest": 1000,
        })
    return chain


def test_calculate_iv_skew_quantifies_rr25_and_bf25():
    skew = calculate_iv_skew(_chain())

    assert skew is not None
    assert skew["expiry"] == EXPIRY
    assert skew["regime"] == "fear"
    assert skew["rr25"] > 0  # puts richer than calls
    assert abs(skew["rr25"] - 0.1367) < 0.01
    assert abs(skew["bf25"] - 0.035) < 0.01
    assert skew["rr25_norm"] > skew["bf25_norm"] > 0
    assert "reasoning" in skew


def test_calculate_iv_skew_returns_none_without_deltas():
    chain = [
        {
            "symbol": "X", "strike": 100, "expiry": EXPIRY,
            "option_type": "CALL", "implied_volatility": 0.3,
        },
    ]
    assert calculate_iv_skew(chain) is None


def test_calculate_iv_skew_returns_none_for_empty_chain():
    assert calculate_iv_skew([]) is None


def test_calculate_iv_skew_returns_none_without_both_sides():
    chain = [opt for opt in _chain() if opt["option_type"] == "CALL"]
    assert calculate_iv_skew(chain) is None


def test_calculate_iv_skew_picks_most_traded_expiry():
    quiet = _chain()
    for opt in quiet:
        opt["expiry"] = "2027-01-15"
    noisy = _chain()
    for opt in noisy:
        opt["volume"] = 9_999
    skew = calculate_iv_skew(quiet + noisy)

    assert skew is not None
    assert skew["expiry"] == EXPIRY


def test_implied_earnings_move_from_front_straddle():
    chain = _chain()
    chain.append({
        "symbol": "TESTC", "strike": 100, "expiry": "2026-08-15",
        "option_type": "CALL", "delta": 0.5, "implied_volatility": 0.3,
        "bid": 2.0, "ask": 2.2, "last": 2.1,
    })
    chain.append({
        "symbol": "TESTP", "strike": 100, "expiry": "2026-08-15",
        "option_type": "PUT", "delta": -0.5, "implied_volatility": 0.3,
        "bid": 2.0, "ask": 2.2, "last": 2.1,
    })

    assert implied_earnings_move(chain, 100.0) == 4.2


def test_implied_earnings_move_requires_live_straddle():
    assert implied_earnings_move([], 100.0) is None
    assert implied_earnings_move(_chain(), 0) is None


def test_historical_earnings_moves():
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    prices = []
    for index in range(60):
        base = 100.0
        if index > 10:
            base *= 1.05  # +5% realized move after the day-10 event
        if index > 30:
            base *= 1.10  # +10% realized move after the day-30 event
        prices.append(round(base, 4))
    frame = pd.DataFrame({"Close": prices}, index=dates)

    # The day-45 "event" has no jump → filtered as a data glitch.
    moves = historical_earnings_moves(
        frame,
        [dates[10].date(), dates[30].date(), dates[45].date()],
    )

    assert len(moves) == 2
    assert abs(moves[0] - 5.0) < 0.1
    assert abs(moves[1] - 10.0) < 0.2


def test_historical_earnings_moves_ignores_missing_dates():
    dates = pd.date_range("2026-01-01", periods=10, freq="B")
    frame = pd.DataFrame({"Close": list(range(10, 20))}, index=dates)

    assert historical_earnings_moves(frame, [dates[-1].date()]) == []


def test_earnings_move_edge_sell_when_iv_rich():
    result = earnings_move_edge(5.0, [3.0, 4.0, 2.0, 3.5])

    assert result["read"] == "sell_iv"
    assert result["signal"] == "premium_sell_edge"
    assert result["edge_pct"] > 0


def test_earnings_move_edge_buy_when_iv_cheap():
    result = earnings_move_edge(2.0, [3.0, 4.0, 2.0, 3.5])

    assert result["read"] == "buy_iv"
    assert result["edge_pct"] < 0


def test_earnings_move_edge_requires_enough_history():
    assert earnings_move_edge(5.0, [3.0, 4.0]) is None
    assert earnings_move_edge(5.0, []) is None
