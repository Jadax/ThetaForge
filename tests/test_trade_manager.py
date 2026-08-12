"""Trade management engine (agents/trade_engine/trade_manager.py)."""
from agents.trade_engine.trade_manager import (
    OpenPosition,
    evaluate_position,
    portfolio_plan,
    PROFIT_TARGET,
    MANAGE_DTE,
    LOSS_TO_CREDIT_STOP,
    EARNINGS_EXIT_DAYS,
    MAX_POSITIONS,
    MAX_CAPITAL_SLICE_PCT,
)


def _position(**overrides):
    fields = dict(
        symbol="AAPL",
        strategy="bull_put",
        short_strike=200,
        long_strike=190,
        expiry="2026-09-18",
        credit_received=1.20,
        quantity=1,
        spot=210,
        dte=30,
        short_leg_value=None,
    )
    fields.update(overrides)
    return OpenPosition(**fields)


# ── position exits ────────────────────────────────────────────────────────

def test_take_profit_at_50_percent_of_max_credit():
    result = evaluate_position(_position(short_leg_value=0.50))
    assert result["action"] == "close_profit"
    assert result["profit_pct"] >= PROFIT_TARGET


def test_short_premium_target_is_the_documented_50_percent():
    assert PROFIT_TARGET == 0.50


def test_gamma_window_triggers_close_or_roll_at_21_dte():
    result = evaluate_position(_position(dte=MANAGE_DTE, short_leg_value=0.80))
    assert result["action"] == "close_time"
    assert result["urgency"] == "high"


def test_loss_to_credit_stop_at_twice_the_credit():
    result = evaluate_position(_position(short_leg_value=1.20 * LOSS_TO_CREDIT_STOP))
    assert result["action"] == "close_loss"
    assert result["loss_to_credit"] >= LOSS_TO_CREDIT_STOP


def test_pre_earnings_exit_for_short_premium():
    result = evaluate_position(
        _position(short_leg_value=1.0),
        days_to_earnings=EARNINGS_EXIT_DAYS,
    )
    assert result["action"] == "close_pre_earnings"


def test_tested_short_strike_flags_review_when_loss_inside_stop():
    result = evaluate_position(_position(spot=195, short_leg_value=1.0))
    assert result["action"] == "review_tested"
    assert "tested" in result["reason"]


def test_untested_winner_holds_toward_target():
    result = evaluate_position(_position(short_leg_value=0.85))
    assert result["action"] == "hold"
    assert result["urgency"] == "low"


def test_unmeasurable_position_holds_without_fabricated_metric():
    result = evaluate_position(_position(short_leg_value=None))
    assert result["action"] == "hold"
    assert result["profit_pct"] is None
    assert result["loss_to_credit"] is None


def test_profit_metric_uses_credit_not_strike_gamma():
    # 1.20 credit → 0.60 left in the short leg = 50% captured.
    result = evaluate_position(_position(short_leg_value=0.60))
    assert result["profit_pct"] == 0.5


# ── portfolio plan ────────────────────────────────────────────────────────

def test_portfolio_green_when_within_all_limits():
    plan = portfolio_plan(
        [{"symbol": "AAPL", "capital_required": 1000}],
        capital=100_000,
    )
    assert plan["can_open_new"] is True
    assert plan["violations"] == []
    assert plan["num_positions"] == 1


def test_portfolio_blocks_when_position_cap_reached():
    positions = [{"symbol": f"S{i}", "capital_required": 1000} for i in range(MAX_POSITIONS)]
    plan = portfolio_plan(positions, capital=100_000)
    assert plan["can_open_new"] is False
    assert any("positions" in violation for violation in plan["violations"])


def test_portfolio_blocks_when_symbol_slice_exceeds_capital_limit():
    slice_limit = 100_000 * MAX_CAPITAL_SLICE_PCT
    plan = portfolio_plan(
        [{"symbol": "AAPL", "capital_required": slice_limit + 1}],
        capital=100_000,
    )
    assert plan["can_open_new"] is False
    assert any("over-allocated" in violation for violation in plan["violations"])


def test_portfolio_drawdown_circuit_breaker():
    plan = portfolio_plan(
        [],
        capital=100_000,
        realized_pnl=-20_000,
        starting_capital=100_000,
    )
    assert plan["can_open_new"] is False
    assert any("circuit breaker" in violation for violation in plan["violations"])
    assert plan["realized_drawdown_pct"] == -0.20


def test_portfolio_weekly_capital_limit():
    plan = portfolio_plan(
        [],
        capital=100_000,
        weekly_capital_limit=5_000,
        weekly_capital_used=6_000,
    )
    assert plan["can_open_new"] is False
    assert any("weekly capital" in violation for violation in plan["violations"])
