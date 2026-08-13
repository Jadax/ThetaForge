import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "add_trade.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("add_trade", SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


add_trade = _load_script()


def _entry(id_, opened, net_pnl, status="closed"):
    return {
        "id": id_,
        "symbol": "SPY",
        "opened": opened,
        "closed": opened,
        "status": status,
        "strategy": "bull_put_credit",
        "label": "paper",
        "legs": [{"action": "SELL", "type": "PUT", "strike": 595,
                  "expiry": "2026-08-21", "dte": 37}],
        "entry_ivr": 48,
        "dte_at_entry": 37,
        "capital_at_risk": 1500,
        "max_profit": 375,
        "net_pnl": net_pnl,
        "net_pnl_pct": 80.0,
        "reason": "Contango, IVR refill.",
        "research": [],
        "tags": ["theta"],
        "exit_note": "Closed at 80% of max profit.",
        "timestamp": "2026-08-01T00:00:00Z",
    }


def _fixture():
    return {
        "trader": {"name": "T", "handle": "@t", "tagline": "x"},
        "as_of": "2026-08-04",
        "account_equity": 25000,
        "trades": [
            _entry("TF-2026-001", "2026-05-01", 100),
            _entry("TF-2026-002", "2026-06-01", -50),
        ],
    }


@pytest.fixture
def journal_file(tmp_path):
    path = tmp_path / "trades.json"
    path.write_text(json.dumps(_fixture(), indent=2), encoding="utf-8")
    return path


def _closed_trade_args(journal_file, **extra):
    args = [
        "--journal", str(journal_file),
        "--non-interactive",
        "--symbol", "SPY",
        "--strategy", "bull_put_credit",
        "--status", "closed",
        "--leg", "SELL PUT 595 2026-08-21 37",
        "--leg", "BUY PUT 580 2026-08-21 37",
        "--entry-ivr", "48",
        "--dte-at-entry", "37",
        "--capital-at-risk", "1500",
        "--max-profit", "375",
        "--net-pnl", "300",
        "--reason", "Contango, IVR refill.",
        "--exit-note", "Closed at 80%.",
        "--tag", "theta",
    ]
    for key, value in extra.items():
        args += [f"--{key}", value]
    return args


def test_appends_trade_recomputes_metrics_and_updates_as_of(journal_file):
    code = add_trade.main(_closed_trade_args(journal_file))
    assert code == 0

    data = json.loads(journal_file.read_text(encoding="utf-8"))
    assert data["as_of"] == date.today().isoformat()
    assert len(data["trades"]) == 3
    trade = data["trades"][0]
    assert trade["id"] == "TF-2026-003"
    assert trade["status"] == "closed"
    assert trade["legs"] == [
        {"action": "SELL", "type": "PUT", "strike": 595.0,
         "expiry": "2026-08-21", "dte": 37},
        {"action": "BUY", "type": "PUT", "strike": 580.0,
         "expiry": "2026-08-21", "dte": 37},
    ]

    metrics = add_trade.compute_metrics(data["trades"])
    assert metrics["net_pnl"] == 350
    assert metrics["win_rate"] == pytest.approx(66.7, abs=0.1)
    assert metrics["profit_factor"] == pytest.approx(8.0)
    assert metrics["avg_win"] == pytest.approx(200.0)
    assert metrics["avg_loss"] == pytest.approx(50.0)
    assert metrics["max_drawdown"] == pytest.approx(50.0)
    assert metrics["streak"] == 1
    assert metrics["expectancy"] == pytest.approx(116.67, abs=0.1)
    assert metrics["drawdown_from_peak"] == pytest.approx(0.0)


def test_build_trade_carries_source_expected_move_and_management_plan(tmp_path):
    ledger_path = tmp_path / "paper_order_ledger.json"
    ledger = [{
        "id": "ledger-9",
        "strategy": "bull_put_credit",
        "symbol": "SPY",
        "legs": [
            {"symbol": "SPY", "action": "SELL", "right": "P",
             "strike": 595, "expiry": "2026-08-21"},
            {"symbol": "SPY", "action": "BUY", "right": "P",
             "strike": 580, "expiry": "2026-08-21"},
        ],
        "max_loss_total": 1500,
    }]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = tmp_path / "trades.json"
    journal_path.write_text(json.dumps(_fixture()), encoding="utf-8")

    args = [
        "--journal", str(journal_path),
        "--ledger", str(ledger_path),
        "--non-interactive",
        "--from-ledger", "ledger-9",
        "--status", "open",
        "--expected-move-pct", "2.4",
        "--entry-ivr", "58",
        "--max-profit", "300",
        "--net-pnl", "0",
        "--reason", "Rich IV, outside expected move.",
        "--management-plan",
        '{"target": "close at 50% of max credit", "event": "close before earnings"}',
    ]
    assert add_trade.main(args) == 0

    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["source"] == "ledger"
    assert trade["expected_move_pct"] == 2.4
    assert trade["management_plan"]["target"] == "close at 50% of max credit"
    assert trade["entry_ivr"] == 58


def test_from_ledger_prefills_legs_and_strategy(tmp_path):
    ledger_path = tmp_path / "paper_order_ledger.json"
    ledger = [{
        "id": "ledger-1",
        "strategy": "bear_call_credit",
        "symbol": "XLE",
        "legs": [
            {"symbol": "XLE", "action": "SELL", "right": "C",
             "strike": 97, "expiry": "2026-08-07"},
            {"symbol": "XLE", "action": "BUY", "right": "C",
             "strike": 103, "expiry": "2026-08-07"},
        ],
        "max_loss_total": 600,
    }]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    journal_path = tmp_path / "trades.json"
    journal_path.write_text(json.dumps(_fixture()), encoding="utf-8")

    args = [
        "--journal", str(journal_path),
        "--ledger", str(ledger_path),
        "--non-interactive",
        "--from-ledger", "ledger-1",
        "--status", "open",
        "--entry-ivr", "55",
        "--max-profit", "150",
        "--net-pnl", "0",
        "--reason", "Sold calls into a headline spike.",
    ]
    code = add_trade.main(args)
    assert code == 0

    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["symbol"] == "XLE"
    assert trade["strategy"] == "bear_call_credit"
    assert trade["status"] == "open"
    assert trade["source_id"] == "ledger-1"
    assert "label" not in trade
    assert trade["capital_at_risk"] == 600.0
    assert trade["legs"][0]["type"] == "CALL"
    assert trade["legs"][1]["type"] == "CALL"
    assert trade["legs"][0]["action"] == "SELL"
    assert trade["closed"] is None
    assert "Open" in trade["exit_note"]


def test_closed_trade_requires_exit_note(journal_file):
    args = _closed_trade_args(journal_file, **{"exit-note": ""})
    code = add_trade.main(args)
    assert code == 2
    data = json.loads(journal_file.read_text(encoding="utf-8"))
    assert len(data["trades"]) == 2


def test_unknown_strategy_is_rejected(journal_file):
    args = _closed_trade_args(journal_file, strategy="vertical_spread")
    code = add_trade.main(args)
    assert code == 2


def test_next_trade_id_handles_three_digits():
    trades = [
        {"id": "TF-2026-010"},
        {"id": "TF-2026-009"},
        {"id": "TF-2026-100"},
    ]
    assert add_trade.next_trade_id(trades) == "TF-2026-101"


def test_equity_trade_without_legs(journal_file):
    args = [
        "--journal", str(journal_file),
        "--non-interactive",
        "--asset-class", "equity",
        "--symbol", "NVDA",
        "--strategy", "equity_momentum",
        "--status", "closed",
        "--entry-price", "100",
        "--stop-price", "96",
        "--target-price", "108",
        "--capital-at-risk", "40",
        "--max-profit", "0",
        "--net-pnl", "28",
        "--reason", "Momentum long.",
        "--exit-note", "Auto-closed at the 2R target.",
    ]
    code = add_trade.main(args)
    assert code == 0

    data = json.loads(journal_file.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["asset_class"] == "equity"
    assert trade["legs"] == []
    assert trade["entry_price"] == 100.0
    assert trade["stop_price"] == 96.0
    assert trade["target_price"] == 108.0
    assert trade["capital_at_risk"] == 40.0


def test_equity_trade_requires_capital_at_risk(journal_file):
    args = [
        "--journal", str(journal_file),
        "--non-interactive",
        "--asset-class", "equity",
        "--symbol", "NVDA",
        "--strategy", "equity_momentum",
        "--status", "open",
        "--reason", "Momentum long.",
        "--exit-note", "Open — monitoring.",
    ]
    assert add_trade.main(args) == 2


def test_equity_from_ledger_prefills_prices(tmp_path):
    ledger_path = tmp_path / "paper_order_ledger.json"
    ledger = [{
        "id": "ledger-eq-1",
        "asset_class": "equity",
        "strategy": "equity_momentum",
        "symbol": "NVDA",
        "quantity": 10,
        "max_loss_total": 40.0,
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 108.0,
    }]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = tmp_path / "trades.json"
    journal_path.write_text(json.dumps(_fixture()), encoding="utf-8")

    args = [
        "--journal", str(journal_path),
        "--ledger", str(ledger_path),
        "--non-interactive",
        "--from-ledger", "ledger-eq-1",
        "--status", "open",
        "--max-profit", "0",
        "--net-pnl", "0",
        "--reason", "Momentum long from ledger.",
    ]
    assert add_trade.main(args) == 0

    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["asset_class"] == "equity"
    assert trade["legs"] == []
    assert trade["entry_price"] == 100.0
    assert trade["capital_at_risk"] == 40.0
