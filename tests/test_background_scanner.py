"""Regression tests for actionable background Brain notifications."""
import json
from datetime import datetime, timezone

import pytest

from agents.trade_engine import background_scanner as scanner_module
from agents.trade_engine.background_scanner import (
    _atm_iv,
    _no_trade_reason_code,
    is_market_hours,
)


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scanner_module, "SCAN_RESULTS_FILE", str(tmp_path / "results.json"))
    monkeypatch.setattr(scanner_module, "SCAN_NOTIFICATIONS_FILE", str(tmp_path / "notifications.json"))
    monkeypatch.setattr(scanner_module, "SCAN_STATE_FILE", str(tmp_path / "state.json"))
    return scanner_module.BackgroundBrainScanner(interval_seconds=300)


@pytest.mark.asyncio
async def test_persisted_no_trade_notifications_are_hidden(scanner):
    notifications = [
        {
            "id": "invalid",
            "best_strategy": "no_trade",
            "acknowledged": False,
        },
        {
            "id": "valid",
            "best_strategy": "bull_put_spread",
            "score": 80,
            "acknowledged": False,
        },
    ]
    with open(scanner_module.SCAN_NOTIFICATIONS_FILE, "w", encoding="utf-8") as handle:
        json.dump(notifications, handle)

    visible = await scanner.get_notifications(unacknowledged_only=True)

    assert [notification["id"] for notification in visible] == ["valid"]


@pytest.mark.asyncio
async def test_scan_records_unavailable_data_instead_of_emitting_signal(scanner, monkeypatch):
    async def unavailable(_symbol):
        return None, "option_chain_unavailable"

    monkeypatch.setattr(scanner, "_analyze_one", unavailable)

    new_count = await scanner.scan_once(symbols=["AAA", "BBB"])
    status = await scanner.get_status()

    assert new_count == 0
    assert status["symbols_scanned_last_run"] == 0
    assert status["last_results"]["symbols"] == {}
    assert status["last_results"]
    with open(scanner_module.SCAN_STATE_FILE, encoding="utf-8") as handle:
        state = json.load(handle)
    assert state["scan_diagnostics"]["skipped_symbols"] == {"option_chain_unavailable": 2}
    assert state["errors"] == ["2 symbols skipped: option chain unavailable"]


def test_no_trade_cannot_pass_new_trade_gate(scanner):
    assert not scanner._is_new_trade("MRK", -55, "strong_sell", "no_trade", "bullish")
    assert not scanner._is_new_trade(
        "XLU", -36, "sell", "put_debit_spread", "neutral"
    )
    assert scanner._is_new_trade(
        "SPY", 80, "strong_buy", "bull_put_spread", "bullish"
    )


# ── No-trade reason codes (diagnostics) ──────────────────────────────────


def test_no_trade_reason_code_classifies_gates():
    assert _no_trade_reason_code("no_trade", "Signal agreement is only 35% — insufficient confirmation") == "low_confidence"
    assert _no_trade_reason_code("no_trade", "IVR 60 but VIX term structure inverted") == "inverted_term_structure"
    assert _no_trade_reason_code("no_trade", "VIX 45 is extreme; wait for confirmation") == "high_vix"
    assert _no_trade_reason_code("no_trade", "earnings within 7 days") == "earnings_proximity"
    assert _no_trade_reason_code("no_trade", "No strategy has a sufficiently differentiated edge in the current regime") == "no_edge"
    assert _no_trade_reason_code("no_trade", "unrecognized reason text") == "other"
    assert _no_trade_reason_code("bull_put_credit", "some reasoning") == "bull_put_credit"
    # v1.6.0 high-win-rate gates must be tallied, not swallowed by "other".
    assert _no_trade_reason_code("no_trade", "Bullish signal but AMD is in a confirmed downtrend — do not sell puts into the knife") == "trend_mismatch"
    assert _no_trade_reason_code("no_trade", "Bearish signal but XYZ is in a confirmed uptrend — do not sell calls into strength") == "trend_mismatch"
    assert _no_trade_reason_code("no_trade", "AMD: relative strength -25% vs SPY — laggard, no directional premium") == "laggard"


# ── ATM IV extraction ────────────────────────────────────────────────────


def _opt(strike, iv, dte=30, delta=None, opt_type="call"):
    return {
        "strike": strike,
        "dte": dte,
        "option_type": opt_type,
        "implied_volatility": iv,
        "delta": delta,
    }


def test_atm_iv_uses_front_expiry_delta_fifty():
    chain = [
        _opt(100, 0.45, dte=7, delta=0.52),
        _opt(105, 0.50, dte=7, delta=0.48),
        _opt(110, 0.65, dte=7, delta=0.30),
        _opt(100, 0.55, dte=45, delta=0.50),  # back expiry ignored
    ]
    assert _atm_iv(chain) == pytest.approx(0.475, abs=0.001)


def test_atm_iv_uses_parity_when_deltas_missing():
    # No deltas: the strike where call IV ≈ put IV wins over far-OTM noise.
    chain = [
        _opt(100, 0.20, dte=7, opt_type="call"),
        _opt(100, 0.22, dte=7, opt_type="put"),
        _opt(140, 0.90, dte=7, opt_type="call"),  # far-OTM inflated IV
        _opt(140, 0.95, dte=7, opt_type="put"),
    ]
    assert _atm_iv(chain) == pytest.approx(0.21, abs=0.001)


def test_atm_iv_falls_back_to_front_expiry_median():
    chain = [
        _opt(100, 0.20, dte=7, opt_type="call"),
        _opt(120, 0.60, dte=7, opt_type="call"),
        _opt(80, 0.40, dte=7, opt_type="put"),
        _opt(90, 0.70, dte=45, opt_type="call"),  # back expiry ignored
    ]
    assert _atm_iv(chain) == pytest.approx(0.40, abs=0.001)


def test_atm_iv_returns_none_for_empty_chain():
    assert _atm_iv([]) is None


# ── Market-hours gating ──────────────────────────────────────────────────


def test_is_market_hours_true_on_a_weekday_mid_session():
    wednesday_10am_et = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    assert is_market_hours(wednesday_10am_et)


def test_is_market_hours_false_after_the_close():
    wednesday_8pm_et = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    assert not is_market_hours(wednesday_8pm_et)


def test_is_market_hours_false_on_a_weekend():
    saturday = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    assert not is_market_hours(saturday)


def test_is_market_hours_inclusive_of_open_and_close_boundaries():
    open_boundary = datetime(2026, 8, 12, 13, 30, tzinfo=timezone.utc)  # 9:30 ET
    close_boundary = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)  # 16:00 ET
    assert is_market_hours(open_boundary)
    assert is_market_hours(close_boundary)


def test_is_market_hours_false_on_christmas():
    christmas_midday = datetime(2026, 12, 25, 16, 0, tzinfo=timezone.utc)
    assert not is_market_hours(christmas_midday)


def test_is_market_hours_false_on_a_holiday_observed_for_a_weekend_date():
    # July 4, 2026 falls on a Saturday; NYSE observes the holiday on the
    # preceding Friday. A plain weekday check alone would miss this.
    july_3_friday = datetime(2026, 7, 3, 16, 0, tzinfo=timezone.utc)
    assert july_3_friday.weekday() == 4  # confirms this date is a Friday
    assert not is_market_hours(july_3_friday)


def test_is_market_hours_respects_a_half_day_early_close():
    # The day after Thanksgiving 2026 (Nov 27) is a half day: NYSE closes at
    # 1pm ET instead of 4pm. A plain 9:30-16:00 check would wrongly call
    # 2pm ET "open".
    half_day_morning = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)  # 11am ET
    half_day_afternoon = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)  # 2pm ET
    assert is_market_hours(half_day_morning)
    assert not is_market_hours(half_day_afternoon)


def test_is_market_hours_falls_back_when_calendar_lookup_errors(monkeypatch):
    def broken_schedule(*_args, **_kwargs):
        raise RuntimeError("calendar library unavailable")

    monkeypatch.setattr(scanner_module._NYSE_CALENDAR, "schedule", broken_schedule)
    scanner_module._schedule_cache.clear()

    wednesday_10am_et = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    wednesday_8pm_et = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    assert is_market_hours(wednesday_10am_et)
    assert not is_market_hours(wednesday_8pm_et)


@pytest.mark.asyncio
async def test_status_reports_live_market_open_flag(scanner, monkeypatch):
    monkeypatch.setattr(scanner_module, "is_market_hours", lambda: False)
    assert (await scanner.get_status())["market_open"] is False

    monkeypatch.setattr(scanner_module, "is_market_hours", lambda: True)
    assert (await scanner.get_status())["market_open"] is True


@pytest.mark.asyncio
async def test_skipping_a_closed_market_does_not_clobber_the_last_real_scan(scanner, monkeypatch):
    async def unavailable(_symbol):
        return None, "option_chain_unavailable"

    monkeypatch.setattr(scanner, "_analyze_one", unavailable)
    await scanner.scan_once(symbols=["AAA"])
    status_after_scan = await scanner.get_status()

    scanner._mark_skipped_for_closed_market()
    status_after_skip = await scanner.get_status()

    assert status_after_skip["last_run"] == status_after_scan["last_run"]
    assert status_after_skip["symbols_scanned_last_run"] == status_after_scan["symbols_scanned_last_run"]
    assert status_after_skip["last_closed_market_check"] is not None
