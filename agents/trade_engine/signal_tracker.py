"""
Signal Performance Tracker.
Records Brain predictions and tracks actual outcomes to dynamically
adjust signal weights based on historical accuracy.

This is the SELF-IMPROVING component of the AI Brain, modeled on how
institutional desks track signal decay and rebalance signal weights.

Architecture:
1. Every Brain analysis is logged with timestamp, signals, scores
2. After N days, fetch actual price movement
3. Calculate hit rate per signal source
4. Feed accuracy data back into Brain for weight adjustment
"""
import json
import os
import math
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SIGNAL_LOG_FILE = os.path.join(DATA_DIR, "signal_history.json")
SIGNAL_ACCURACY_FILE = os.path.join(DATA_DIR, "signal_accuracy.json")

# The scanner runs several Brain analyses concurrently; the read-modify-write
# on the JSON history is serialized so one cycle can never lose another's rows.
_LOCK = threading.Lock()

# A directional read below this strength is treated as no-read (never credited
# or faulted) -- mirrors the Brain's INFORMATIVE_STRENGTH_EPS.
NEUTRAL_STRENGTH_EPS = 0.05


@dataclass
class SignalRecord:
    """A single recorded Brain analysis."""
    timestamp: str
    symbol: str
    stock_price: float
    overall_signal: str
    overall_score: float
    confidence: float
    regime: str
    best_strategy: str
    signals: List[Dict] = field(default_factory=list)
    price_at_outcome: Optional[float] = None
    days_to_outcome: int = 5
    outcome_recorded: bool = False
    was_correct: Optional[bool] = None
    actual_return_pct: Optional[float] = None


class SignalTracker:
    """
    Tracks Brain signal accuracy over time.
    
    Usage:
        tracker = SignalTracker()
        tracker.record_prediction(symbol, price, signal, score, ...)
        # After 5 days:
        tracker.record_outcome(symbol, new_price)
        accuracy = tracker.get_accuracy_by_source()
    """

    # Outcomes measured at these horizons
    OUTCOME_HORIZONS = [5, 10, 20, 45]  # trading days

    def __init__(self):
        self._ensure_files()

    def _ensure_files(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        for f in [SIGNAL_LOG_FILE, SIGNAL_ACCURACY_FILE]:
            if not os.path.exists(f):
                with open(f, "w") as fh:
                    json.dump([], fh)

    def _read_log(self) -> List[Dict]:
        with open(SIGNAL_LOG_FILE, "r") as f:
            return json.load(f)

    def _write_log(self, data: List[Dict]):
        with open(SIGNAL_LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _read_accuracy(self) -> Dict:
        with open(SIGNAL_ACCURACY_FILE, "r") as f:
            return json.load(f)

    def _write_accuracy(self, data: Dict):
        with open(SIGNAL_ACCURACY_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def record_prediction(
        self,
        symbol: str,
        stock_price: float,
        overall_signal: str,
        overall_score: float,
        confidence: float,
        regime: str,
        best_strategy: str,
        signals: List[Dict],
        days_to_outcome: int = 5,
    ):
        """Record a Brain prediction for later evaluation."""
        with _LOCK:
            log = self._read_log()

            record = SignalRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=symbol.upper(),
                stock_price=stock_price,
                overall_signal=overall_signal,
                overall_score=overall_score,
                confidence=confidence,
                regime=regime,
                best_strategy=best_strategy,
                signals=signals,
                days_to_outcome=days_to_outcome,
            )

            log.append(asdict(record))

            # Keep last 5000 records to avoid file bloat
            if len(log) > 5000:
                log = log[-5000:]

            self._write_log(log)

    def record_outcome(self, symbol: str, current_price: float):
        """
        Record the outcome for pending predictions on this symbol.
        Evaluates all un-outcome'd predictions that are old enough.
        """
        with _LOCK:
            log = self._read_log()
            updated = 0
            now = datetime.now(timezone.utc)

            for record in log:
                if record["outcome_recorded"]:
                    continue
                if record["symbol"] != symbol.upper():
                    continue

                pred_time = datetime.fromisoformat(record["timestamp"])
                days_elapsed = (now - pred_time).days

                if days_elapsed < record["days_to_outcome"]:
                    continue

                # Calculate outcome
                entry_price = record["stock_price"]
                ret_pct = (current_price - entry_price) / entry_price * 100

                # Was the signal correct?
                signal = record["overall_signal"]
                if signal in ("strong_buy", "buy", "weak_buy"):
                    was_correct = ret_pct > 0
                elif signal in ("strong_sell", "sell", "weak_sell"):
                    was_correct = ret_pct < 0
                else:
                    was_correct = abs(ret_pct) < 2.0  # Neutral is "correct" if stock didn't move much

                record["price_at_outcome"] = current_price
                record["outcome_recorded"] = True
                record["was_correct"] = was_correct
                record["actual_return_pct"] = round(ret_pct, 2)
                updated += 1

            self._write_log(log)
            return updated

    def get_accuracy_by_source(self, min_samples: int = 5) -> Dict[str, Dict]:
        """
        Calculate accuracy per signal source.

        Each source is judged on its OWN directional read, not the composite
        call: a source whose strength agrees with the realized move (sign of
        strength == sign of realized return) is a hit. Neutral reads (strength
        near zero) are data-absence, so they are neither credited nor faulted.
        Legacy rows without a per-signal strength fall back to the composite
        `was_correct`.

        Returns: {"cpr": {"correct": 15, "total": 20, "accuracy": 75.0}, ...}
        """
        log = self._read_log()
        source_stats: Dict[str, Dict] = {}

        for record in log:
            if not record.get("outcome_recorded"):
                continue
            ret = record.get("actual_return_pct")
            if ret is None:
                continue
            overall_correct = bool(record.get("was_correct"))

            for sig in record.get("signals", []):
                src = sig.get("source", "unknown")
                stats = source_stats.setdefault(
                    src, {"correct": 0, "total": 0, "returns": []}
                )
                strength = sig.get("strength")
                if strength is None:
                    correct = overall_correct
                elif abs(float(strength)) < NEUTRAL_STRENGTH_EPS:
                    continue
                else:
                    correct = (float(strength) > 0) == (ret > 0)

                stats["total"] += 1
                if correct:
                    stats["correct"] += 1
                stats["returns"].append(ret)

        # Calculate metrics
        result = {}
        for src, stats in source_stats.items():
            if stats["total"] < min_samples:
                continue
            acc = stats["correct"] / stats["total"] * 100
            avg_ret = sum(stats["returns"]) / len(stats["returns"]) if stats["returns"] else 0
            result[src] = {
                "accuracy_pct": round(acc, 1),
                "total_predictions": stats["total"],
                "correct_predictions": stats["correct"],
                "avg_return_pct": round(avg_ret, 2),
            }

        return result

    def get_strategy_accuracy(self, min_samples: int = 3) -> Dict[str, Dict]:
        """Calculate accuracy per recommended strategy."""
        log = self._read_log()
        strat_stats: Dict[str, Dict] = {}

        for record in log:
            if not record.get("outcome_recorded"):
                continue

            strat = record.get("best_strategy", "unknown")
            if strat not in strat_stats:
                strat_stats[strat] = {"correct": 0, "total": 0, "returns": []}

            strat_stats[strat]["total"] += 1
            if record.get("was_correct"):
                strat_stats[strat]["correct"] += 1
            if record.get("actual_return_pct") is not None:
                strat_stats[strat]["returns"].append(record["actual_return_pct"])

        result = {}
        for strat, stats in strat_stats.items():
            if stats["total"] < min_samples:
                continue
            acc = stats["correct"] / stats["total"] * 100
            avg_ret = sum(stats["returns"]) / len(stats["returns"]) if stats["returns"] else 0
            result[strat] = {
                "accuracy_pct": round(acc, 1),
                "total_predictions": stats["total"],
                "correct_predictions": stats["correct"],
                "avg_return_pct": round(avg_ret, 2),
            }

        return result

    def get_dynamic_weights(self) -> Dict[str, float]:
        """
        Calculate dynamic signal weights based on historical accuracy.
        Signals with higher accuracy get higher weights.
        """
        source_accuracy = self.get_accuracy_by_source(min_samples=3)

        if not source_accuracy:
            # No data yet — return default weights
            return {
                "flow": 0.20, "iv": 0.20, "technical": 0.15,
                "cpr": 0.15, "sentiment": 0.10, "gex": 0.10,
                "sideways": 0.10,
            }

        # Weight by accuracy * sqrt(sample_count) for statistical significance
        raw_weights = {}
        for src, stats in source_accuracy.items():
            raw_weights[src] = stats["accuracy_pct"] * math.sqrt(stats["total_predictions"])

        total = sum(raw_weights.values()) or 1
        return {src: round(w / total, 3) for src, w in raw_weights.items()}

    def get_performance_summary(self) -> Dict:
        """Full performance summary."""
        log = self._read_log()
        total = len(log)
        recorded = [r for r in log if r.get("outcome_recorded")]
        correct = [r for r in recorded if r.get("was_correct")]

        return {
            "total_predictions": total,
            "outcome_recorded": len(recorded),
            "pending_outcomes": total - len(recorded),
            "overall_accuracy_pct": round(
                len(correct) / len(recorded) * 100, 1
            ) if recorded else 0,
            "by_source": self.get_accuracy_by_source(),
            "by_strategy": self.get_strategy_accuracy(),
            "dynamic_weights": self.get_dynamic_weights(),
        }

