"""
Persistent per-symbol implied-volatility history store.

IV Rank and IV Percentile are meaningless unless they come from an actual
history of the symbol's implied volatility. Free feeds (yfinance, CBOE) expose
only a point-in-time chain, so this store appends one daily ATM-IV snapshot per
symbol per scan and computes rank/percentile from the accumulated history. The
store degrades gracefully: callers fall back to a realized-vol proxy until
enough samples exist.

Reuses calculate_iv_rank / calculate_iv_percentile from iv_metrics.
"""
import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Dict, Any, List, Optional

from agents.volatility.iv_metrics import calculate_iv_rank, calculate_iv_percentile

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DEFAULT_PATH = os.path.join(DATA_DIR, "iv_history.json")

# Fewer than this many daily snapshots is too noisy to trust as a 52-week rank;
# callers fall back to the realized-volatility proxy in that case.
MIN_SAMPLES = 10
# ~1.5 years of daily snapshots is plenty; cap keeps the JSON file small.
MAX_SAMPLES = 500


class IVHistoryStore:
    """Append-only daily IV snapshot store keyed by symbol."""

    def __init__(self, path: str = None):
        self.path = path or DEFAULT_PATH
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({}, handle)

    def _read(self) -> Dict[str, List[Dict[str, Any]]]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, List[Dict[str, Any]]]):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def record(self, symbol: str, atm_iv: Optional[float], hv_20: Optional[float]):
        """Append today's snapshot once per symbol per day (idempotent)."""
        symbol = symbol.upper()
        if atm_iv is None or atm_iv <= 0:
            return
        today = date.today().isoformat()
        data = self._read()
        entries = data.get(symbol, [])
        if entries and entries[-1].get("date") == today:
            return
        entries.append({
            "date": today,
            "iv": round(float(atm_iv), 4),
            "hv_20": round(float(hv_20), 4) if hv_20 else None,
        })
        data[symbol] = entries[-MAX_SAMPLES:]
        self._write(data)

    def _ivs(self, symbol: str) -> List[float]:
        return [entry.get("iv") for entry in self._read().get(symbol.upper(), []) if entry.get("iv")]

    def sample_count(self, symbol: str) -> int:
        return len(self._ivs(symbol))

    def iv_rank(self, symbol: str, current_iv: Optional[float] = None) -> Optional[float]:
        """IV Rank (0-100) or None when history is too thin to trust."""
        history = self._ivs(symbol)
        if len(history) < MIN_SAMPLES:
            return None
        current = history[-1] if current_iv is None else float(current_iv)
        return round(calculate_iv_rank(current, history), 1)

    def iv_percentile(self, symbol: str, current_iv: Optional[float] = None) -> Optional[float]:
        """IV Percentile (0-100) or None when history is too thin to trust."""
        history = self._ivs(symbol)
        if len(history) < MIN_SAMPLES:
            return None
        current = history[-1] if current_iv is None else float(current_iv)
        return round(calculate_iv_percentile(current, history), 1)

    def iv_52w_range(self, symbol: str, current_iv: Optional[float] = None) -> Optional[Dict[str, float]]:
        """High/low implied-vol bound for the Brain's rank input."""
        history = self._ivs(symbol)
        if not history:
            return None
        current = history[-1] if current_iv is None else float(current_iv)
        return {
            "iv_52w_high": max(max(history), current),
            "iv_52w_low": min(min(history), current),
        }

    def vrp_zscore(
        self,
        symbol: str,
        current_iv: Optional[float] = None,
        hv_20: Optional[float] = None,
    ) -> Optional[float]:
        """Z-score of today's IV-minus-RV premium vs the symbol's own history.

        The volatility risk premium (VRP = ATM IV − realized vol) is the
        institutional premium-harvesting metric (Bondarenko 2019; FlashAlpha;
        VolatilityBox). Each stored snapshot carries its own iv + hv_20, so the
        trailing VRP series is available from the same store that already
        computes IV rank. Returns None until MIN_SAMPLES snapshots exist.
        """
        history = self._read().get(symbol.upper(), [])
        points = [
            entry for entry in history
            if entry.get("iv") and entry.get("hv_20")
        ]
        if len(points) < MIN_SAMPLES:
            return None
        premium = [float(entry["iv"]) - float(entry["hv_20"]) for entry in points]
        if current_iv is None:
            current_iv = float(points[-1]["iv"])
        if hv_20 is None:
            hv_20 = float(points[-1]["hv_20"])
        current = float(current_iv) - float(hv_20)
        mean = sum(premium) / len(premium)
        variance = sum((value - mean) ** 2 for value in premium) / len(premium)
        std = variance ** 0.5
        if std <= 1e-6:
            return 0.0
        return round((current - mean) / std, 2)

    def iv_change_5d(self, symbol: str) -> Optional[float]:
        """Fractional 5-trading-day change in the symbol's ATM IV.

        IV momentum (rising vs falling vol) is a standard screener column
        (Barchart, TanukiTrade). Computed from the same daily snapshots.
        """
        ivs = self._ivs(symbol)
        if len(ivs) < 6:
            return None
        base = ivs[-6]
        if base <= 0:
            return None
        return round((ivs[-1] - base) / base, 4)
