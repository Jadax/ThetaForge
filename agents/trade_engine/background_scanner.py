"""
Background Brain Scanner.
Runs the AI Brain on the full tradeable universe periodically in a background
asyncio task. Detects new actionable trade opportunities and generates
persistent notifications that the dashboard can surface.

Data flows: IBKR bridge (live) -> FreeDataProvider (yfinance fallback).
"""
import json
import logging
import os
import asyncio
from typing import Dict, Optional, List
from datetime import datetime, timedelta

import httpx

from agents.data_ingestion.free_data import FreeDataProvider

logger = logging.getLogger(__name__)

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8002")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SCAN_RESULTS_FILE = os.path.join(DATA_DIR, "brain_scan_results.json")
SCAN_NOTIFICATIONS_FILE = os.path.join(DATA_DIR, "brain_notifications.json")
SCAN_STATE_FILE = os.path.join(DATA_DIR, "brain_scan_state.json")

# Background alerts are discovery candidates, but they should still meet the
# same high-conviction floor used by the detailed Advisor recommendation path.
NOTIFICATION_SCORE_FLOOR = 75
NON_ACTIONABLE_STRATEGIES = {"no_trade", "avoid_new_positions", "roll_or_close"}

# Liquid options underlyings — most actively traded US ETFs and large-caps.
# These are the first-pass scan targets; the screener discovers additional names.
LIQUID_OPTIONS_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLB", "XLC", "XLU", "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "GOOGL", "TSLA", "AVGO", "AMD", "NFLX", "CRM", "ORCL", "ADBE", "INTC",
    "QCOM", "CSCO", "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP",
    "COIN", "HOOD", "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "AMGN",
    "TMO", "ISRG", "XOM", "CVX", "OXY", "SLB", "CAT", "DE", "GE", "BA",
    "LMT", "NKE", "COST", "WMT", "HD", "MCD", "SBUX", "DIS", "UBER",
    "PLTR", "SMCI",
]


# ── Universe builder ────────────────────────────────────────────────────


async def build_scan_universe(max_symbols: int = 300) -> List[str]:
    """Build a deduplicated, rank-ordered universe to scan.

    Sources (in priority order):
      1. Liquid options universe (static, ~70 symbols)
      2. IBKR bridge — current user positions (live)
      3. IBKR bridge — TWS scanner (hot stocks by volume, gainers, losers)
      4. Yahoo Finance screeners (most actives, day gainers/losers, growth tech)

    The list is deduplicated and capped at *max_symbols*.
    """
    seen: set = set()
    universe: list = []

    def _add(sym: str):
        s = sym.upper().strip()
        if s and s not in seen:
            seen.add(s)
            universe.append(s)

    for sym in LIQUID_OPTIONS_UNIVERSE:
        _add(sym)

    # IBKR bridge — current positions and live TWS market discovery. A hosted
    # deployment cannot reach a Bridge on a personal machine, so both calls are
    # expected to fail there; the failure is logged, never raised.
    bridge_symbols = await _bridge_discoveries()
    for sym in bridge_symbols:
        _add(sym)

    # Yahoo Finance screeners (fallback discovery)
    try:
        active = await FreeDataProvider().get_active_stock_universe(limit=80)
        for sym in active or []:
            _add(sym)
    except Exception as error:
        logger.warning("Yahoo screener discovery failed: %s", error)

    return universe[:max_symbols]


async def _bridge_discoveries() -> List[str]:
    """Collect position and TWS-scanner symbols from the local paper Bridge."""
    token = os.getenv("BRIDGE_ACCESS_TOKEN", "")
    # The Bridge authenticates on X-ThetaForge-Bridge-Token. Sending any other
    # header name silently returns 401 and yields no discoveries.
    headers = {"X-ThetaForge-Bridge-Token": token} if token else {}
    symbols: List[str] = []
    try:
        # One client for both calls, closed on exit. Creating a client per
        # request leaked a connection pool on every five-minute scan.
        async with httpx.AsyncClient(base_url=BRIDGE_URL, headers=headers) as client:
            try:
                response = await client.get("/positions", timeout=5)
                if response.status_code == 200:
                    symbols.extend(str(item.get("symbol", "")) for item in response.json())
            except Exception as error:
                logger.debug("Bridge positions unavailable: %s", error)
            try:
                response = await client.get("/scanner/universe", timeout=8)
                if response.status_code == 200:
                    symbols.extend(str(sym) for sym in response.json().get("symbols", []))
            except Exception as error:
                logger.debug("Bridge TWS scanner unavailable: %s", error)
    except Exception as error:
        logger.debug("Bridge is not reachable: %s", error)
    return symbols


# ── Scanner ─────────────────────────────────────────────────────────────


class BackgroundBrainScanner:
    """Periodically runs the AI Brain on the full tradeable universe.

    Scans every *interval_seconds*, diffs against the previous scan, and
    emits a notification for every symbol whose overall_score crosses
    NOTIFICATION_SCORE_FLOOR and whose result signature has changed.
    """

    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_results: Dict[str, str] = {}  # symbol → signature
        self._provider = FreeDataProvider()
        self._brain = None
        self._ensure_files()

    # ── persistence helpers ──────────────────────────────────────────

    def _ensure_files(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        for path, default in [
            (SCAN_RESULTS_FILE, {"symbols": {}}),
            (SCAN_NOTIFICATIONS_FILE, []),
            (SCAN_STATE_FILE,
             {"last_run": None, "next_run": None, "interval_seconds": self.interval,
              "is_running": False, "symbols_scanned": 0,
              "symbols_with_trades": 0, "errors": []}),
        ]:
            if not os.path.exists(path):
                with open(path, "w") as f:
                    json.dump(default, f)

    def _read_json(self, path: str):
        with open(path, "r") as f:
            return json.load(f)

    def _write_json(self, path: str, data):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _result_signature(self, score: float, signal: str, strategy: str, regime: str) -> str:
        return f"{signal}|{round(score, 1)}|{strategy}|{regime}"

    def _is_new_trade(self, symbol: str, score: float, signal: str, strategy: str,
                       regime: str) -> bool:
        if strategy in NON_ACTIONABLE_STRATEGIES or abs(score) < NOTIFICATION_SCORE_FLOOR:
            return False
        sig = self._result_signature(score, signal, strategy, regime)
        return self._last_results.get(symbol) != sig

    async def _lazy_brain(self):
        if self._brain is None:
            from agents.trade_engine.ai_brain import AIBrain
            self._brain = AIBrain()
        return self._brain

    # ── scan logic ───────────────────────────────────────────────────

    async def _analyze_one(self, symbol: str) -> Optional[dict]:
        """Run the Brain on a single symbol and return a result dict, or None."""
        try:
            price = await self._provider.get_stock_price(symbol)
            if not price or price <= 0:
                return None
        except Exception:
            return None

        try:
            chain = await self._provider.get_option_chain(symbol) or []
        except Exception:
            chain = []

        try:
            vix = await self._provider.get_vix()
            if vix is None:
                vix = 20
        except Exception:
            vix = 20

        try:
            hist = await self._provider.get_historical_prices(symbol, period="1y")
            if hist is not None and len(hist):
                closes = hist["Close"].tolist() if hasattr(hist, "tolist") else list(hist["Close"])
                high_prices = hist["High"].tolist() if "High" in hist.columns else closes
                low_prices = hist["Low"].tolist() if "Low" in hist.columns else closes
            else:
                closes = [price]
                high_prices = [price * 1.01]
                low_prices = [price * 0.99]
        except Exception:
            closes = [price]
            high_prices = [price * 1.01]
            low_prices = [price * 0.99]

        try:
            brain = await self._lazy_brain()
            result = brain.analyze(
                symbol=symbol,
                stock_price=price,
                option_chain=chain,
                historical_prices=closes,
                high_prices=high_prices,
                low_prices=low_prices,
                vix=vix,
            )
            return {
                "score": result.overall_score,
                "signal": result.overall_signal.value,
                "regime": result.regime,
                "strategy": result.best_strategy,
                "strategy_reasoning": result.best_strategy_reasoning,
                "iv_rank": (result.iv_signal or {}).get("iv_rank"),
                "iv_hv_ratio": (result.iv_signal or {}).get("ratio"),
                "iv_hv_signal": (result.iv_signal or {}).get("signal"),
                "top_signal": "",
            }
        except Exception:
            return None

    async def scan_once(self, symbols: Optional[List[str]] = None) -> int:
        """Run one full scan pass over *symbols* (or the auto-built universe).

        Returns the count of new notifications generated.
        """
        if symbols is None:
            symbols = await build_scan_universe()

        new_count = 0
        results: Dict[str, dict] = {}

        # Clean old no_trade notifications so stale entries don't linger
        old_notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
        clean = [
            notification for notification in old_notifs
            if notification.get("best_strategy") not in NON_ACTIONABLE_STRATEGIES
            and abs(float(notification.get("score", 0) or 0)) >= NOTIFICATION_SCORE_FLOOR
        ]
        if len(clean) != len(old_notifs):
            self._write_json(SCAN_NOTIFICATIONS_FILE, clean)

        for symbol in symbols:
            data = await self._analyze_one(symbol)
            if data is None:
                continue

            # Only alert on tradeable signals — skip no_trade
            if data["strategy"] in NON_ACTIONABLE_STRATEGIES:
                results[symbol] = {
                    "score": data["score"],
                    "signal": data["signal"],
                    "strategy": data["strategy"],
                    "filtered": "no_trade",
                }
                continue

            if self._is_new_trade(symbol, data["score"], data["signal"],
                                   data["strategy"], data["regime"]):
                notif = {
                    "id": f"NTF-{symbol}-{int(datetime.utcnow().timestamp())}",
                    "symbol": symbol,
                    "score": data["score"],
                    "signal": data["signal"],
                    "regime": data["regime"],
                    "best_strategy": data["strategy"],
                    "strategy_reasoning": data.get("strategy_reasoning", ""),
                    "iv_rank": data.get("iv_rank"),
                    "iv_hv_ratio": data.get("iv_hv_ratio"),
                    "iv_hv_signal": data.get("iv_hv_signal"),
                    "top_signal": data.get("top_signal", ""),
                    "timestamp": datetime.utcnow().isoformat(),
                    "acknowledged": False,
                }
                self._last_results[symbol] = self._result_signature(
                    data["score"], data["signal"], data["strategy"], data["regime"]
                )

                notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
                notifs.append(notif)
                if len(notifs) > 500:
                    notifs = notifs[-500:]
                self._write_json(SCAN_NOTIFICATIONS_FILE, notifs)
                new_count += 1

            results[symbol] = {
                "score": data["score"],
                "signal": data["signal"],
                "strategy": data["strategy"],
            }

        self._write_json(SCAN_RESULTS_FILE, {
            "symbols": results,
            "last_full_run": datetime.utcnow().isoformat(),
        })

        state = self._read_json(SCAN_STATE_FILE)
        state["last_run"] = datetime.utcnow().isoformat()
        state["next_run"] = (datetime.utcnow() + timedelta(seconds=self.interval)).isoformat()
        state["symbols_scanned"] = len(results)
        state["symbols_with_trades"] = sum(
            1 for result in results.values()
            if result.get("strategy") not in NON_ACTIONABLE_STRATEGIES
            and abs(result["score"]) >= NOTIFICATION_SCORE_FLOOR
        )
        state["errors"] = []
        self._write_json(SCAN_STATE_FILE, state)

        return new_count

    async def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                await self.scan_once()
            except Exception:
                # A failed pass must not kill the loop, but it must be visible:
                # silent failures here previously hid broken discovery for
                # several releases.
                logger.exception("Background scan pass failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
                break
            except asyncio.TimeoutError:
                continue

    async def start(self):
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        state = self._read_json(SCAN_STATE_FILE)
        state["is_running"] = True
        self._write_json(SCAN_STATE_FILE, state)

    async def stop(self):
        if self._task is None:
            return
        self._stop_event.set()
        await asyncio.wait_for(self._task, timeout=10)
        self._task = None
        state = self._read_json(SCAN_STATE_FILE)
        state["is_running"] = False
        self._write_json(SCAN_STATE_FILE, state)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def get_notifications(self, unacknowledged_only: bool = False,
                                limit: int = 50) -> List[Dict]:
        notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
        # Defensive read-time filter hides invalid records already persisted by
        # older deployments without waiting for the next five-minute scan.
        notifs = [
            notification for notification in notifs
            if notification.get("best_strategy") not in NON_ACTIONABLE_STRATEGIES
            and abs(float(notification.get("score", 0) or 0)) >= NOTIFICATION_SCORE_FLOOR
        ]
        if unacknowledged_only:
            notifs = [n for n in notifs if not n.get("acknowledged")]
        return notifs[-limit:]

    async def acknowledge_notification(self, notification_id: str) -> bool:
        notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
        for n in notifs:
            if n.get("id") == notification_id:
                n["acknowledged"] = True
                self._write_json(SCAN_NOTIFICATIONS_FILE, notifs)
                return True
        return False

    async def acknowledge_all(self):
        notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
        for n in notifs:
            n["acknowledged"] = True
        self._write_json(SCAN_NOTIFICATIONS_FILE, notifs)

    async def get_status(self) -> Dict:
        state = self._read_json(SCAN_STATE_FILE)
        notifs = self._read_json(SCAN_NOTIFICATIONS_FILE)
        unacked = [n for n in notifs if not n.get("acknowledged")]
        last_results = self._read_json(SCAN_RESULTS_FILE)
        return {
            "is_running": self.is_running,
            "last_run": state.get("last_run"),
            "next_run": state.get("next_run"),
            "interval_seconds": self.interval,
            "symbols_scanned_last_run": state.get("symbols_scanned", 0),
            "symbols_with_trades": state.get("symbols_with_trades", 0),
            "pending_notifications": len(unacked),
            "total_notifications": len(notifs),
            "last_results": last_results,
        }


# ── global singleton ────────────────────────────────────────────────────

_scanner_instance: Optional[BackgroundBrainScanner] = None


async def get_background_scanner() -> BackgroundBrainScanner:
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = BackgroundBrainScanner(interval_seconds=300)
    return _scanner_instance
