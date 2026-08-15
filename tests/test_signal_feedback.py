"""Tests for the self-learning feedback loop (SignalTracker + AIBrain wiring)."""
from datetime import datetime, timedelta, timezone

import pytest

from agents.trade_engine import signal_tracker as tracker_module
from agents.trade_engine.signal_tracker import SignalTracker
from agents.trade_engine.ai_brain import AIBrain


def _point_files_at(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tracker_module, "SIGNAL_LOG_FILE", str(tmp_path / "signals.json"))
    monkeypatch.setattr(tracker_module, "SIGNAL_ACCURACY_FILE", str(tmp_path / "accuracy.json"))


def _seed_outcomes(tmp_path, monkeypatch, symbol="AAPL", count=30,
                   outcome_price=110.0, entry_price=100.0, backdate_days=6):
    """Record `count` due predictions whose realized move is UP (+10%).

    Each record carries a bullish flow read (+0.6) and a bearish iv read
    (-0.5), so after the up-move flow should score ~100% and iv ~0%.
    """
    _point_files_at(tmp_path, monkeypatch)
    tracker = SignalTracker()
    for _ in range(count):
        tracker.record_prediction(
            symbol=symbol, stock_price=entry_price, overall_signal="buy",
            overall_score=30, confidence=80, regime="neutral",
            best_strategy="bull_put_credit",
            signals=[
                {"source": "flow", "signal": "bullish", "strength": 0.6,
                 "confidence": 70, "weight": 0.2, "reasoning": ""},
                {"source": "iv", "signal": "bearish", "strength": -0.5,
                 "confidence": 70, "weight": 0.2, "reasoning": ""},
            ],
            days_to_outcome=5,
        )
    log = tracker._read_log()
    for record in log:
        record["timestamp"] = (
            datetime.now(timezone.utc) - timedelta(days=backdate_days)
        ).isoformat()
    tracker._write_log(log)
    assert tracker.record_outcome(symbol, outcome_price) == count
    return tracker


# ── blend math ───────────────────────────────────────────────────────────

def test_blended_weights_nudge_accurate_sources_and_renormalize():
    brain = AIBrain()
    base = dict(brain.REGIME_WEIGHTS["bullish"])
    brain._accuracy_cache = {
        "flow": {"accuracy_pct": 80.0, "total_predictions": 60},
        "iv": {"accuracy_pct": 20.0, "total_predictions": 12},
        # Below MIN_DYNAMIC_SAMPLES: too few outcomes to trust a nudge.
        "technical": {"accuracy_pct": 90.0, "total_predictions": 5},
    }
    brain._accuracy_cache_at = float("inf")

    effective, _ = brain._feedback_blended_weights(base)

    assert effective["flow"] > base["flow"]
    assert effective["iv"] < base["iv"]
    # Below MIN_DYNAMIC_SAMPLES the source is untouched -- it only moves by the
    # global renormalization, far less than an actually-nudged source.
    tech_drift = abs(effective["technical"] / base["technical"] - 1)
    flow_drift = abs(effective["flow"] / base["flow"] - 1)
    assert tech_drift < 0.05
    assert flow_drift > tech_drift * 2
    assert sum(effective.values()) == pytest.approx(sum(base.values()), abs=0.01)


def test_dynamic_drift_is_bounded():
    brain = AIBrain()
    base = brain.REGIME_WEIGHTS["neutral"]
    brain._accuracy_cache = {
        "flow": {"accuracy_pct": 100.0, "total_predictions": 1000},
        "iv": {"accuracy_pct": 0.0, "total_predictions": 1000},
    }
    brain._accuracy_cache_at = float("inf")

    effective, _ = brain._feedback_blended_weights(base)

    # A perfect or worthless history may move a weight only within the drift cap.
    assert effective["flow"] / base["flow"] > 1.2
    assert effective["iv"] / base["iv"] < 0.8


# ── full loop over real tracker files ────────────────────────────────────

def test_learned_accuracy_shifts_effective_weights_from_real_outcomes(tmp_path, monkeypatch):
    _seed_outcomes(tmp_path, monkeypatch, count=30)
    brain = AIBrain()
    base = dict(brain.REGIME_WEIGHTS["neutral"])

    effective, accuracy = brain._feedback_blended_weights(base)

    assert accuracy["flow"]["accuracy_pct"] > 90.0   # bullish strength, up-move
    assert accuracy["iv"]["accuracy_pct"] < 10.0     # bearish strength, up-move
    assert accuracy["flow"]["total_predictions"] == 30
    assert effective["flow"] > base["flow"]
    assert effective["iv"] < base["iv"]
    assert sum(effective.values()) == pytest.approx(sum(base.values()), abs=0.05)


def test_analyze_surfaces_signal_accuracy_and_dynamic_weights(tmp_path, monkeypatch):
    _seed_outcomes(tmp_path, monkeypatch, count=20)
    brain = AIBrain()
    rising = [100.0 + index * 0.5 for index in range(60)]

    output = brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=rising, high_prices=[101.0] * 60, low_prices=[99.0] * 60,
        current_iv=0.30, hv_20=0.18, vix=18.0,
        vix_term_structure={"VIX9D": 11, "VIX3M": 13},
    )

    assert output.signal_accuracy  # learned per-source accuracy is present
    assert output.dynamic_weights  # effective (blended) weights are present
    assert output.signal_accuracy["flow"]["accuracy_pct"] > 90.0
    base_flow = brain.REGIME_WEIGHTS[output.regime]["flow"]
    assert output.dynamic_weights["flow"] > base_flow


# ── recording gate ───────────────────────────────────────────────────────

def test_analyze_records_predictions_only_for_actionable_analyses(tmp_path, monkeypatch):
    _point_files_at(tmp_path, monkeypatch)
    brain = AIBrain()
    calls = []
    brain._record_feedback_prediction = lambda **kw: calls.append(kw)

    # Flat/quiet inputs: no informative signal, no strategy -> not a prediction.
    quiet = [100.0] * 20
    brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=quiet, high_prices=[100.0] * 20, low_prices=[100.0] * 20,
        current_iv=0.25, hv_20=0.25, vix=20.0,
        vix_term_structure={"VIX9D": 11, "VIX3M": 13},
        record_feedback=True,
    )
    assert calls == []

    rising = [100.0 + index * 0.5 for index in range(60)]
    brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=rising, high_prices=[101.0] * 60, low_prices=[99.0] * 60,
        current_iv=0.30, hv_20=0.18, vix=18.0,
        vix_term_structure={"VIX9D": 11, "VIX3M": 13},
        record_feedback=True,
    )
    assert len(calls) == 1
    assert calls[0]["symbol"] == "TEST"
    assert calls[0]["best_strategy"] == "bull_put_credit"
    assert calls[0]["all_signals"]

    # Feedback is opt-in; the default path must not write history.
    brain.analyze(
        symbol="TEST", stock_price=100.0, option_chain=[],
        historical_prices=rising, high_prices=[101.0] * 60, low_prices=[99.0] * 60,
        current_iv=0.30, hv_20=0.18, vix=18.0,
        vix_term_structure={"VIX9D": 11, "VIX3M": 13},
        record_feedback=False,
    )
    assert len(calls) == 1


# ── outcome scoring via the Brain ────────────────────────────────────────

def test_brain_record_outcome_scores_due_predictions(tmp_path, monkeypatch):
    _seed_outcomes(tmp_path, monkeypatch, count=3)
    brain = AIBrain()

    # A fresh prediction that is not due yet is left untouched.
    tracker = SignalTracker()
    tracker.record_prediction(
        symbol="AAPL", stock_price=100, overall_signal="buy", overall_score=20,
        confidence=75, regime="neutral", best_strategy="cash_secured_put",
        signals=[{"source": "technical", "strength": 0.5}], days_to_outcome=5,
    )
    assert brain.record_outcome("AAPL", 105) == 0

    log = tracker._read_log()
    log[-1]["timestamp"] = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    tracker._write_log(log)

    assert brain.record_outcome("AAPL", 105) == 1
    log = tracker._read_log()
    assert log[-1]["outcome_recorded"] is True
    assert log[-1]["actual_return_pct"] == 5.0


def test_feedback_fails_closed_when_tracker_is_broken(tmp_path, monkeypatch):
    _point_files_at(tmp_path, monkeypatch)
    (tmp_path / "signals.json").write_text("{ not json", encoding="utf-8")
    brain = AIBrain()
    base = brain.REGIME_WEIGHTS["neutral"]

    effective, accuracy = brain._feedback_blended_weights(base)

    assert effective == pytest.approx(base, abs=0.01)  # unblended base weights
    assert brain.record_outcome("AAPL", 105) == 0
