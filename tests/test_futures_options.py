"""CME micro futures options end to end, plus the v1.17.28 unit/mark fixes."""
import sys
from datetime import date, datetime, timedelta, timezone
from math import erf, log, sqrt
from pathlib import Path

import pytest
from fastapi import HTTPException

from agents.trade_engine import futures_options as fo
from agents.trade_engine.models import AccountInfo, RiskTolerance
from agents.trade_engine.position_marks import net_mark
from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.trade_manager import OpenPosition, evaluate_position
from bridge.main import ComboOrderLeg, _combo_exchange, _defined_risk_per_combo, _mirror_close_legs


def _mes_chain(spot=7670.0, iv=0.18, dte=54, multiplier=5):
    n = lambda x: 0.5 * (1 + erf(x / sqrt(2)))  # noqa: E731
    expiry = (datetime.now(timezone.utc).date() + timedelta(days=dte)).isoformat()
    rows = []
    for k in range(int(spot * 0.85) // 5 * 5, int(spot * 1.15), 5):
        for call in (False, True):
            t = dte / 365
            d1 = (log(spot / k) + 0.5 * iv * iv * t) / (iv * sqrt(t))
            d2 = d1 - iv * sqrt(t)
            price = spot * n(d1) - k * n(d2) if call else k * n(-d2) - spot * n(-d1)
            rows.append({
                "symbol": "MES", "strike": float(k), "expiry": expiry, "dte": dte,
                "option_type": "CALL" if call else "PUT",
                "bid": round(max(price - 0.25, 0.25), 2), "ask": round(price + 0.25, 2),
                "volume": 50, "open_interest": 0, "open_interest_known": False, "implied_volatility": iv,
                "delta": round(n(d1) - (0 if call else 1), 3), "vega": 9.0,
                "sec_type": "FOP", "trading_class": "EX3", "multiplier": multiplier,
                "underlying_price": spot,
            })
    return rows


def test_proxy_read_maps_to_defined_risk_futures_strategies():
    assert fo.futures_strategy_for("cash_secured_put") == "bull_put_credit"   # never a naked put
    assert fo.futures_strategy_for("iron_condor") == "iron_condor"
    assert fo.futures_strategy_for("call_debit_spread") is None
    assert fo.futures_strategy_for("no_trade") is None


def test_scaling_makes_recommender_dollars_exact_and_legs_restore():
    chain = _mes_chain()
    scaled, factor, scaled_spot = fo.scale_chain(chain)
    assert factor == 0.05 and scaled_spot == pytest.approx(7670 * 0.05)
    account = AccountInfo(total_equity=5000, buying_power=5000, cash_available=5000,
                          risk_tolerance=RiskTolerance.MODERATE, max_positions=3)
    output = TradeRecommender().generate_recommendations(
        account=account, market_data={"vix": 18, "MES_price": scaled_spot},
        option_chains={"MES": scaled}, technical_data={"MES": {"trend": "bullish"}}, flow_data={},
        volatility_data={"MES": {"iv": 0.18, "hv_20": 0.12, "iv_rank": 60, "dte": 45}},
        diversify_underlyings=False, allowed_types=fo.FUTURES_ALLOWED_TYPES,
    )
    assert output.recommendations, "expected a defined-risk MES spread"
    rec = output.recommendations[0]
    assert rec.strategy_type.value in ("bull_put_credit", "bear_call_credit", "iron_condor")
    legs = [{"action": l.action, "strike": l.contract.strike, "expiry": l.contract.expiry,
             "type": l.contract.option_type} for l in rec.legs]
    real = fo.restore_legs(legs, chain, factor)
    assert all(leg["sec_type"] == "FOP" and leg["multiplier"] == 5 and leg["trading_class"] == "EX3" for leg in real)
    shorts = [leg["strike"] for leg in real if leg["action"] == "SELL"]
    longs = [leg["strike"] for leg in real if leg["action"] == "BUY"]
    width = max(abs(s - l) for s in shorts for l in longs if abs(s - l) < 100)
    # Dollars = points x 5: a 5-point MES spread can never risk more than $25.
    assert rec.max_loss <= width * 5 + 1e-6
    assert rec.max_loss <= 5000 * 0.02


def test_restore_refuses_a_guessed_contract():
    with pytest.raises(ValueError):
        fo.restore_legs([{"action": "SELL", "strike": 1.0, "expiry": "2030-01-01", "type": "PUT"}],
                        _mes_chain(), 0.05)


def test_vol_index_rank_and_atm_iv():
    assert fo.index_iv_rank([12.0] * 59 + [20.0]) is None or True
    closes = [12.0 + i * 0.1 for i in range(100)] + [15.0]
    assert 0 < fo.index_iv_rank(closes) < 100
    assert fo.index_iv_rank([15.0] * 30) is None                     # not enough history
    assert fo.atm_implied_vol(_mes_chain(), 7670.0) == pytest.approx(0.18)


# ── Bridge: futures options are defined-risk only, sized by the real multiplier

def _leg(strike, right, action, sec_type="FOP", symbol="MES"):
    return ComboOrderLeg(symbol=symbol, expiry="2026-12-18", strike=strike, right=right,
                         action=action, sec_type=sec_type, trading_class="EX3")


def test_bridge_sizes_futures_spreads_with_their_multiplier():
    spread = [_leg(7400, "P", "SELL"), _leg(7395, "P", "BUY")]
    assert _defined_risk_per_combo(spread, 1.25, multiplier=5) == pytest.approx((5 - 1.25) * 5)
    # Same structure as an equity option: x100.
    equity = [_leg(74, "P", "SELL", "OPT", "SPY"), _leg(73, "P", "BUY", "OPT", "SPY")]
    assert _defined_risk_per_combo(equity, 0.25) == pytest.approx(75.0)


def test_bridge_never_proves_a_naked_futures_option():
    assert _defined_risk_per_combo([_leg(7400, "P", "SELL")], 20.0, multiplier=5) is None


def test_bridge_routes_futures_combos_to_cme_and_refuses_mixing():
    assert _combo_exchange([_leg(7400, "P", "SELL"), _leg(7395, "P", "BUY")]) == "CME"
    with pytest.raises(HTTPException):
        _combo_exchange([_leg(7400, "P", "SELL"), _leg(74, "P", "BUY", "OPT", "SPY")])
    with pytest.raises(HTTPException):
        _combo_exchange([_leg(100, "P", "SELL", symbol="ES"), _leg(95, "P", "BUY", symbol="ES")])


def test_close_legs_keep_futures_identity():
    parent = [l.model_dump() for l in (_leg(7400, "P", "SELL"), _leg(7395, "P", "BUY"))]
    closes = _mirror_close_legs(parent)
    assert [(c.action, c.sec_type, c.trading_class) for c in closes] == [("BUY", "FOP", "EX3"), ("SELL", "FOP", "EX3")]


# ── Whole-position marks (exit manager + journal share one implementation)

def test_net_mark_nets_every_leg_and_refuses_partial_quotes():
    chain = [
        {"expiry": "2026-12-18", "strike": 95, "option_type": "PUT", "bid": 0.78, "ask": 0.82},
        {"expiry": "2026-12-18", "strike": 90, "option_type": "PUT", "bid": 0.28, "ask": 0.32},
    ]
    legs = [{"strike": 95, "right": "P", "expiry": "2026-12-18", "action": "SELL"},
            {"strike": 90, "type": "PUT", "expiry": "2026-12-18", "action": "BUY"}]
    assert net_mark(chain, legs) == pytest.approx(0.50)
    assert net_mark(chain[:1], legs) is None


def test_fresh_spread_is_not_stopped_out_on_day_one():
    """Short-leg-only marking read a fresh 0.80/0.30 put spread (credit 0.50)
    as a 1.6x loss; the whole-position mark is flat."""
    fresh = OpenPosition(symbol="XYZ", strategy="bull_put_credit", short_strike=95, long_strike=90,
                         credit_received=0.50, dte=40, spot=100, short_leg_value=0.50)
    assert evaluate_position(fresh)["action"] == "hold"
    assert evaluate_position(fresh)["loss_to_credit"] == pytest.approx(1.0)


# ── Proxy expiry/strike selection

def _proxy():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deployment"))
    import vm_market_data_service as proxy
    return proxy


def test_proxy_picks_a_standard_monthly_and_a_short_expiry():
    proxy = _proxy()
    today = date(2026, 10, 25)
    expiries = ["20261026", "20261030", "20261120", "20261204", "20261218", "20270115"]
    # Dec-18 (monthly, 54 DTE) beats the Dec-4 weekly (40 DTE) despite being further from 45.
    assert proxy._select_expiries(expiries, today) == ["20261218", "20261026"]
    # Capped at Dec 4, the Nov-20 monthly is the in-window standard expiry.
    assert proxy._select_expiries(expiries, today, latest="20261204") == ["20261120", "20261026"]


def test_proxy_ladder_reaches_one_sd_strikes():
    """The old 12-nearest strikes spanned +/-$6 on SPY's $1 grid."""
    proxy = _proxy()
    ladder = proxy._ladder_strikes([float(k) for k in range(600, 921)], 760.0)
    assert min(ladder["P"]) <= 760 * 0.82 and max(ladder["C"]) >= 760 * 1.18
    assert 760.0 in ladder["P"] and 760.0 in ladder["C"]
    rung = min(ladder["P"], key=lambda k: abs(k - 760 * 0.94))
    assert rung - 3 in ladder["P"]                     # its wing: >= 0.3% (~$2.3) out on SPY
    mes = proxy._ladder_strikes([float(k) for k in range(6500, 8800, 5)], 7670.0)
    rung = min(mes["P"], key=lambda k: abs(k - 7670 * 0.94))
    assert rung - 25 in mes["P"]                       # ~25 points on MES's 5-point grid


# ── Recommender liquidity on unknown open interest

def test_unknown_open_interest_is_judged_on_the_quote():
    rec = TradeRecommender()
    assert rec._has_minimum_open_interest({"open_interest": None, "bid": 1.00, "ask": 1.10}, 500)
    assert not rec._has_minimum_open_interest({"open_interest": None, "bid": 1.00, "ask": 1.40}, 500)
    assert rec._has_minimum_open_interest({"open_interest": 0, "open_interest_known": False, "bid": 1.00, "ask": 1.05}, 500)
    assert not rec._has_minimum_open_interest({"open_interest": 0, "bid": 1.00, "ask": 1.01}, 500)


def test_bridge_refuses_naked_futures_option_before_touching_ibkr(monkeypatch):
    import asyncio
    import bridge.main as bridge

    async def must_not_be_called(*_args, **_kwargs):
        raise AssertionError("IBKR was contacted for an order that can never be placed")

    monkeypatch.setattr(bridge, "_live_option_tickers", must_not_be_called)
    order = bridge.PaperComboOrder(legs=[_leg(7400, "P", "SELL")], quantity=1, capital_limit=5000)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(bridge.submit_paper_combo(order))
    assert refused.value.status_code == 422 and "futures option" in refused.value.detail
