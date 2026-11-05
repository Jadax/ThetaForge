"""Safety checks for the local paper-only IBKR execution bridge."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from bridge.main import (
    ComboOrderLeg,
    _current_week_key,
    _defined_risk_per_combo,
    _mirror_close_legs,
    _reserved_capital,
    _same_leg_set,
)


def test_defined_risk_vertical_uses_worst_case_loss():
    legs = [
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=90, right="C", action="SELL"),
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=91, right="C", action="BUY"),
    ]
    assert _defined_risk_per_combo(legs, 0.10) == 90.0


def test_iron_condor_uses_wider_wing_for_max_loss():
    legs = [
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=95, right="P", action="BUY"),
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=97, right="P", action="SELL"),
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=103, right="C", action="SELL"),
        ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=104, right="C", action="BUY"),
    ]
    assert _defined_risk_per_combo(legs, 0.25) == 175.0


def test_single_leg_automation_requires_cash_secured_put_or_covered_call():
    cash_secured_put = [ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=90, right="P", action="SELL")]
    naked_long_call = [ComboOrderLeg(symbol="SPY", expiry="2026-08-14", strike=90, right="C", action="BUY")]
    assert _defined_risk_per_combo(cash_secured_put, 0.10) == 8990.0
    assert _defined_risk_per_combo(naked_long_call, -0.10) is None


def test_weekly_capital_reserves_open_and_filled_orders_only():
    week = _current_week_key(datetime(2026, 7, 29, tzinfo=timezone.utc))
    records = [
        {"week_key": week, "status": "Submitted", "max_loss_total": 250},
        {"week_key": week, "status": "Filled", "max_loss_total": 175},
        {"week_key": week, "status": "Cancelled", "max_loss_total": 100},
        {"week_key": "2026-W29", "status": "Submitted", "max_loss_total": 999},
    ]
    assert _reserved_capital(records, week) == 425


def test_reserved_capital_releases_closed_positions_and_ignores_close_orders():
    week = _current_week_key(datetime(2026, 7, 29, tzinfo=timezone.utc))
    records = [
        {"week_key": week, "status": "Filled", "max_loss_total": 250},
        {"week_key": week, "status": "Filled", "max_loss_total": 175, "closed_by": "close-1"},
        {"week_key": week, "status": "Submitted", "max_loss_total": 999, "close_of": "entry-1"},
    ]
    assert _reserved_capital(records, week) == 250


def test_mirror_close_legs_reverses_every_action():
    parent = [
        {"symbol": "SPY", "expiry": "2026-08-14", "strike": 90, "right": "C", "action": "SELL"},
        {"symbol": "SPY", "expiry": "2026-08-14", "strike": 91, "right": "C", "action": "BUY"},
    ]
    close = _mirror_close_legs(parent)
    assert [(leg.action, leg.strike) for leg in close] == [("BUY", 90.0), ("SELL", 91.0)]
    assert _same_leg_set(close, parent)
    assert not _same_leg_set([close[0]], parent)


def test_equity_records_reserve_stop_defined_risk():
    """A long equity entry reserves shares x (entry - stop) like an options
    max loss, so both engines draw from the same weekly capital ledger."""
    week = _current_week_key(datetime(2026, 7, 29, tzinfo=timezone.utc))
    records = [
        {"week_key": week, "status": "Filled", "asset_class": "equity",
         "shares": 10, "entry_price": 100.0, "stop_price": 98.0,
         "max_loss_total": 20.0},
        {"week_key": week, "status": "Submitted", "max_loss_total": 250},
    ]
    assert _reserved_capital(records, week) == 270


def test_equity_close_releases_reservation_and_ignores_close_order():
    week = _current_week_key(datetime(2026, 7, 29, tzinfo=timezone.utc))
    records = [
        {"week_key": week, "status": "Filled", "asset_class": "equity",
         "max_loss_total": 80.0},
        {"week_key": week, "status": "Filled", "asset_class": "equity",
         "max_loss_total": 40.0, "closed_by": "close-1"},
        {"week_key": week, "status": "Submitted", "asset_class": "equity",
         "max_loss_total": 999.0, "close_of": "entry-1"},
    ]
    assert _reserved_capital(records, week) == 80


# ── Order-state reconciliation and marketable limits (v1.17.27) ────────────

from datetime import date  # noqa: E402

from bridge.main import _marketable_limit, _reconcile_lapsed_orders  # noqa: E402


def test_marketable_limit_buffers_only_stale_quotes():
    assert _marketable_limit(100.0, "BUY", "live") == 100.0
    assert _marketable_limit(100.0, "BUY", "delayed") == 100.5
    assert _marketable_limit(100.0, "SELL", "delayed") == 99.5   # a stop sell must fill


def test_lapsed_day_orders_resolve_instead_of_freezing():
    today = date(2026, 10, 23)
    records = [
        {"id": "a", "status": "Submitted", "filled": 0, "submitted_at": "2026-10-20T13:51:48+00:00"},
        {"id": "b", "status": "Submitted", "filled": 3, "quantity": 5, "submitted_at": "2026-10-20T14:00:00+00:00"},
        {"id": "c", "status": "Submitted", "filled": 0, "submitted_at": "2026-10-23T14:00:00+00:00"},
    ]
    assert _reconcile_lapsed_orders(records, today)
    assert records[0]["status"] == "Inactive"                      # stops reserving capital
    assert records[1]["status"] == "Filled" and records[1]["quantity"] == 3
    assert records[2]["status"] == "Submitted"                     # today's order still working


def test_unfilled_close_unlinks_parent_so_exit_is_retried():
    """A stop-loss sell that expired unfilled must not leave the position
    marked closed (held at IBKR, invisible to the exit manager)."""
    parent = {"id": "p", "status": "Filled", "filled": 10, "closed_by": "x", "closed_at": "t",
              "submitted_at": "2026-10-20T14:00:00+00:00"}
    close = {"id": "x", "close_of": "p", "status": "Cancelled", "filled": 0,
             "submitted_at": "2026-10-22T19:00:00+00:00"}
    assert _reconcile_lapsed_orders([parent, close], date(2026, 10, 23))
    assert "closed_by" not in parent
    assert close["unlinked"] is True
    # A filled close keeps its parent closed.
    filled_parent = {"id": "q", "closed_by": "y"}
    filled_close = {"id": "y", "close_of": "q", "status": "Filled", "filled": 10}
    _reconcile_lapsed_orders([filled_parent, filled_close], date(2026, 10, 23))
    assert filled_parent["closed_by"] == "y"


# ── v1.17.32: a bought (debit) spread closes for a credit ───────────────────

def _close_harness(monkeypatch, parent, bids_asks):
    import asyncio
    import bridge.main as bridge
    from types import SimpleNamespace

    records = [parent]
    placed = {}

    async def connected():
        return None

    async def tickers(legs):
        return [SimpleNamespace(conId=i + 1) for i in range(len(legs))], list(bids_asks)

    class FakeIB:
        def placeOrder(self, contract, order):
            placed["order"] = order
            return SimpleNamespace(order=SimpleNamespace(orderId=7),
                                   orderStatus=SimpleNamespace(status="Submitted", filled=0, remaining=1, avgFillPrice=0))

    monkeypatch.setattr(bridge, "ensure_connected", connected)
    monkeypatch.setattr(bridge, "ib", FakeIB())
    monkeypatch.setattr(bridge, "_sync_order_ledger", lambda: records)
    monkeypatch.setattr(bridge, "_live_option_tickers", tickers)
    monkeypatch.setattr(bridge, "_bid_ask_usable", lambda t: t)
    monkeypatch.setattr(bridge, "_contract_multiplier", lambda q: 100)
    monkeypatch.setattr(bridge, "_snapshot_quality", lambda t: "live")
    monkeypatch.setattr(bridge, "_write_order_ledger", lambda r: None)
    request = bridge.CloseComboRequest(ledger_id=parent["id"], reason="close_profit", capital_limit=5000)
    return asyncio.run(bridge.close_paper_combo(request, None)), placed


def _spread_parent(net_credit, strategy):
    return {"id": "p1", "symbol": "NCLH", "strategy": strategy, "net_credit": net_credit, "filled": 1,
            "quantity": 1, "max_loss_per_combo": 45.0,
            "legs": [{"symbol": "NCLH", "expiry": "2026-11-20", "strike": 18.0, "right": "C", "action": "BUY"},
                     {"symbol": "NCLH", "expiry": "2026-11-20", "strike": 18.5, "right": "C", "action": "SELL"}]}


def test_debit_spread_closes_for_a_credit(monkeypatch):
    # Close = SELL 18C (bid 0.60), BUY 18.5C (ask 0.25): receives 0.35 for a 0.45 debit.
    result, placed = _close_harness(monkeypatch, _spread_parent(-0.45, "call_debit_spread"),
                                    [(0.60, 0.65), (0.20, 0.25)])
    assert result["net_credit"] == 0.35 and result["limit_price"] == -0.35
    assert placed["order"].action == "BUY" and placed["order"].lmtPrice == -0.35
    assert result["realized_pnl"] == -10.0          # (-0.45 + 0.35) x 100


def test_credit_spread_close_that_would_pay_is_still_refused(monkeypatch):
    with pytest.raises(HTTPException):
        _close_harness(monkeypatch, _spread_parent(0.30, "bear_call_credit"), [(0.60, 0.65), (0.20, 0.25)])
