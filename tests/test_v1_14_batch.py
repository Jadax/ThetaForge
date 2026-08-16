"""Tests for the v1.14 best-of-genre batch: P/L calculator, GEX heatmap,
alert webhooks, equity-momentum backtest, and strategy playbooks.
"""
import time

import pytest


# ── P/L calculator ─────────────────────────────────────────────────────────

from agents.trade_engine.pnl_calculator import calculate_pnl, pnl_at


def _bull_put():
    return [
        {"action": "SELL", "option_type": "put", "strike": 45, "entry_price": 1.50},
        {"action": "BUY", "option_type": "put", "strike": 40, "entry_price": 0.60},
    ]


def test_pnl_calculator_bull_put_profile():
    result = calculate_pnl(_bull_put(), 50, iv=0.30, dte=30)
    assert result["net_entry_per_share"] == 0.90
    assert result["max_profit"] == 90.0
    assert result["max_loss"] == -410.0
    assert result["breakevens"] == [44.1]
    assert result["pop_at_expiry"] > 80.0
    # curve spans both strike wings (full-loss zone reachable)
    assert min(p["spot"] for p in result["pnl_points"]) <= 40
    assert max(p["spot"] for p in result["pnl_points"]) >= 45


def test_pnl_calculator_custom_target_prices():
    result = calculate_pnl(_bull_put(), 50, target_prices=[35, 50, 60])
    points = result["pnl_points"]
    assert points[0]["pnl"] == -410.0  # deep below both strikes = full loss
    assert points[1]["pnl"] == 90.0    # above short strike = full credit
    assert points[2]["pnl"] == 90.0


def test_pnl_calculator_fails_closed():
    assert "error" in calculate_pnl([], 50)
    assert "error" in calculate_pnl(_bull_put(), 0)


def test_pnl_at_long_call():
    # Long call: -premium then +intrinsic; breakeven at strike + premium.
    legs = [{"action": "BUY", "option_type": "call", "strike": 100, "entry_price": 5.0}]
    assert pnl_at(legs, 100, 1) == -500.0
    assert pnl_at(legs, 105, 1) == 0.0
    assert pnl_at(legs, 110, 1) == 500.0


# ── GEX heatmap ────────────────────────────────────────────────────────────

from agents.flow_analysis.gex_engine import GEXEngine


def _fake_chain():
    chain = []
    for strike in (90, 95, 100, 105, 110):
        # Call-heavy OI -> net positive dealer GEX (asymmetric positioning).
        call_oi = 40000 if strike == 100 else 15000
        chain.append({"strike": strike, "option_type": "CALL", "open_interest": call_oi,
                      "last": 2.0, "implied_volatility": 0.3, "dte": 14, "expiry": "2026-08-30"})
        chain.append({"strike": strike, "option_type": "PUT", "open_interest": 3000,
                      "last": 2.0, "implied_volatility": 0.3, "dte": 14, "expiry": "2026-08-30"})
    return chain


def test_gex_heatmap_shape():
    gex_data = GEXEngine(underlying_price=100).calculate_chain_gex(_fake_chain(), 100)
    heat = GEXEngine().gex_heatmap(gex_data)
    assert len(heat["rows"]) >= 5
    assert all(r["strike"] <= 110 for r in heat["rows"])
    assert any(r["heat"] in ("hot", "elevated", "extreme") for r in heat["rows"])
    assert heat["walls"]["positive"] is not None
    assert heat["walls"]["negative"] is not None
    # rows are sorted ascending by strike
    strikes = [r["strike"] for r in heat["rows"]]
    assert strikes == sorted(strikes)


def test_gex_heatmap_empty_fails_closed():
    heat = GEXEngine().gex_heatmap({"strike_gex": {}, "underlying": 100})
    assert heat["rows"] == []
    assert heat["walls"]["positive"] is None


# ── Alert webhook ──────────────────────────────────────────────────────────

from agents.trade_engine.alerts import AlertEngine


@pytest.fixture
def webhook_engine(tmp_path, monkeypatch):
    import agents.trade_engine.alerts as alerts_module
    monkeypatch.setattr(alerts_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(alerts_module, "WEBHOOK_CONFIG_FILE", str(tmp_path / "webhook.json"))
    return AlertEngine()


def test_webhook_set_get_clear(webhook_engine):
    result = webhook_engine.set_webhook("https://discord.com/api/webhooks/x")
    assert result["configured"] is True
    config = webhook_engine.get_webhook()
    assert config["configured"] is True
    assert config["url"] == "https://discord.com/api/webhooks/x"
    cleared = webhook_engine.clear_webhook()
    assert cleared["enabled"] is False
    assert webhook_engine.get_webhook()["configured"] is False


def test_webhook_delivers_events(tmp_path, monkeypatch):
    import agents.trade_engine.alerts as alerts_module
    monkeypatch.setattr(alerts_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(alerts_module, "WEBHOOK_CONFIG_FILE", str(tmp_path / "webhook.json"))
    monkeypatch.setattr(alerts_module, "ALERTS_FILE", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(alerts_module, "ALERT_HISTORY_FILE", str(tmp_path / "history.json"))

    delivered = []
    import httpx
    def _fake_post(url, **kwargs):
        delivered.append((url, kwargs.get("json")))
        return True
    monkeypatch.setattr(httpx, "post", _fake_post)

    engine = AlertEngine()
    engine.set_webhook("https://example.test/hook")
    engine.add_rule("SPY", alerts_module.AlertType.SCORE_ABOVE, 70, "hot")
    assert len(engine.check_one("SPY", {"score": 80})) == 1
    time.sleep(0.2)
    assert len(delivered) == 1
    assert delivered[0][0] == "https://example.test/hook"
    assert delivered[0][1]["events"][0]["symbol"] == "SPY"


# ── Equity momentum backtest ───────────────────────────────────────────────

from agents.equity_trader.equity_backtest import backtest_momentum


def test_equity_backtest_uptrend_produces_trades():
    closes = [50 * (1 + 0.001 * i) + (i % 40) * 0.02 for i in range(400)]
    result = backtest_momentum(closes, rsi_max=70)
    assert result["proxy"] is True
    assert result["overall"]["n"] > 0
    assert result["overall"]["win_rate"] >= 0
    assert "net_pnl_pct" in result["overall"]
    assert len(result["trades"]) == result["overall"]["n"]


def test_equity_backtest_insufficient_history():
    result = backtest_momentum([100.0, 101.0, 102.0], rsi_max=70)
    assert "insufficient" in result.get("error", "")


# ── Strategy playbooks ─────────────────────────────────────────────────────

from agents.trade_engine.playbooks import get_playbook, list_playbooks


def test_playbooks_list_and_get():
    playbooks = list_playbooks()
    ids = {p["id"] for p in playbooks}
    assert {"bull_put_credit", "iron_condor", "wheel", "covered_call",
            "debit_spreads", "bear_call_credit", "0dte"} <= ids
    wheel = get_playbook("wheel")
    assert wheel["name"] == "The Wheel (CSP then Covered Call)"
    assert "mechanics" in wheel and "risk_warning" in wheel


def test_playbook_unknown_fails_closed():
    result = get_playbook("not_a_strategy")
    assert result.get("error") and result.get("found") is False
