"""High-win-rate entry gates (agents/trade_engine/high_winrate.py)."""
from agents.trade_engine.high_winrate import (
    evaluate_entry,
    strategy_bias,
    is_premium_selling,
    trend_alignment_ok,
    expected_move_buffer_ok,
    entry_dte_ok,
    earnings_window_ok,
    relative_strength_ok,
)


# ── taxonomy ──────────────────────────────────────────────────────────────

def test_strategy_bias_classifies_directional_and_neutral_types():
    assert strategy_bias("bull_put") == "bull"
    assert strategy_bias("bull_put_credit") == "bull"
    assert strategy_bias("cash_secured_put") == "bull"
    assert strategy_bias("covered_call") == "bull"
    assert strategy_bias("call_debit_spread") == "bull"
    assert strategy_bias("bear_call") == "bear"
    assert strategy_bias("bear_call_credit") == "bear"
    assert strategy_bias("put_debit_spread") == "bear"
    assert strategy_bias("iron_condor") == "range"
    assert strategy_bias("") == "range"


def test_is_premium_selling_covers_sell_structures_only():
    assert is_premium_selling("bull_put")
    assert is_premium_selling("bear_call")
    assert is_premium_selling("iron_condor")
    assert is_premium_selling("cash_secured_put")
    assert is_premium_selling("covered_call")
    assert not is_premium_selling("call_debit_spread")
    assert not is_premium_selling("put_debit_spread")


# ── trend alignment ───────────────────────────────────────────────────────

def test_trend_alignment_blocks_bull_structures_in_downtrend():
    ok, reason = trend_alignment_ok("bull_put", "bearish")
    assert not ok and "downtrend" in reason


def test_trend_alignment_blocks_bear_structures_in_uptrend():
    ok, reason = trend_alignment_ok("bear_call", "bullish")
    assert not ok and "uptrend" in reason


def test_trend_alignment_allows_aligned_and_neutral_structures():
    assert trend_alignment_ok("bull_put", "bullish")[0]
    assert trend_alignment_ok("bull_put", "neutral")[0]
    assert trend_alignment_ok("bear_call", "bearish")[0]
    assert trend_alignment_ok("bear_call", "neutral")[0]
    assert trend_alignment_ok("iron_condor", "bearish")[0]  # neutral is agnostic


def test_trend_alignment_missing_trend_is_not_a_veto():
    assert trend_alignment_ok("bull_put", None)[0]


# ── expected-move buffer ──────────────────────────────────────────────────

def test_expected_move_buffer_rejects_short_strike_inside_1sd():
    ok, reason = expected_move_buffer_ok("bull_put", short_strike=195, spot=200, expected_move_1sd=10)
    assert not ok and "1-SD" in reason


def test_expected_move_buffer_accepts_strike_at_or_outside_1sd():
    assert expected_move_buffer_ok("bull_put", short_strike=190, spot=200, expected_move_1sd=10)[0]
    assert expected_move_buffer_ok("bull_put", short_strike=189, spot=200, expected_move_1sd=10)[0]


def test_expected_move_buffer_ignores_debit_structures():
    assert expected_move_buffer_ok("call_debit_spread", short_strike=205, spot=200, expected_move_1sd=10)[0]


def test_expected_move_buffer_missing_data_is_not_a_veto():
    assert expected_move_buffer_ok("bull_put", short_strike=None, spot=200, expected_move_1sd=None)[0]


# ── DTE band ──────────────────────────────────────────────────────────────

def test_entry_dte_blocks_sell_premium_inside_gamma_window():
    ok, reason = entry_dte_ok("bull_put", 20)
    assert not ok and "gamma" in reason


def test_entry_dte_blocks_sell_premium_beyond_capital_drag():
    ok, reason = entry_dte_ok("bear_call", 61)
    assert not ok


def test_entry_dte_accepts_the_tastytrade_band():
    assert entry_dte_ok("bull_put", 30)[0]
    assert entry_dte_ok("iron_condor", 45)[0]
    assert entry_dte_ok("bear_call", 21)[0]
    assert entry_dte_ok("bear_call", 60)[0]


def test_entry_dte_debit_floor_blocks_last_week_time_value():
    ok, reason = entry_dte_ok("call_debit_spread", 10)
    assert not ok
    assert entry_dte_ok("call_debit_spread", 14)[0]


# ── earnings blackout ─────────────────────────────────────────────────────

def test_earnings_blackout_blocks_short_premium_before_the_event():
    ok, reason = earnings_window_ok("bull_put", 3)
    assert not ok and "earnings" in reason


def test_earnings_blackout_ignores_debit_and_outside_window():
    assert earnings_window_ok("bull_put", 8)[0]
    assert earnings_window_ok("bull_put", None)[0]
    assert earnings_window_ok("call_debit_spread", 2)[0]  # earnings debit trades are legit


# ── relative strength (IBD "L") ───────────────────────────────────────────

def test_relative_strength_blocks_directional_premium_on_laggards():
    ok, reason = relative_strength_ok("bull_put", -0.25)
    assert not ok and "laggard" in reason


def test_relative_strength_accepts_leaders_and_neutral_structures():
    assert relative_strength_ok("bull_put", 0.10)[0]
    assert relative_strength_ok("iron_condor", -0.25)[0]  # range premium is agnostic
    assert relative_strength_ok("call_debit_spread", -0.25)[0]  # buyers can buy laggards
    assert relative_strength_ok("bull_put", None)[0]  # missing data is soft


# ── evaluate_entry ────────────────────────────────────────────────────────

def test_evaluate_entry_passes_a_textbook_high_winrate_candidate():
    ok, reason = evaluate_entry(
        "bull_put",
        trend="bullish",
        short_strike=180,
        spot=200,
        expected_move_1sd=15,
        dte=35,
        rs_126=0.05,
    )
    assert ok, reason


def test_evaluate_entry_first_failure_wins_and_is_specific():
    ok, reason = evaluate_entry(
        "bull_put",
        trend="bearish",
        short_strike=180,
        spot=200,
        expected_move_1sd=15,
        dte=35,
    )
    assert not ok and "trend_mismatch" in reason
