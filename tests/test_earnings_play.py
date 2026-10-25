"""Earnings IV-crush condor: Brain selection, Recommender structure, exits (v1.17.28)."""
from datetime import datetime, timedelta, timezone
from math import erf, exp, log, sqrt

from agents.trade_engine.ai_brain import AIBrain, SignalStrength
from agents.trade_engine.models import AccountInfo, RiskTolerance, StrategyType
from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.trade_manager import OpenPosition, evaluate_position


# ── Brain ────────────────────────────────────────────────────────────────

def _inputs(days_to_earnings, move=None, **overrides):
    args = dict(
        signal=SignalStrength.NEUTRAL, regime="neutral",
        iv_signal={"eff_iv_rank": 70, "earnings_move": move},
        sideways={"is_sideways": False}, vix=19, days_to_earnings=days_to_earnings,
        symbol="AAPL", existing_positions=[], confidence=20, trend="neutral",
        relative_strength=0.0, days_to_macro=None,
    )
    args.update(overrides)
    return args


RICH = {"implied_move_pct": 6.0, "median_historical_move_pct": 4.0, "read": "sell_iv", "events_used": 8}


def test_rich_implied_move_the_day_before_earnings_is_an_iv_crush_condor():
    result = AIBrain()._select_best_strategy(**_inputs(1, RICH))
    assert result["strategy"] == "earnings_iron_condor"
    assert result["reason_code"] == "earnings_iv_crush"


def test_earnings_play_needs_a_rich_move_and_the_right_window():
    brain = AIBrain()
    fair = dict(RICH, implied_move_pct=4.2)                     # 1.05x: not rich enough
    assert brain._select_best_strategy(**_inputs(1, fair))["strategy"] == "avoid_new_positions"
    assert brain._select_best_strategy(**_inputs(5, RICH))["strategy"] == "avoid_new_positions"
    assert brain._select_best_strategy(**_inputs(1, None))["strategy"] == "avoid_new_positions"


def test_earnings_today_is_vetoed():
    """days_to_earnings == 0 used to skip the earnings veto entirely (falsy)."""
    result = AIBrain()._select_best_strategy(**_inputs(0, RICH))
    assert result["strategy"] == "avoid_new_positions"


def test_market_vetoes_still_block_the_earnings_play():
    brain = AIBrain()
    assert brain._select_best_strategy(**_inputs(1, RICH, vix=33))["strategy"] == "avoid_new_positions"
    inverted = _inputs(1, RICH)
    inverted["iv_signal"] = dict(inverted["iv_signal"], term_structure="inverted")
    assert brain._select_best_strategy(**inverted)["strategy"] == "avoid_new_positions"


# ── Recommender ──────────────────────────────────────────────────────────

def _bs(S, K, T, v, call):
    n = lambda x: 0.5 * (1 + erf(x / sqrt(2)))  # noqa: E731
    d1 = (log(S / K) + 0.5 * v * v * T) / (v * sqrt(T))
    d2 = d1 - v * sqrt(T)
    return S * n(d1) - K * n(d2) if call else K * n(-d2) - S * n(-d1)


def _chain(spot, iv, dte, step, half_spread=0.02):
    expiry = (datetime.now(timezone.utc).date() + timedelta(days=dte)).isoformat()
    rows = []
    strike = spot * 0.75
    while strike <= spot * 1.25:
        k = round(strike / step) * step
        for call in (False, True):
            price = _bs(spot, k, dte / 365, iv, call)
            rows.append({
                "strike": k, "option_type": "CALL" if call else "PUT", "expiry": expiry, "dte": dte,
                "bid": round(max(price - half_spread, 0.01), 2), "ask": round(price + half_spread, 2),
                "volume": 900, "open_interest": 4000, "iv": iv, "delta": 0.2, "vega": 0.01,
            })
        strike += step
    return rows


def _recommend(chain, spot, mode="standard", vol=None, equity=20000):
    account = AccountInfo(total_equity=equity, buying_power=equity, cash_available=equity,
                          risk_tolerance=RiskTolerance.MODERATE, max_positions=3)
    return TradeRecommender().generate_recommendations(
        account=account, market_data={"vix": 19, "XYZ_price": spot},
        option_chains={"XYZ": chain}, technical_data={"XYZ": {"trend": "neutral"}},
        flow_data={}, volatility_data={"XYZ": vol}, diversify_underlyings=False, mode=mode,
    ).recommendations


def test_earnings_mode_sells_a_condor_outside_the_implied_move():
    # Pre-earnings inflated IV; the implied move is the chain's own straddle
    # (~0.8 x IV x sqrt(T) = 7.5%), and a liquid weekly's $0.01 half-spread.
    spot, iv = 50.0, 0.90
    chain = _chain(spot, iv, dte=4, step=0.5, half_spread=0.01)
    vol = {"iv": iv, "hv_20": 0.35, "iv_rank": 80, "dte": 4,
           "earnings_implied_move_pct": 7.5, "days_to_earnings": 1}
    recs = _recommend(chain, spot, mode="earnings", vol=vol)
    assert recs, "expected an earnings condor"
    rec = recs[0]
    assert rec.strategy_type == StrategyType.EARNINGS_IRON_CONDOR
    shorts = sorted(leg.contract.strike for leg in rec.legs if leg.action == "SELL")
    assert shorts[0] <= spot - 3.75 and shorts[1] >= spot + 3.75   # outside the 7.5% implied move
    assert rec.max_loss <= 20000 * 0.02                            # within the risk budget


def test_earnings_mode_refuses_shorts_inside_the_true_move():
    """An implied move understated vs the chain's IV puts the shorts inside
    the real move (POP < 55%): nothing qualifies."""
    chain = _chain(50.0, 0.9, dte=4, step=0.5, half_spread=0.01)
    vol = {"iv": 0.9, "hv_20": 0.35, "iv_rank": 80, "dte": 4,
           "earnings_implied_move_pct": 4.0, "days_to_earnings": 1}
    assert _recommend(chain, 50.0, mode="earnings", vol=vol) == []


def test_earnings_mode_needs_an_implied_move_and_a_print_date():
    chain = _chain(50.0, 0.9, dte=4, step=0.5)
    vol = {"iv": 0.9, "hv_20": 0.35, "iv_rank": 80, "dte": 4}
    assert _recommend(chain, 50.0, mode="earnings", vol=vol) == []


def test_standard_condor_shorts_sit_at_the_expected_move():
    """The condor used to sell the strikes adjacent to ATM (computing the
    expected move and never using it), so every condor failed the 1-SD gate."""
    recommender = TradeRecommender()
    spot, iv, dte = 100.0, 0.40, 45
    chain = _chain(spot, iv, dte, step=1.0)
    puts = sorted((r for r in chain if r["option_type"] == "PUT"), key=lambda r: r["strike"])
    calls = sorted((r for r in chain if r["option_type"] == "CALL"), key=lambda r: r["strike"])
    nvrp = {"iv": iv, "hv_20": 0.30, "iv_rank": 70, "vix": 20, "trend": "neutral"}
    expected_move = recommender.analytics.expected_move(spot, iv, dte)["expected_move_1sd"]
    cand = recommender._score_iron_condor("XYZ", spot, puts, calls, dte, None, {}, {}, nvrp, 400, 2000,
                                          min_credit_to_width=0.0)
    assert cand is not None
    assert cand["put_short"] <= spot - expected_move
    assert cand["call_short"] >= spot + expected_move
    assert cand["put_short"] - cand["put_long"] == 1.0           # narrowest wing


# ── Exit rules ───────────────────────────────────────────────────────────

def _condor(strategy="earnings_iron_condor", dte=6, mark=0.60):
    return OpenPosition(symbol="AAPL", strategy=strategy, short_strike=95, long_strike=94,
                        credit_received=0.50, dte=dte, spot=100, short_leg_value=mark)


def test_earnings_condor_closes_after_the_print_not_before():
    assert evaluate_position(_condor(), days_to_earnings=1)["action"] == "hold"
    assert evaluate_position(_condor(), days_to_earnings=0)["action"] == "hold"     # print tonight
    assert evaluate_position(_condor(), days_to_earnings=88)["action"] == "close_post_earnings"


def test_earnings_condor_is_exempt_from_the_21_dte_and_pre_earnings_exits():
    # A standard condor at 6 DTE is closed for gamma; the event trade is not.
    assert evaluate_position(_condor("iron_condor"), days_to_earnings=None)["action"] == "close_time"
    assert evaluate_position(_condor(), days_to_earnings=1)["action"] == "hold"
    assert evaluate_position(_condor(dte=1), days_to_earnings=None)["action"] == "close_time"


def test_earnings_condor_keeps_profit_and_loss_stops():
    assert evaluate_position(_condor(mark=0.20), days_to_earnings=1)["action"] == "close_profit"
    assert evaluate_position(_condor(mark=1.10), days_to_earnings=1)["action"] == "close_loss"
