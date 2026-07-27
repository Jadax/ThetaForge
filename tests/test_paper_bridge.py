"""Safety checks for the local paper-only IBKR execution bridge."""

from bridge.main import ComboOrderLeg, _defined_risk_per_combo


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
