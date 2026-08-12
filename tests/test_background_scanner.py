"""Regression tests for actionable background Brain notifications."""
import json
from datetime import datetime, timezone

import pytest

from agents.trade_engine import background_scanner as scanner_module
from agents.trade_engine.background_scanner import is_market_hours


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
