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
    assert "TWS" in trade["reason"]
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
