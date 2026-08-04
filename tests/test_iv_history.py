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


def test_contango_detection():
    provider = FreeDataProvider()
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": 14}) is True
    assert provider.is_vix_contango({"VIX9D": 18, "VIX3M": 15, "VIX6M": 14}) is False
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": None}) is True
    assert provider.is_vix_contango({"VIX9D": 11, "VIX3M": 13, "VIX6M": 12}) is False
    assert provider.is_vix_contango({"VIX9D": None, "VIX3M": 13, "VIX6M": 14}) is None
