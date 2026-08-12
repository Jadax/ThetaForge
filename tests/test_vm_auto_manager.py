"""Tests for the autonomous paper position manager (VM-side exit loop)."""
import importlib.util
import os
from pathlib import Path

import httpx

MANAGER = Path(__file__).resolve().parent.parent / "deployment" / "vm_auto_manager.py"


def _load_manager():
    os.environ.setdefault("ADVISOR_URL", "http://advisor.test")
    os.environ.setdefault("ADVISOR_API_TOKEN", "advisor-token")
    os.environ.setdefault("BRIDGE_ACCESS_TOKEN", "bridge-token")
    spec = importlib.util.spec_from_file_location("vm_auto_manager", MANAGER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load_manager()


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=httpx.Response(self.status_code))


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def _entry(rec_id, **extra):
    record = {
        "id": rec_id,
        "recommendation_id": "rec-1",
        "strategy": "bull_put_credit",
        "symbol": "SPY",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 595, "right": "P", "action": "SELL"},
            {"symbol": "SPY", "expiry": "2026-08-21", "strike": 580, "right": "P", "action": "BUY"},
        ],
        "quantity": 1,
        "status": "Filled",
        "filled": 1,
        "net_credit": 300,
        "max_loss_total": 1500,
        "submitted_at": "2026-08-01T14:00:00Z",
    }
    record.update(extra)
    return record


def _close(rec_id, parent_id, realized_pnl=-250.0):
    return {
        "id": rec_id,
        "close_of": parent_id,
        "strategy": "bull_put_credit",
        "symbol": "SPY",
        "legs": [],
        "quantity": 1,
        "status": "Filled",
        "filled": 1,
        "realized_pnl": realized_pnl,
        "submitted_at": "2026-08-07T15:30:00Z",
    }


def test_is_manageable_tokens():
    assert m.is_manageable("iron_condor")
    assert m.is_manageable("cash_secured_put")
    assert m.is_manageable("covered_call")
    assert m.is_manageable("bull_put_credit")
    assert not m.is_manageable(None)
    assert not m.is_manageable("call_debit_spread")


def test_open_positions_filters_ledger():
    client = FakeClient([FakeResponse(200, {
        "capital_reserved": 1500,
        "orders": [
            _entry("open-1"),
            _entry("closed-1", closed_by="close-9"),
            _entry("cancelled-1", status="Cancelled"),
            _entry("unfilled-1", filled=0),
            _entry("norec-1", recommendation_id=None),
            _entry("debit-1", strategy="call_debit_spread"),
            _close("close-9", "closed-1", realized_pnl=-250.0),
        ],
    })])
    entries, realized, reserved = m.open_positions(client)
    assert [e["id"] for e in entries] == ["open-1"]
    assert realized == -250.0
    assert reserved == 1500.0


def test_short_and_long_strike_extraction():
    vertical = [
        {"action": "SELL", "strike": 595},
        {"action": "BUY", "strike": 580},
    ]
    assert m._short_strike(vertical, "bull_put_credit") == 595
    assert m._long_strike(vertical, "bull_put_credit") == 580
    iron_condor = [
        {"action": "BUY", "strike": 570},
        {"action": "SELL", "strike": 595},
        {"action": "SELL", "strike": 620},
        {"action": "BUY", "strike": 635},
    ]
    assert m._short_strike(iron_condor, "iron_condor") == 595
    assert m._long_strike(iron_condor, "iron_condor") == 570
    single = [{"action": "SELL", "strike": 90}]
    assert m._long_strike(single, "cash_secured_put") == 0


def test_build_position_inputs():
    record = _entry("open-1")
    inputs = m.build_position_inputs([record])
    assert len(inputs) == 1
    position = inputs[0]
    assert position["symbol"] == "SPY"
    assert position["short_strike"] == 595
    assert position["long_strike"] == 580
    assert position["credit_received"] == 300
    assert position["quantity"] == 1
    assert position["expiry"] == "2026-08-21"
    assert position["dte"] is None or position["dte"] >= 0


def test_submit_close_ok_and_rejection():
    ok_client = FakeClient([FakeResponse(200, {
        "status": "Submitted", "cost_to_close": 25.0, "realized_pnl": 275.0,
    })])
    ok, message = m.submit_close(ok_client, "open-1", "close_profit")
    assert ok and "close submitted" in message

    bad_client = FakeClient([FakeResponse(422, {"detail": "Position not filled"})])
    ok, message = m.submit_close(bad_client, "open-1", "close_time")
    assert not ok and "bridge rejected close" in message and "Position not filled" in message
