"""Tests for Tracks C/D/E: portfolio analytics, alert gallery + scan wiring,
and the standalone historical backtest endpoints.
"""
import json
import os
from datetime import datetime, timezone

import pytest


# ── Track C: portfolio analytics ──────────────────────────────────────────

from agents.trade_engine.portfolio_analytics import analyze_ledger, fold_ledger


def _sample_ledger():
    return [
        {"id": "E1", "strategy": "bull_put_credit", "symbol": "AAPL",
         "max_loss_total": 300, "submitted_at": "2026-06-01T09:00:00"},
        {"id": "E2", "strategy": "bull_put_credit", "symbol": "MSFT",
         "max_loss_total": 300, "submitted_at": "2026-07-01T09:00:00"},
        {"id": "E3", "strategy": "iron_condor", "symbol": "AAPL",
         "max_loss_total": 400, "submitted_at": "2026-07-20T09:00:00"},
        {"id": "C1", "close_of": "E1", "reason": "take_profit", "realized_pnl": 165,
         "submitted_at": "2026-07-15T09:00:00"},
        {"id": "C2", "close_of": "E2", "reason": "stop_loss", "realized_pnl": -300,
         "submitted_at": "2026-08-01T09:00:00"},
    ]


def test_fold_ledger_attaches_closes_to_parents():
    folded = fold_ledger(_sample_ledger())
    assert len(folded["entries"]) == 3
    assert len(folded["opens"]) == 1
    assert len(folded["closes"]) == 2
    assert folded["closes"][0]["symbol"] == "AAPL"
    assert folded["closes"][0]["realized_pnl"] == 165
    assert folded["closes"][1]["realized_pnl"] == -300


def test_fold_ledger_never_counts_close_rows_as_positions():
    folded = fold_ledger([{"id": "C1", "close_of": "E1", "realized_pnl": 10}])
    assert folded["entries"] == []
    assert folded["opens"] == []


def test_analyze_ledger_summary_math():
    result = analyze_ledger(_sample_ledger(), capital=10000)
    summary = result["summary"]
    assert summary["total_entries"] == 3
    assert summary["open_positions"] == 1
    assert summary["closed_positions"] == 2
    assert summary["realized_pnl"] == -135
    assert summary["win_rate"] == 50.0
    assert summary["expectancy"] == -67.5
    assert summary["open_risk"] == 400
    assert summary["open_risk_pct_of_equity"] == 4.0


def test_analyze_ledger_strategy_and_month_breakdown():
    result = analyze_ledger(_sample_ledger(), capital=10000)
    by_strategy = result["by_strategy"]
    assert by_strategy["bull_put_credit"]["trades"] == 2
    assert by_strategy["bull_put_credit"]["net_pnl"] == -135
    assert "iron_condor" not in by_strategy  # no closed iron condor trades yet
    assert result["by_month"]["2026-07"]["net_pnl"] == 165
    assert result["by_month"]["2026-08"]["net_pnl"] == -300


def test_analyze_ledger_concentration_violations():
    records = [
        {"id": f"E{i}", "strategy": "bull_put_credit", "symbol": "AAPL",
         "max_loss_total": 5000, "submitted_at": "2026-06-01T09:00:00"}
        for i in range(9)
    ]
    result = analyze_ledger(records, capital=10000)
    violations = result["concentration"]["violations"]
    assert any("positions at the 8 cap" in v for v in violations)
    assert any("over-allocated" in v for v in violations)


def test_analyze_ledger_empty_ledger_is_zeros():
    result = analyze_ledger([], capital=10000)
    assert result["summary"]["realized_pnl"] == 0
    assert result["summary"]["win_rate"] == 0.0
    assert result["summary"]["open_positions"] == 0
    assert result["equity_curve"] == []


# ── Track D: alerts (new types, check_one, gallery) ───────────────────────

from agents.trade_engine.alerts import (
    ALERT_GALLERY,
    AlertEngine,
    AlertPriority,
    AlertType,
    rule_from_template,
)


@pytest.fixture
def alert_engine(tmp_path, monkeypatch):
    import agents.trade_engine.alerts as alerts_module
    monkeypatch.setattr(alerts_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(alerts_module, "ALERTS_FILE", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(alerts_module, "ALERT_HISTORY_FILE", str(tmp_path / "history.json"))
    return AlertEngine()


def test_check_one_fires_score_above(alert_engine):
    alert_engine.add_rule("SPY", AlertType.SCORE_ABOVE, 70, "SPY hot")
    events = alert_engine.check_one("SPY", {"score": 82})
    assert len(events) == 1
    assert events[0]["current_value"] == 82
    # one_time rules fire once
    assert alert_engine.check_one("SPY", {"score": 85}) == []


def test_check_one_iv_percentile_below(alert_engine):
    alert_engine.add_rule("SPY", AlertType.IV_PERCENTILE_BELOW, 20, "cheap")
    assert len(alert_engine.check_one("spy", {"iv_percentile": 12})) == 1
    assert alert_engine.check_one("SPY", {"iv_percentile": 50}) == []


def test_check_one_gex_regime_matches_string_threshold(alert_engine):
    alert_engine.add_rule("SPY", AlertType.GEX_REGIME, "wall_below", "gamma flip")
    assert len(alert_engine.check_one("SPY", {"gex_regime": "wall_below"})) == 1
    assert alert_engine.check_one("SPY", {"gex_regime": "wall_above"}) == []


def test_check_one_pcr_fails_closed_on_missing_read(alert_engine):
    alert_engine.add_rule("SPY", AlertType.PCR_ABOVE, 1.5, "put-heavy")
    # missing pcr read -> fail closed, no alert
    assert alert_engine.check_one("SPY", {"pcr": None}) == []
    # once a real read above the threshold exists it fires
    assert len(alert_engine.check_one("SPY", {"pcr": 2.0})) == 1


def test_check_one_pcr_below(alert_engine):
    alert_engine.add_rule("SPY", AlertType.PCR_BELOW, 0.7, "call-heavy")
    assert len(alert_engine.check_one("SPY", {"pcr": 0.5})) == 1


def test_check_one_theoretical_edge_none_is_safe(alert_engine):
    alert_engine.add_rule("SPY", AlertType.THEORETICAL_EDGE_ABOVE, 1.0, "edge")
    assert alert_engine.check_one("SPY", {}) == []
    assert len(alert_engine.check_one("SPY", {"theoretical_edge_pct": 2.5})) == 1


def test_gallery_lists_all_new_types():
    template_ids = {t["template_id"] for t in ALERT_GALLERY}
    assert {"score_above", "iv_percentile_above", "gex_regime", "pcr_above",
            "theoretical_edge_above"} <= template_ids


def test_rule_from_template_uses_default_and_override():
    spec = rule_from_template("score_above", "aapl")
    assert spec["symbol"] == "AAPL"
    assert spec["alert_type"] == "score_above"
    assert spec["threshold"] == 70.0
    assert spec["priority"] == "high"
    override = rule_from_template("score_above", "aapl", 90)
    assert override["threshold"] == 90.0


def test_rule_from_template_unknown_raises():
    with pytest.raises(ValueError):
        rule_from_template("not_a_template", "SPY")


def test_scanner_emits_price_and_pcr_fields():
    # The scan result rows now carry price + raw pcr so alert rules have the
    # data they need; assert the wiring contract rather than run a scan.
    from agents.trade_engine import background_scanner as bs
    import inspect
    source = inspect.getsource(bs.BackgroundBrainScanner._analyze_one)
    assert '"price": price' in source
    assert '"pcr": (pcr_data or {}).get("current")' in source
    # Alerts evaluate once per pass over every analyzed symbol (in a worker
    # thread) instead of one file-read per symbol on the event loop.
    assert "asyncio.to_thread(self._run_alert_checks, alert_rows)" in inspect.getsource(bs.BackgroundBrainScanner.scan_once)
    # alert_rows holds a compact scalar projection (alert rules only read a
    # few keys), never the full chain/flow/gex/pcr payload, so the per-pass
    # alert bookkeeping cannot re-accumulate the whole universe in memory.
    assert "alert_rows[symbol] = _alert_projection(data)" in inspect.getsource(bs.BackgroundBrainScanner.scan_once)


# ── Track E: historical backtest ───────────────────────────────────────────

from agents.trade_engine.historical_backtest import (
    backtest_credit_spread_detailed,
    backtest_strategy_series,
)


def test_backtest_credit_spread_detailed_shape():
    events = [
        {"expiry_price": 150, "short_strike": 140, "long_strike": 135,
         "credit": 1.50, "expiry_date": "2026-06-19T00:00:00"},
        {"expiry_price": 120, "short_strike": 140, "long_strike": 135,
         "credit": 1.50, "expiry_date": "2026-07-17T00:00:00"},
    ]
    result = backtest_credit_spread_detailed(events)
    assert result["overall"]["n"] == 2
    assert result["overall"]["net_pnl"] == 150.0 + (-350.0)
    assert set(result["by_month"].keys()) == {"2026-06", "2026-07"}
    assert len(result["curve"]) == 2
    assert result["curve"][-1]["cumulative_pnl"] == -200.0


def test_backtest_strategy_series_is_proxy_labeled():
    closes = [100.0 + i * 0.5 for i in range(60)]
    result = backtest_strategy_series(closes, dte=10, otm_pct=0.02, width_pct=0.05)
    assert result["proxy"] is True
    assert result["overall"]["n"] == 50
    assert "credit_fraction" in result["assumptions"]
    assert len(result["events"]) == 50
    # an OTM put expiring above short strike should win the full credit
    assert result["events"][0]["pnl"] > 0


def test_backtest_strategy_series_insufficient_history():
    result = backtest_strategy_series([100.0, 101.0], dte=10)
    assert result["overall"]["n"] == 0
    assert "insufficient" in result["error"]


def test_backtest_strategy_series_call_side():
    # Bear call: strikes above spot; losing window when expiry exceeds short.
    rising = [100.0 + i * 1.5 for i in range(60)]
    result = backtest_strategy_series(rising, right="call", dte=10,
                                      otm_pct=0.02, width_pct=0.05)
    assert result["proxy"] is True
    assert any(row["pnl"] < 0 for row in result["events"])
