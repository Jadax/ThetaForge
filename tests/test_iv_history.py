"""Tests for the persistent IV history store and VIX contango helpers."""
import json

import pytest

from agents.volatility.iv_history import IVHistoryStore, MIN_SAMPLES
from agents.data_ingestion.free_data import FreeDataProvider


def _seed_history(tmp_path, symbol, ivs):
    """Write dated IV snapshots directly (bypasses daily-idempotency)."""
    store = IVHistoryStore(str(tmp_path / "iv_history.json"))
    entries = [
        {"date": f"2026-01-{index + 1:02d}", "iv": iv, "hv_20": 0.15}
        for index, iv in enumerate(ivs)
    ]
    with open(store.path, "w", encoding="utf-8") as handle:
        json.dump({symbol: entries}, handle)
    return store


def _seed_vrp_history(tmp_path, symbol, premiums):
    """Seed snapshots with explicit (iv, hv) pairs, controlling the VRP series."""
    store = IVHistoryStore(str(tmp_path / "iv_history.json"))
    entries = [
        {"date": f"2026-02-{index + 1:02d}", "iv": iv, "hv_20": hv}
        for index, (iv, hv) in enumerate(premiums)
    ]
    with open(store.path, "w", encoding="utf-8") as handle:
        json.dump({symbol: entries}, handle)
    return store


def test_record_and_rank(tmp_path):
    store = _seed_history(tmp_path, "AAPL", [0.20 + index * 0.01 for index in range(MIN_SAMPLES)])

    assert store.sample_count("AAPL") == MIN_SAMPLES
    assert store.iv_percentile("AAPL") is not None
    assert 0 <= store.iv_percentile("AAPL") <= 100


def test_thin_history_returns_none(tmp_path):
    store = _seed_history(tmp_path, "AAPL", [0.25])
    assert store.iv_rank("AAPL") is None
    assert store.iv_percentile("AAPL") is None


def test_record_is_daily_idempotent(tmp_path):
    store = IVHistoryStore(str(tmp_path / "iv_history.json"))
    store.record("AAPL", 0.30, 0.15)
    store.record("AAPL", 0.31, 0.15)
    assert store.sample_count("AAPL") == 1


def test_record_ignores_missing_iv(tmp_path):
    store = IVHistoryStore(str(tmp_path / "iv_history.json"))
    store.record("AAPL", None, 0.15)
    assert store.sample_count("AAPL") == 0


def test_iv_rank_uses_provided_current(tmp_path):
    store = _seed_history(tmp_path, "MSFT", [0.20 + index * 0.01 for index in range(MIN_SAMPLES)])
    # IV above the 52-week high yields a rank > 100 (IV expansion regime);
    # the value stays unclamped so the Brain's gates see a real extreme.
    rank = store.iv_rank("MSFT", current_iv=0.30)
    assert rank > 100
    assert store.iv_rank("MSFT", current_iv=0.20) == pytest.approx(0.0)


def test_52w_range(tmp_path):
    store = _seed_history(tmp_path, "NVDA", [0.20 + index * 0.01 for index in range(MIN_SAMPLES)])
    bounds = store.iv_52w_range("NVDA")
    assert bounds["iv_52w_high"] == pytest.approx(0.29)
    assert bounds["iv_52w_low"] == pytest.approx(0.20)


def test_vrp_zscore_thin_history_returns_none(tmp_path):
    store = _seed_vrp_history(tmp_path, "TSLA", [(0.30, 0.20)] * 3)
    assert store.vrp_zscore("TSLA") is None


def test_vrp_zscore_rich_current_premium(tmp_path):
    # Trailing VRP hovers near 0.05; today's premium is 0.15 → clearly rich.
    pairs = [(0.25, 0.20)] * (MIN_SAMPLES - 1) + [(0.30, 0.15)]
    store = _seed_vrp_history(tmp_path, "NVDA", pairs)
    z = store.vrp_zscore("NVDA")
    assert z is not None and z > 1.0


def test_vrp_zscore_flat_series_is_zero(tmp_path):
    store = _seed_vrp_history(tmp_path, "MSFT", [(0.30, 0.20)] * MIN_SAMPLES)
    assert store.vrp_zscore("MSFT") == 0.0


def test_vrp_zscore_accepts_explicit_current(tmp_path):
    pairs = [(0.25, 0.20)] * (MIN_SAMPLES - 1) + [(0.25, 0.18)]
    store = _seed_vrp_history(tmp_path, "AMD", pairs)
    z = store.vrp_zscore("AMD", current_iv=0.40, hv_20=0.20)
    assert z is not None and z > 1.0


def test_iv_change_5d(tmp_path):
    store = _seed_history(tmp_path, "META", [0.20, 0.22, 0.24, 0.26, 0.28, 0.30])
    assert store.iv_change_5d("META") == pytest.approx(0.50, abs=0.01)


def test_iv_change_5d_thin_history_returns_none(tmp_path):
    store = _seed_history(tmp_path, "COIN", [0.20] * 5)
    assert store.iv_change_5d("COIN") is None


def test_contango_detection():
    provider = FreeDataProvider()
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": 14}) is True
    assert provider.is_vix_contango({"VIX9D": 18, "VIX3M": 15, "VIX6M": 14}) is False
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": None}) is True
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": 12}) is False
    assert provider.is_vix_contango({"VIX9D": None, "VIX3M": 13, "VIX6M": 14}) is None
