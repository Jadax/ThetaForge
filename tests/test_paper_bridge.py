"""Safety checks for the local paper-only IBKR execution bridge."""
from datetime import datetime, timezone

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
