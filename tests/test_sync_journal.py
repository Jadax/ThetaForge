import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "sync_journal.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("sync_journal", SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_journal = _load_script()


def _record(rec_id, status="Filled", recommendation_id="rec-abc", **extra):
    record = {
        "id": rec_id,
        "recommendation_id": recommendation_id,
        "strategy": "bull_put_credit",
        "symbol": "SPY",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 595,
             "right": "P", "action": "SELL"},
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 580,
             "right": "P", "action": "BUY"},
        ],
        "quantity": 1,
        "status": status,
        "net_credit": 300,
        "max_loss_total": 1500,
        "submitted_at": "2026-08-01T14:00:00Z",
        "updated_at": "2026-08-01T14:00:00Z",
    }
    record.update(extra)
    return record


def _journal(tmp_path, trades):
    path = tmp_path / "trades.json"
    data = {
        "trader": {"name": "T", "handle": "@t", "tagline": "x"},
        "as_of": "2026-08-04",
        "account_equity": 25000,
        "trades": trades,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_rebuilds_from_ledger(tmp_path):
    ledger = [_record("rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    assert sync_journal.main(["--journal", str(journal_path),
                              "--ledger", str(ledger_path)]) == 0

    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(data["trades"]) == 1
    trade = data["trades"][0]
    assert trade["id"] == "rec-1"
    assert trade["source_id"] == "rec-1"
    assert trade["symbol"] == "SPY"
    assert trade["strategy"] == "bull_put_credit"
    assert trade["status"] == "open"
    assert trade["opened"] == "2026-08-01"
    assert trade["capital_at_risk"] == 1500.0
    assert trade["max_profit"] == 300.0
    assert trade["legs"] == [
        {"action": "SELL", "type": "PUT", "strike": 595.0,
         "expiry": "2026-08-21", "dte": 20},
        {"action": "BUY", "type": "PUT", "strike": 580.0,
         "expiry": "2026-08-21", "dte": 20},
    ]
    assert "paper account" in trade["reason"]
    assert trade["source"] == "ledger"
    assert trade["ledger_ref"] == "rec-1"
    assert trade["order"]["status"] == "Filled"
    assert trade["order"]["net_credit"] == 300
    assert trade["management_plan"]["target"]
    assert "verification" in data
    assert data["verification"]["entries_from_ledger"] == 1
    assert len(data["verification"]["ledger_sha"]) == 64


def test_excludes_cancelled_and_unrecommended(tmp_path):
    ledger = [
        _record("rec-filled", status="Filled"),
        _record("rec-cancelled", status="Cancelled"),
        _record("rec-norec", recommendation_id=None),
        _record("rec-inactive", status="Inactive"),
    ]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert [trade["id"] for trade in data["trades"]] == ["rec-filled"]


def test_overlays_narrative_by_source_id(tmp_path):
    ledger = [_record("rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    existing = [{
        "id": "rec-1",
        "source_id": "rec-1",
        "symbol": "SPY",
        "opened": "2026-08-01",
        "closed": "2026-08-03",
        "status": "closed",
        "strategy": "bull_put_credit",
        "legs": [],
        "entry_ivr": 48,
        "dte_at_entry": 37,
        "capital_at_risk": 1500,
        "max_profit": 375,
        "net_pnl": 300.0,
        "net_pnl_pct": 80.0,
        "reason": "Custom thesis.",
        "research": [{"label": "Chain", "url": "https://example.com"}],
        "tags": ["theta"],
        "exit_note": "Custom exit.",
        "timestamp": "2026-08-03T00:00:00Z",
    }]
    journal_path = _journal(tmp_path, existing)

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["reason"] == "Custom thesis."
    assert trade["exit_note"] == "Custom exit."
    assert trade["net_pnl"] == 300.0
    assert trade["status"] == "closed"
    assert trade["closed"] == "2026-08-03"
    assert trade["entry_ivr"] == 48
    assert trade["tags"] == ["theta"]
    assert trade["legs"][0]["strike"] == 595.0


def test_keeps_manual_entries_without_source_id(tmp_path):
    ledger = [_record("rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    manual = [{
        "id": "TF-2026-099",
        "symbol": "AAPL",
        "opened": "2026-07-01",
        "closed": None,
        "status": "open",
        "strategy": "covered_call",
        "legs": [],
        "entry_ivr": 33,
        "dte_at_entry": 22,
        "capital_at_risk": 25800,
        "max_profit": 1400,
        "net_pnl": 0.0,
        "net_pnl_pct": 0.0,
        "reason": "Manual entry.",
        "research": [],
        "tags": [],
        "exit_note": "Open.",
        "timestamp": "2026-07-01T00:00:00Z",
    }]
    journal_path = _journal(tmp_path, manual)

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    ids = {trade["id"] for trade in data["trades"]}
    assert ids == {"rec-1", "TF-2026-099"}


def test_drops_entries_whose_source_left_the_ledger(tmp_path):
    ledger = [_record("rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    stale = [{
        "id": "rec-gone", "source_id": "rec-gone", "symbol": "QQQ",
        "opened": "2026-06-01", "closed": None, "status": "open",
        "strategy": "iron_condor", "legs": [], "entry_ivr": 39,
        "dte_at_entry": 24, "capital_at_risk": 500, "max_profit": 130,
        "net_pnl": 0.0, "net_pnl_pct": 0.0, "reason": "x",
        "research": [], "tags": [], "exit_note": "Open.",
        "timestamp": "2026-06-01T00:00:00Z",
    }]
    journal_path = _journal(tmp_path, stale)

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert [trade["id"] for trade in data["trades"]] == ["rec-1"]


def test_empty_ledger_yields_empty_journal(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text("[]", encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert data["trades"] == []
    assert data["verification"]["entries_from_ledger"] == 0


def test_manual_entries_marked_manual_and_sha_changes(tmp_path):
    ledger = [_record("rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    manual = [{
        "id": "TF-2026-099", "symbol": "AAPL", "opened": "2026-07-01",
        "closed": None, "status": "open", "strategy": "covered_call",
        "legs": [], "entry_ivr": 33, "dte_at_entry": 22,
        "capital_at_risk": 25800, "max_profit": 1400, "net_pnl": 0.0,
        "net_pnl_pct": 0.0, "reason": "Manual entry.", "research": [],
        "tags": [], "exit_note": "Open.", "timestamp": "2026-07-01T00:00:00Z",
    }]
    journal_path = _journal(tmp_path, manual)

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    manual_entry = next(t for t in data["trades"] if t["id"] == "TF-2026-099")
    ledger_entry = next(t for t in data["trades"] if t["id"] == "rec-1")
    assert manual_entry["source"] == "manual"
    assert ledger_entry["source"] == "ledger"


def _close_record(rec_id, parent_id, reason="close_profit", realized_pnl=275.0, **extra):
    record = {
        "id": rec_id,
        "close_of": parent_id,
        "strategy": "bull_put_credit",
        "symbol": "SPY",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 595,
             "right": "P", "action": "BUY"},
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 580,
             "right": "P", "action": "SELL"},
        ],
        "quantity": 1,
        "status": "Filled",
        "net_credit": -25,
        "limit_price": 25,
        "cost_to_close": 25,
        "realized_pnl": realized_pnl,
        "reason": reason,
        "submitted_at": "2026-08-07T15:30:00Z",
        "updated_at": "2026-08-07T15:30:00Z",
    }
    record.update(extra)
    return record


def test_close_record_collapses_into_parent_entry(tmp_path):
    ledger = [_record("rec-1"), _close_record("close-1", "rec-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(data["trades"]) == 1
    trade = data["trades"][0]
    assert trade["id"] == "rec-1"
    assert trade["status"] == "closed"
    assert trade["closed"] == "2026-08-07"
    assert trade["net_pnl"] == 275.0
    assert trade["net_pnl_pct"] == round(275.0 / 1500.0, 4)
    assert trade["exit_note"] == "Auto-closed at the 50% of max-credit take-profit target."
    assert trade["close_order"]["net_credit"] == -25
    assert trade["close_order"]["status"] == "Filled"
    assert trade["close_order"]["realized_pnl"] == 275.0
    assert trade["timestamp"] == "2026-08-07T15:30:00Z"
    assert data["verification"]["entries_from_ledger"] == 2


def test_close_without_parent_is_standalone_entry(tmp_path):
    ledger = [_close_record("close-orphan", "rec-missing", reason="close_time")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(data["trades"]) == 1
    trade = data["trades"][0]
    assert trade["status"] == "closed"
    assert "parent entry was not found" in trade["reason"]


def test_close_exit_notes_map_all_management_reasons(tmp_path):
    cases = [
        ("close_loss", "Auto-closed at the 2x-credit loss stop."),
        ("close_time", "Auto-closed at the 21-DTE gamma management window."),
        ("close_pre_earnings", "Auto-closed before the earnings event."),
        ("close_pre_macro", "Auto-closed before the scheduled macro event (FOMC/CPI/NFP)."),
        ("managed_exit", "Closed by the ThetaForge management loop."),
    ]
    for reason, expected in cases:
        assert sync_journal._exit_note(reason) == expected


def _stock_record(rec_id, status="Filled", **extra):
    record = {
        "id": rec_id,
        "recommendation_id": "eq-rec-1",
        "asset_class": "equity",
        "strategy": "equity_momentum",
        "symbol": "NVDA",
        "quantity": 10,
        "status": status,
        "net_credit": 0,
        "max_loss_total": 40.0,
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 108.0,
        "highest_high": 100.0,
        "risk_per_share": 4.0,
        "submitted_at": "2026-08-01T14:00:00Z",
        "updated_at": "2026-08-01T14:00:00Z",
    }
    record.update(extra)
    return record


def test_equity_entry_carries_asset_class_and_plan(tmp_path):
    ledger = [_stock_record("eq-1")]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["asset_class"] == "equity"
    assert trade["instrument_type"] == "stock"
    assert trade["shares"] == 10
    assert trade["legs"] == []
    assert trade["entry_price"] == 100.0
    assert trade["stop_price"] == 96.0
    assert trade["target_price"] == 108.0
    assert trade["management_plan"]["stop"] == "hard stop at 96.0"
    assert trade["management_plan"]["target"] == "take profit at 2R target 108.0"
    assert "Long NVDA" in trade["reason"]


def test_etf_and_option_instrument_types(tmp_path):
    ledger = [
        _stock_record("eq-etf", symbol="SPY"),
        _record("rec-opt"),
    ]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    by_id = {trade["id"]: trade for trade in data["trades"]}
    assert by_id["eq-etf"]["instrument_type"] == "etf"
    assert by_id["rec-opt"]["instrument_type"] == "option"
    assert by_id["rec-opt"]["shares"] is None


def test_equity_close_folds_realized_pnl(tmp_path):
    close = _stock_record("eq-2-close", status="Filled", close_of="eq-2",
                          realized_pnl=28.0, cost_to_close=0.0,
                          reason="close_profit", average_fill_price=108.0,
                          updated_at="2026-08-08T15:30:00Z",
                          submitted_at="2026-08-08T15:30:00Z")
    ledger = [_stock_record("eq-2"), close]
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    journal_path = _journal(tmp_path, [])

    sync_journal.main(["--journal", str(journal_path),
                       "--ledger", str(ledger_path)])
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    trade = data["trades"][0]
    assert trade["status"] == "closed"
    assert trade["asset_class"] == "equity"
    assert trade["net_pnl"] == 28.0
    assert trade["exit_note"] == "Auto-closed at the 50% of max-credit take-profit target."


def test_equity_exit_notes_cover_trail_and_stop(tmp_path):
    assert sync_journal._exit_note("close_stop") == "Auto-closed at the 2x-ATR hard stop."
    assert sync_journal._exit_note("close_trail") == (
        "Auto-closed at the trailing chandelier stop after the position went +1R.")
