"""Regression tests for actionable background Brain notifications."""
import json

import pytest

from agents.trade_engine import background_scanner as scanner_module


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


def test_no_trade_cannot_pass_new_trade_gate(scanner):
    assert not scanner._is_new_trade("MRK", -55, "strong_sell", "no_trade", "bullish")
    assert not scanner._is_new_trade(
        "XLU", -36, "sell", "put_debit_spread", "neutral"
    )
    assert scanner._is_new_trade(
        "SPY", 80, "strong_buy", "bull_put_spread", "bullish"
    )
