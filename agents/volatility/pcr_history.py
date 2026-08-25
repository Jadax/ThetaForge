"""
Persistent per-symbol put/call-ratio history store.

Symbol-level PCR (put volume / call volume) only means something relative to
its own recent distribution -- a 1.1 on a symbol that usually sits at 0.7 is a
fear spike, while the same 1.1 on a chronically-heavy-put symbol is normal.
Free feeds expose only a point-in-time chain, so this store appends one daily
PCR snapshot per symbol per scan and lets the sentiment engine read a
z-score over the accumulated history.

Mirrors the IVHistoryStore contract (append-only daily snapshots, idempotent
per day, graceful degradation below MIN_SAMPLES) so both vol inputs follow the
same persistence pattern.
"""
import json
import logging
import os
from datetime import date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DEFAULT_PATH = os.path.join(DATA_DIR, "pcr_history.json")

# put_call_ratio_sentiment only computes a z-score once 20 samples exist;
# below that it falls back to absolute thresholds, so the store exposes the
# same cutoff for callers that want to know whether the history is "real".
MIN_SAMPLES = 20
# A few months of daily snapshots is plenty for a z-score; the cap keeps the
# JSON small on a long-running deployment.
MAX_SAMPLES = 120


class PCRHistoryStore:
    """Append-only daily PCR snapshot store keyed by symbol.

    An in-memory cache avoids re-parsing the JSON file on every method call
    during a scan pass (same pattern as IVHistoryStore).
    """

    _CACHE_TTL = 10.0  # seconds

    def __init__(self, path: str = None):
        self.path = path or DEFAULT_PATH
        self._ensure_file()
        self._cache: Optional[Dict[str, List[Dict[str, object]]]] = None
        self._cache_ts: float = 0.0

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({}, handle)

    def _read(self) -> Dict[str, List[Dict[str, object]]]:
        import time
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            result = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            result = {}
        self._cache = result
        self._cache_ts = now
        return result

    def _write(self, data: Dict[str, List[Dict[str, object]]]):
        # Atomic replace: shared with forked scan workers (see iv_history).
        tmp_path = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(tmp_path, self.path)
        self._cache = data
        import time
        self._cache_ts = time.monotonic()

    def record(self, symbol: str, pcr: Optional[float]):
        """Append today's snapshot once per symbol per day (idempotent)."""
        symbol = symbol.upper()
        if pcr is None or pcr <= 0:
            return
        today = date.today().isoformat()
        data = self._read()
        entries = data.get(symbol, [])
        if entries and entries[-1].get("date") == today:
            return
        entries.append({
            "date": today,
            "pcr": round(float(pcr), 4),
        })
        data[symbol] = entries[-MAX_SAMPLES:]
        self._write(data)

    def history(self, symbol: str) -> List[float]:
        return [
            float(entry.get("pcr"))
            for entry in self._read().get(symbol.upper(), [])
            if entry.get("pcr")
        ]

    def sample_count(self, symbol: str) -> int:
        return len(self.history(symbol))
