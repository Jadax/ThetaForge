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


def test_atm_iv_ignores_degenerate_preopen_ivs():
    # Pre-market/weekend CBOE snapshots carry near-zero IVs on every contract.
    # Averaging them produced iv_rank=0 and "very cheap vol" across the whole
    # universe; they must be ignored like missing values instead.
    chain = [
        _opt(100, 0.004, dte=7, delta=0.5),
        _opt(105, 0.003, dte=7, delta=0.45),
        _opt(110, 0.002, dte=7, delta=0.3),
    ]
    assert _atm_iv(chain) is None


def test_plausible_iv_bounds():
    assert scanner_module._plausible_iv(0.25)
    assert scanner_module._plausible_iv("0.35")
    assert not scanner_module._plausible_iv(0.0)
    assert not scanner_module._plausible_iv(0.004)
    assert not scanner_module._plausible_iv(None)
    assert not scanner_module._plausible_iv(9.9)


@pytest.mark.asyncio
async def test_degenerate_chain_skips_with_reason(scanner, monkeypatch):
    class FakeBrain:
        def analyze(self, **kwargs):  # must never be reached
            raise AssertionError("brain.analyze ran on a degenerate chain")

        def record_outcome(self, symbol, price):
            pass

    closes = [100.0 + i * 0.1 for i in range(60)]

    async def fake_price(_symbol):
        return 100.0

    async def fake_chain(_symbol):
        return [_opt(95, 0.003, dte=30), _opt(105, 0.002, dte=30)]

    async def fake_vix():
        return 20.0

    async def fake_hist(_symbol, period="1y"):
        return _hist_60(closes)

    async def fake_none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(scanner._provider, "get_stock_price", fake_price)
    monkeypatch.setattr(scanner._provider, "get_option_chain", fake_chain)
    monkeypatch.setattr(scanner._provider, "get_vix", fake_vix)
    monkeypatch.setattr(scanner._provider, "get_historical_prices", fake_hist)
    monkeypatch.setattr(scanner._provider, "get_next_earnings_date", fake_none)
    scanner._brain = FakeBrain()

    data, skip = await scanner._analyze_one("SPY")

    assert data is None
    assert skip == "iv_degenerate"


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

    nyse = scanner_module._get_nyse_calendar()
    monkeypatch.setattr(nyse, "schedule", broken_schedule)
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

    await scanner._mark_skipped_for_closed_market()
    status_after_skip = await scanner.get_status()

    assert status_after_skip["last_run"] == status_after_scan["last_run"]
    assert status_after_skip["symbols_scanned_last_run"] == status_after_scan["symbols_scanned_last_run"]
    assert status_after_skip["last_closed_market_check"] is not None


# ── Live-brain feeds: flow / put-call sentiment / GEX ──────────────────────
# The Brain's regime weights allocate real weight to flow, sentiment, and GEX
# buckets; the scanner must actually feed them (they were previously only set
# on the manual /brain/analyze path, leaving those weights inert in the scan).


def _flow_opt(strike, opt_type, volume=500, oi=100, bid=1.0, ask=1.5, last=1.2, dte=30):
    return {
        "strike": strike,
        "dte": dte,
        "option_type": opt_type,
        "volume": volume,
        "open_interest": oi,
        "bid": bid,
        "ask": ask,
        "last": last,
    }


def test_flow_data_aggregates_directional_flow():
    chain = [
        _flow_opt(95, "put", volume=600, oi=100),
        _flow_opt(105, "call", volume=400, oi=100),
        _flow_opt(110, "call", volume=50, oi=80),  # below min volume/OI -> ignored
    ]
    read = scanner_module._flow_data(chain, stock_price=100.0, iv=0.30)

    assert read is not None
    assert read["total_signals"] >= 2
    assert read["bullish_signals"] >= 1
    assert read["bearish_signals"] >= 1
    assert set(read) >= {"bias", "total_premium_bull", "total_premium_bear"}


def test_flow_data_none_when_no_unusual_activity():
    chain = [_flow_opt(100, "call", volume=5, oi=10)]
    assert scanner_module._flow_data(chain, stock_price=100.0, iv=0.30) is None
    assert scanner_module._flow_data([], stock_price=100.0, iv=0.30) is None


def test_pcr_read_computes_ratio_and_persists_daily(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner_module, "PCR_HISTORY_FILE", str(tmp_path / "pcr_history.json"))
    chain = [
        _flow_opt(95, "put", volume=300),
        _flow_opt(105, "call", volume=200),
    ]

    read = scanner_module._pcr_read("SPY", chain)

    assert read["put_volume"] == 300
    assert read["call_volume"] == 200
    assert read["current"] == pytest.approx(1.5, abs=1e-4)
    # One snapshot recorded (idempotent within the same day).
    assert len(read["historical"]) == 1

    again = scanner_module._pcr_read("SPY", chain)
    assert again["current"] == pytest.approx(1.5, abs=1e-4)
    assert len(again["historical"]) == 1


def test_pcr_read_degrades_to_oi_and_none():
    # No volume anywhere -> OI ratio.
    chain = [
        {**_flow_opt(95, "put", volume=0), "open_interest": 400},
        {**_flow_opt(105, "call", volume=0), "open_interest": 200},
    ]
    read = scanner_module._pcr_read("QQQ", chain)
    assert read["current"] == pytest.approx(2.0, abs=1e-4)

    assert scanner_module._pcr_read("QQQ", []) is None


def test_gex_data_returns_a_regime():
    chain = [
        _flow_opt(95, "put", oi=200, bid=0.5, ask=0.7, last=0.6, dte=30),
        _flow_opt(100, "put", oi=150, bid=1.0, ask=1.2, last=1.1, dte=30),
        _flow_opt(100, "call", oi=180, bid=1.1, ask=1.3, last=1.2, dte=30),
        _flow_opt(105, "call", oi=220, bid=0.6, ask=0.8, last=0.7, dte=30),
    ]
    read = scanner_module._gex_data(chain, stock_price=100.0)

    assert read is not None
    assert read["gex_regime"] in {"HIGH_POSITIVE_GEX", "HIGH_NEGATIVE_GEX", "NEUTRAL", "FLIP_ZONE"}
    assert read["net_gex"] == pytest.approx(read["total_call_gex"] + read["total_put_gex"], abs=0.02)

    assert scanner_module._gex_data([], stock_price=100.0) is None


def _hist_60(closes):
    """Minimal provider-history stub (a real DataFrame-shaped Close/High/Low)."""
    import pandas as pd
    return pd.DataFrame({"Close": closes, "High": closes, "Low": closes})


@pytest.mark.asyncio
async def test_analyze_one_feeds_flow_pcr_gex_to_the_brain(scanner, monkeypatch):
    from agents.trade_engine.ai_brain import BrainOutput, SignalStrength

    captured = {}
    outcomes = []

    class FakeBrain:
        def analyze(self, **kwargs):
            captured.update(kwargs)
            return BrainOutput(
                symbol="SPY",
                stock_price=100.0,
                overall_signal=SignalStrength.BUY,
                overall_score=60.0,
                confidence=60.0,
                best_strategy="bull_put_credit",
                best_strategy_reasoning="test reasoning",
                regime="bullish",
                iv_signal={"iv_rank": 55},
                sentiment_signal={"signal": "neutral", "confidence": 30},
                relative_strength=0.1,
            )

        def record_outcome(self, symbol, price):
            outcomes.append((symbol, price))

    closes = [100.0 + i * 0.1 for i in range(60)]
    async def fake_price(_symbol):
        return 100.0
    async def fake_chain(_symbol):
        return [_flow_opt(95, "put", volume=600), _flow_opt(105, "call", volume=400)]
    async def fake_vix():
        return 20.0
    async def fake_hist(_symbol, period="1y"):
        return _hist_60(closes)
    async def fake_none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(scanner._provider, "get_stock_price", fake_price)
    monkeypatch.setattr(scanner._provider, "get_option_chain", fake_chain)
    monkeypatch.setattr(scanner._provider, "get_vix", fake_vix)
    monkeypatch.setattr(scanner._provider, "get_historical_prices", fake_hist)
    monkeypatch.setattr(scanner._provider, "get_vix_term_structure", fake_none)
    monkeypatch.setattr(scanner._provider, "get_next_earnings_date", fake_none)
    monkeypatch.setattr(scanner._provider, "get_short_interest", fake_none)
    monkeypatch.setattr(scanner._provider, "get_earnings_dates", fake_none)
    monkeypatch.setattr(scanner_module, "_flow_data",
                        lambda chain, price, iv: {"bias": "bullish", "total_signals": 2})
    monkeypatch.setattr(scanner_module, "_pcr_read",
                        lambda symbol, chain, store=None: {"current": 1.5, "historical": [], "put_volume": 300, "call_volume": 200})
    monkeypatch.setattr(scanner_module, "_gex_data",
                        lambda chain, price: {"gex_regime": "NEUTRAL", "net_gex": 0.0})
    scanner._brain = FakeBrain()

    data, skip = await scanner._analyze_one("SPY")

    assert skip is None
    assert captured["flow_data"]["bias"] == "bullish"
    assert captured["pcr_data"]["current"] == 1.5
    assert captured["gex_data"]["gex_regime"] == "NEUTRAL"
    assert data["flow_bias"] == "bullish"
    assert data["pcr_signal"] == {"signal": "neutral", "confidence": 30}
    assert data["gex_regime"] == "NEUTRAL"
    # The scan feeds the feedback loop with the price it already fetched.
    assert outcomes == [("SPY", 100.0)]
