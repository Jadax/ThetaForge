"""
Equity Background Scanner.

Periodically runs the EquityBrain over the equity universe and emits persistent
notifications for actionable long candidates, mirroring the options Brain
scanner's contract (persisted results + notifications + status file) so the
dashboard, executor, and manager can consume both engines identically.

Data flow: IBKR bridge (via FreeDataProvider, when configured) -> yfinance.
Market breadth (risk tilt) and the SPY benchmark are computed once per scan
pass and shared across the per-symbol fan-out, exactly like the options
scanner's cached SPY return.
"""
import asyncio
import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from agents.data_ingestion.free_data import FreeDataProvider
from agents.equity_trader.equity_brain import EquityBrain, BUY_SCORE_FLOOR
from agents.equity_trader.equity_universe import build_equity_universe
from agents.trade_engine.background_scanner import is_market_hours
from agents.trade_engine.macro_calendar import macro_days_until

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
EQUITY_RESULTS_FILE = os.path.join(DATA_DIR, "equity_scan_results.json")
EQUITY_NOTIFICATIONS_FILE = os.path.join(DATA_DIR, "equity_notifications.json")
EQUITY_STATE_FILE = os.path.join(DATA_DIR, "equity_scan_state.json")

# Notifications carry only high-conviction, gated longs. The score floor here is
# higher than the recommender's because a notification is a one-way page to the
# executor; a recommender fetch can still reject for capital/position reasons.
EQUITY_NOTIFICATION_SCORE_FLOOR = 70.0
NON_ACTIONABLE = {"no_trade"}
# Same bounded fan-out rationale as the options scanner: keep per-scan concurrency
# low enough that the free data sources (and Render) are not overwhelmed.
# Matched to the options scanner's 3: both scanners share one free-tier CPU,
# and their combined worker threads must not starve the event loop.
SCAN_CONCURRENCY = 3

# Post-boot delay before this scanner's first tick. The options scanner waits
# STARTUP_GRACE_SECONDS; the extra stagger keeps both scanners' heavy passes
# from starting (and re-aligning) at the same instant on a single free CPU.
EQUITY_START_GRACE_SECONDS = 135

# Liquid ETFs are rotation candidates; individual stocks are momentum names.
ETF_SET = {
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLB", "XLC", "XLU", "XLRE",
}


class EquityBackgroundScanner:
    """Periodically runs the EquityBrain over the equity universe."""

    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_results: Dict[str, str] = {}
        self._provider = FreeDataProvider()
        self._brain = EquityBrain()
        self._spy_6m_return_cache: Optional[float] = None
        self._persist_lock = asyncio.Lock()
        self._ensure_files()

    # ── persistence ────────────────────────────────────────────────────

    def _ensure_files(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        for path, default in [
            (EQUITY_RESULTS_FILE, {"symbols": {}}),
            (EQUITY_NOTIFICATIONS_FILE, []),
            (EQUITY_STATE_FILE,
             {"last_run": None, "next_run": None, "interval_seconds": self.interval,
              "is_running": False, "symbols_scanned": 0, "symbols_with_trades": 0,
              "scan_diagnostics": {}, "errors": []}),
        ]:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(default, handle)

    def _read_json(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: str, data) -> None:
        # Compact separators: machine-read state files; indent=2 doubled
        # serialization time on the free-tier CPU.
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))

    async def _read_json_async(self, path: str):
        return await asyncio.to_thread(self._read_json, path)

    async def _write_json_async(self, path: str, data) -> None:
        await asyncio.to_thread(self._write_json, path, data)

    # ── shared per-scan inputs (computed once, like the options scanner) ──

    async def _market_risk_tilt(self) -> Optional[str]:
        """Broad risk tilt from MarketOverview. Cached per scan; failures
        degrade to None (soft — never a fabricated veto)."""
        try:
            from agents.general_trader.market_overview import MarketOverview
            overview = await MarketOverview(provider=self._provider).overview()
            tilt = (overview.get("risk_tilt") or {}).get("tilt")
            return tilt if tilt in {"risk_on", "risk_off", "mixed"} else None
        except Exception:
            logger.debug("Market risk tilt unavailable; skipping this scan's breadth veto")
            return None

    async def _spy_6m_return(self) -> Optional[float]:
        """6-month SPY return for relative-strength vs the market."""
        if self._spy_6m_return_cache is None:
            try:
                hist = await self._provider.get_historical_prices("SPY", period="6mo")
                closes = hist["Close"].tolist() if hist is not None and hasattr(hist, "Close") else []
                if len(closes) >= 2 and closes[0]:
                    self._spy_6m_return_cache = closes[-1] / closes[0] - 1
            except Exception:
                logger.warning("SPY history unavailable; relative strength disabled this scan")
                self._spy_6m_return_cache = 0.0
        return self._spy_6m_return_cache

    # ── per-symbol analysis ────────────────────────────────────────────

    async def _analyze_one(self, symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Run the EquityBrain only when the data needed is present. Missing
        data persists a skip reason, never a placeholder signal."""
        try:
            price = await self._provider.get_stock_price(symbol)
            if not price or price <= 0:
                return None, "price_unavailable"
        except Exception:
            return None, "price_unavailable"

        try:
            hist = await self._provider.get_historical_prices(symbol, period="1y")
            if hist is None or len(hist) < 60:
                return None, "history_unavailable"
            closes = hist["Close"].tolist() if hasattr(hist, "tolist") else list(hist["Close"])
            highs = hist["High"].tolist() if "High" in hist.columns else closes
            lows = hist["Low"].tolist() if "Low" in hist.columns else closes
            volumes = hist["Volume"].tolist() if "Volume" in hist.columns else []
        except Exception:
            return None, "history_unavailable"

        # Market breadth read: computed ONCE per scan pass in scan_once() and
        # shared by the fan-out (see _scan_risk_tilt there). An earlier
        # version called _market_risk_tilt() here -- per symbol -- which
        # multiplied MarketOverview's ~18 full-year history fetches into
        # thousands of yfinance calls per pass, despite this module's
        # docstring already claiming per-pass sharing.
        risk_tilt = getattr(self, "_scan_risk_tilt", None)
        benchmark_return_6m = await self._spy_6m_return()

        days_to_earnings = None
        try:
            next_earnings = await self._provider.get_next_earnings_date(symbol)
            days_to_earnings = (next_earnings - date.today()).days if next_earnings else None
        except Exception:
            pass

        try:
            days_to_macro = macro_days_until()
        except Exception:
            days_to_macro = None

        try:
            result = await asyncio.to_thread(
                self._brain.analyze,
                symbol=symbol.upper(),
                closes=closes,
                highs=highs,
                lows=lows,
                volumes=volumes,
                benchmark_return_6m=benchmark_return_6m,
                market_risk_tilt=risk_tilt,
                days_to_earnings=days_to_earnings,
                days_to_macro=days_to_macro,
                is_etf=symbol.upper() in ETF_SET,
            )
        except Exception:
            logger.exception("EquityBrain analysis failed for %s", symbol)
            return None, "brain_error"

        return {
            "symbol": symbol.upper(),
            "signal": result.signal,
            "score": result.score,
            "strategy": result.strategy,
            "reasoning": result.reasoning,
            "no_trade_reason": result.no_trade_reason,
            "price": result.price,
            "rsi_14": result.rsi_14,
            "adx": result.adx,
            "sma_50": result.sma_50,
            "sma_200": result.sma_200,
            "above_200d": result.above_200d,
            "momentum_1m": result.momentum_1m,
            "momentum_3m": result.momentum_3m,
            "momentum_6m": result.momentum_6m,
            "relative_strength": result.relative_strength,
            "volume_ratio": result.volume_ratio,
            "percent_off_52w_high": result.percent_off_52w_high,
            "atr_value": result.atr_value,
            "atr_pct": result.atr_pct,
            "breakout_20d": result.breakout_20d,
            "market_risk_tilt": result.market_risk_tilt,
            "days_to_earnings": result.days_to_earnings,
            "days_to_macro": result.days_to_macro,
            "is_etf": symbol.upper() in ETF_SET,
        }, None

    # ── scan loop ──────────────────────────────────────────────────────

    async def scan_once(self, symbols: Optional[List[str]] = None) -> int:
        """Run one full scan pass; returns the count of new notifications."""
        if symbols is None:
            symbols = await build_equity_universe()

        new_count = 0
        results: Dict[str, dict] = {}
        skipped: Dict[str, int] = {}
        no_trade_reasons: Dict[str, int] = {}

        notifs = await self._read_json_async(EQUITY_NOTIFICATIONS_FILE) or []
        notifs = [
            n for n in notifs
            if n.get("signal") not in NON_ACTIONABLE
            and (float(n.get("score") or 0)) >= EQUITY_NOTIFICATION_SCORE_FLOOR
        ]

        # Market-wide breadth read, once per pass and shared by every symbol's
        # analysis (MarketOverview pulls ~18 full-year histories per call).
        self._scan_risk_tilt = await self._market_risk_tilt()

        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _one(symbol: str) -> None:
            async with semaphore:
                data, skip = await self._analyze_one(symbol)
            if skip:
                skipped[skip] = skipped.get(skip, 0) + 1
                return
            results[symbol] = data
            if data.get("no_trade_reason"):
                reason = data["no_trade_reason"]
                no_trade_reasons[reason] = no_trade_reasons.get(reason, 0) + 1

        # Batched fan-out (mirrors the options scanner): bounded concurrency,
        # a gc pass in a thread between batches, and an explicit yield so the
        # event loop keeps servicing health checks while worker threads chew
        # through indicator math on the single free-tier CPU.
        # Small batches for the same reason as the options scanner: bounded
        # cyclic scrape-garbage between gc passes (see background_scanner).
        _BATCH_SIZE = 6
        for i in range(0, len(symbols), _BATCH_SIZE):
            batch = symbols[i : i + _BATCH_SIZE]
            await asyncio.gather(*(_one(symbol) for symbol in batch))
            import gc
            await asyncio.to_thread(gc.collect)
            from agents.trade_engine.background_scanner import _rss_mb
            rss = _rss_mb()
            if rss:
                logger.info(
                    "equity batch %d/%d done, peak RSS %.0f MB",
                    i // _BATCH_SIZE + 1,
                    (len(symbols) + _BATCH_SIZE - 1) // _BATCH_SIZE,
                    rss,
                )
            await asyncio.sleep(0)

        for symbol, data in results.items():
            signature = f"{data.get('signal')}|{round(float(data.get('score') or 0), 1)}|{data.get('strategy')}"
            if data.get("signal") == "buy" and float(data.get("score") or 0) >= EQUITY_NOTIFICATION_SCORE_FLOOR:
                if self._last_results.get(symbol) != signature:
                    notification = {
                        "id": str(uuid4()),
                        "engine": "equity",
                        "symbol": symbol,
                        "signal": "buy",
                        "score": round(float(data.get("score") or 0), 1),
                        "strategy": data.get("strategy"),
                        "reasoning": data.get("reasoning"),
                        "price": data.get("price"),
                        "market_risk_tilt": data.get("market_risk_tilt"),
                        "created_at": date.today().isoformat(),
                        "acknowledged": False,
                    }
                    notifs.append(notification)
                    new_count += 1
            self._last_results[symbol] = signature

        # Keep only recent notifications; the file must not grow unbounded.
        notifs = notifs[-200:]
        # One thread round-trip for all three state files instead of three
        # sync serializations of multi-hundred-KB result payloads on the loop.
        # The lock keeps a manual trigger and a loop tick from interleaving.
        def _persist() -> None:
            self._write_json(EQUITY_RESULTS_FILE, {"as_of": date.today().isoformat(), "symbols": results})
            self._write_json(EQUITY_NOTIFICATIONS_FILE, notifs)
            self._write_json(EQUITY_STATE_FILE, {
                "last_run": date.today().isoformat(),
                "interval_seconds": self.interval,
                "is_running": True,
                "symbols_scanned": len(results),
                "symbols_with_trades": sum(1 for d in results.values() if d.get("signal") == "buy"),
                "scan_diagnostics": {"skipped": skipped, "no_trade_reasons": no_trade_reasons},
                "errors": [],
            })
        async with self._persist_lock:
            await asyncio.to_thread(_persist)
        return new_count

    # ── lifecycle ──────────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Post-boot grace + stagger: let the platform health probe settle
        # before heavy work starts, and offset this scanner's first tick from
        # the options scanner's so both never hit the free CPU simultaneously.
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=EQUITY_START_GRACE_SECONDS
            )
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop_event.is_set():
            try:
                if is_market_hours():
                    new = await self.scan_once()
                    logger.info("Equity scan pass complete: %d new notification(s)", new)
                else:
                    logger.debug("Equity scanner idle (market closed)")
            except Exception:
                logger.exception("Equity scan pass failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("Equity Background Scanner started (%ss interval)", self.interval)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    # ── read APIs for the advisor ──────────────────────────────────────

    async def get_notifications(self, unacknowledged_only: bool = False, limit: int = 50) -> List[dict]:
        notifs = await self._read_json_async(EQUITY_NOTIFICATIONS_FILE) or []
        if unacknowledged_only:
            notifs = [n for n in notifs if not n.get("acknowledged")]
        return sorted(notifs, key=lambda n: n.get("created_at", ""), reverse=True)[:limit]

    async def acknowledge_notification(self, notification_id: str) -> bool:
        notifs = await self._read_json_async(EQUITY_NOTIFICATIONS_FILE) or []
        found = False
        for n in notifs:
            if n.get("id") == notification_id:
                n["acknowledged"] = True
                found = True
        if found:
            await self._write_json_async(EQUITY_NOTIFICATIONS_FILE, notifs)
        return found

    async def get_status(self) -> Dict[str, Any]:
        state = await self._read_json_async(EQUITY_STATE_FILE) or {}
        notifs = await self._read_json_async(EQUITY_NOTIFICATIONS_FILE) or []
        unacked = sum(1 for n in notifs if not n.get("acknowledged"))
        return {
            "engine": "equity",
            "market_open": is_market_hours(),
            "last_run": state.get("last_run"),
            "interval_seconds": state.get("interval_seconds", self.interval),
            "is_running": bool(self._task and not self._task.done()),
            "symbols_scanned": state.get("symbols_scanned", 0),
            "symbols_with_trades": state.get("symbols_with_trades", 0),
            "pending_notifications": unacked,
            "total_notifications": len(notifs),
            "diagnostics": state.get("scan_diagnostics", {}),
        }


_scanner: Optional[EquityBackgroundScanner] = None


async def get_background_equity_scanner() -> EquityBackgroundScanner:
    """Process-wide singleton so the advisor and the startup lifecycle share
    one scanner task and one in-memory diff state."""
    global _scanner
    if _scanner is None:
        _scanner = EquityBackgroundScanner()
    return _scanner
