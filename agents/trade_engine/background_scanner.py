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
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta, timezone, date

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


def _atm_iv(chain: List[Dict]) -> Optional[float]:
    """Best-effort ATM implied volatility from the nearest-expiry chain.

    Uses the options closest to the 50% delta (or nearest strike to the money
    when deltas are missing) across the first expiry with IV, averaging the
    call and put IVs like a 0.50-delta straddle.
    """
    candidates = []
    for opt in chain:
        iv = opt.get("implied_volatility")
        if not iv or float(iv) <= 0:
            continue
        delta = opt.get("delta")
        if delta is not None:
            try:
                candidates.append((abs(float(delta) - 0.5), float(iv)))
            except (TypeError, ValueError):
                continue
        else:
            candidates.append((None, float(iv)))
    if not candidates:
        return None
    if candidates[0][0] is None:
        return sum(iv for _, iv in candidates) / len(candidates)
    # Near-zero delta offset = closest to the money.
    nearest = min(candidates, key=lambda pair: pair[0])
    near = [c for c in candidates if c[0] is not None and c[0] <= 0.05]
    pool = near if near else [nearest]
    return sum(iv for _, iv in pool) / len(pool)


def _rv_band(iv: Optional[float], hv: Optional[float]) -> Optional[str]:
    """Relative-volatility band (rich/cheap vol) for the scan payload."""
    try:
        from agents.volatility.flow_metrics import relative_volatility_band
    except ImportError:
        return None
    read = relative_volatility_band(
        float(iv) if iv is not None else None,
        float(hv) if hv is not None else None,
    )
    return (read or {}).get("band")


def _flow_signals(chain: List[Dict]) -> Optional[Dict]:
    """Unusual-volume / OI-divergence / pin-price signals from the free chain."""
    try:
        from agents.volatility.flow_metrics import (
            unusual_volume, oi_center_of_mass,
        )
    except ImportError:
        return None
    if not chain:
        return None
    # "Normal" volume reference: the median strike volume (robust to the hot
    # strike itself, unlike a mean which a single dominant strike inflates).
    volumes = sorted(float(o.get("volume") or o.get("volume_o") or 0) for o in chain)
    n = len(volumes)
    baseline = volumes[n // 2] if n % 2 else (volumes[n // 2 - 1] + volumes[n // 2]) / 2
    hottest = max(chain, key=lambda o: float(o.get("volume") or 0), default=None)
    unusual = None
    if hottest and baseline > 0:
        unusual = unusual_volume(hottest.get("volume"), baseline)
    center = oi_center_of_mass([o for o in chain if o.get("open_interest")])
    return {
        "hottest_strike": hottest.get("strike") if hottest else None,
        "unusual_volume": unusual,
        "oi_center_of_mass": center,
    }

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


# ── Nominal scan galleries (Option Samurai / Barchart pattern) ─────────────
# Named, one-click screen ideas. Each maps a name to a filter predicate over a
# scan result payload (or a symbol subset) so the dashboard can present curated
# "theses" without inventing a new scoring path. A missing payload field never
# passes a gallery filter (fail-closed).
SCAN_GALLERIES = {
    "wheel_candidates": {
        "label": "Wheel candidates",
        "thesis": "High-IVR underlyings for cash-secured puts / covered calls",
        "match": lambda r: (r.get("iv_rank") or 0) >= 50 and r.get("strategy") in {"cash_secured_put", "covered_call"},
    },
    "premium_flow": {
        "label": "Premium flow (rich vol)",
        "thesis": "Rich relative volatility and elevated unusual volume",
        "match": lambda r: r.get("rv_band") in {"rich", "very_rich"}
        and (r.get("flow_signals") or {}).get("unusual_volume", {}).get("tier") in {"elevated", "high", "extreme"},
    },
    "earnings_window": {
        "label": "Earnings window",
        "thesis": "Upcoming earnings; selling premium into rich event IV",
        "match": lambda r: (r.get("expected_move_pct") or 0) >= 2.0
        and r.get("strategy") not in {"no_trade", "avoid_new_positions"},
    },
    "high_iv_movers": {
        "label": "Rapid IV movers",
        "thesis": "Underlyings where IV is spiking fast (regime shifts)",
        "match": lambda r: (r.get("flow_signals") or {}).get("oi_center_of_mass") is not None
        and r.get("rv_band") in {"rich", "very_rich"},
    },
}


def gallery_symbols(gallery_name: str, results: Dict[str, dict]) -> List[str]:
    """Return symbols in *results* matching a named gallery's predicate."""
    gallery = SCAN_GALLERIES.get(gallery_name)
    if not gallery:
        return []
    return [
        symbol for symbol, result in results.items()
        if gallery["match"](result)
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
              "symbols_with_trades": 0, "scan_diagnostics": {}, "errors": []}),
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

    async def _analyze_one(self, symbol: str) -> Tuple[Optional[dict], Optional[str]]:
        """Run the Brain only when the data needed for a decision is present.

        Missing price, option-chain, VIX, or history data must never become a
        trade signal through a placeholder value. The caller persists the skip
        reason so a quiet market is distinguishable from unavailable data.
        """
        try:
            price = await self._provider.get_stock_price(symbol)
            if not price or price <= 0:
                return None, "price_unavailable"
        except Exception:
            return None, "price_unavailable"

        try:
            chain = await self._provider.get_option_chain(symbol) or []
        except Exception:
            return None, "option_chain_unavailable"
        if not chain:
            return None, "option_chain_unavailable"

        try:
            vix = await self._provider.get_vix()
            if vix is None:
                return None, "vix_unavailable"
        except Exception:
            return None, "vix_unavailable"

        try:
            hist = await self._provider.get_historical_prices(symbol, period="1y")
            if hist is None or len(hist) < 50:
                return None, "history_unavailable"
            closes = hist["Close"].tolist() if hasattr(hist, "tolist") else list(hist["Close"])
            high_prices = hist["High"].tolist() if "High" in hist.columns else closes
            low_prices = hist["Low"].tolist() if "Low" in hist.columns else closes
        except Exception:
            return None, "history_unavailable"

        # Volatility context: current IV, 20-day realized vol, expected move,
        # VIX term structure, earnings proximity, and the symbol's own IV
        # percentile. Every enrichment degrades to None on failure — the Brain
        # already treats missing data as neutral — so a single broken source
        # can never make a non-signal tradeable (or vice-versa).
        try:
            from agents.volatility.iv_metrics import realized_volatility
            hv_20 = realized_volatility(closes)
        except Exception:
            hv_20 = None

        current_iv = None
        expected_move_pct = None
        try:
            from agents.trade_engine.analytics import OptionsAnalytics
            ivs = [
                float(opt.get("implied_volatility") or 0)
                for opt in chain
                if opt.get("implied_volatility")
            ]
            current_iv = float(_atm_iv(chain)) if _atm_iv(chain) else (
                sorted(ivs)[len(ivs) // 2] if ivs else None
            )
            if current_iv and current_iv > 0 and price > 0:
                move = OptionsAnalytics().expected_move(price, current_iv, 30)
                expected_move_pct = move.get("expected_move_pct")
        except Exception:
            current_iv = None

        iv_percentile = None
        try:
            from agents.volatility.iv_history import IVHistoryStore
            store = IVHistoryStore()
            store.record(symbol, current_iv, hv_20)
            iv_percentile = store.iv_percentile(symbol, current_iv)
        except Exception:
            iv_percentile = None

        try:
            vix_term_structure = await self._provider.get_vix_term_structure()
        except Exception:
            vix_term_structure = None

        try:
            next_earnings = await self._provider.get_next_earnings_date(symbol)
            days_to_earnings = (next_earnings - date.today()).days if next_earnings else None
        except Exception:
            days_to_earnings = None

        # Desk analytics (all fail-closed to None): IV skew from the chain's
        # per-strike deltas, short interest via yfinance, and the earnings
        # implied-vs-realized move read. None of these can fabricate an edge
        # when the source data is missing.
        try:
            from agents.volatility.desk_analytics import calculate_iv_skew
            iv_skew = calculate_iv_skew(chain)
        except Exception:
            iv_skew = None

        try:
            short_interest = await self._provider.get_short_interest(symbol)
        except Exception:
            short_interest = None

        earnings_move = None
        try:
            from agents.volatility.desk_analytics import (
                implied_earnings_move,
                historical_earnings_moves,
                earnings_move_edge,
            )
            if current_iv and price > 0:
                implied = implied_earnings_move(chain, price)
                if implied:
                    earnings_dates = await self._provider.get_earnings_dates(symbol, limit=12)
                    past_dates = [event for event in earnings_dates if event < date.today()]
                    if past_dates:
                        moves = historical_earnings_moves(hist, past_dates)
                        earnings_move = earnings_move_edge(implied, moves)
        except Exception:
            earnings_move = None

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
                current_iv=current_iv if current_iv else 0.20,
                hv_20=hv_20 if hv_20 else 0.18,
                days_to_earnings=days_to_earnings,
                vix_term_structure=vix_term_structure,
                expected_move_pct=expected_move_pct,
                iv_percentile=iv_percentile,
                iv_skew=iv_skew,
                short_interest=short_interest,
                earnings_move=earnings_move,
            )
            return {
                "score": result.overall_score,
                "signal": result.overall_signal.value,
                "regime": result.regime,
                "strategy": result.best_strategy,
                "strategy_reasoning": result.best_strategy_reasoning,
                "iv_rank": (result.iv_signal or {}).get("iv_rank"),
                "iv_percentile": (result.iv_signal or {}).get("iv_percentile"),
                "iv_hv_ratio": (result.iv_signal or {}).get("ratio"),
                "iv_hv_signal": (result.iv_signal or {}).get("signal"),
                "rv_band": _rv_band(current_iv, hv_20),
                "flow_signals": _flow_signals(chain),
                "expected_move_pct": (result.iv_signal or {}).get("expected_move_pct"),
                "term_structure": (result.iv_signal or {}).get("term_structure"),
                "iv_skew": (result.iv_signal or {}).get("iv_skew"),
                "short_interest": (result.iv_signal or {}).get("short_interest"),
                "earnings_move": (result.iv_signal or {}).get("earnings_move"),
                "top_signal": "",
            }, None
        except Exception:
            logger.exception("Brain analysis failed for %s", symbol)
            return None, "brain_error"

    async def scan_once(self, symbols: Optional[List[str]] = None) -> int:
        """Run one full scan pass over *symbols* (or the auto-built universe).

        Returns the count of new notifications generated.
        """
        if symbols is None:
            symbols = await build_scan_universe()

        new_count = 0
        results: Dict[str, dict] = {}
        skipped: Dict[str, int] = {}

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
            data, skip_reason = await self._analyze_one(symbol)
            if data is None:
                reason = skip_reason or "unknown"
                skipped[reason] = skipped.get(reason, 0) + 1
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
                    "id": f"NTF-{symbol}-{int(datetime.now(timezone.utc).timestamp())}",
                    "symbol": symbol,
                    "score": data["score"],
                    "signal": data["signal"],
                    "regime": data["regime"],
                    "best_strategy": data["strategy"],
                    "strategy_reasoning": data.get("strategy_reasoning", ""),
                    "iv_rank": data.get("iv_rank"),
                    "iv_hv_ratio": data.get("iv_hv_ratio"),
                    "iv_hv_signal": data.get("iv_hv_signal"),
                    "iv_skew": data.get("iv_skew"),
                    "short_interest": data.get("short_interest"),
                    "earnings_move": data.get("earnings_move"),
                    "top_signal": data.get("top_signal", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "last_full_run": datetime.now(timezone.utc).isoformat(),
        })

        state = self._read_json(SCAN_STATE_FILE)
        now = datetime.now(timezone.utc)
        state["last_run"] = now.isoformat()
        state["next_run"] = (now + timedelta(seconds=self.interval)).isoformat()
        state["symbols_scanned"] = len(results)
        state["symbols_with_trades"] = sum(
            1 for result in results.values()
            if result.get("strategy") not in NON_ACTIONABLE_STRATEGIES
            and abs(result["score"]) >= NOTIFICATION_SCORE_FLOOR
        )
        state["scan_diagnostics"] = {
            "input_symbols": len(symbols),
            "analyzed_symbols": len(results),
            "skipped_symbols": skipped,
        }
        state["errors"] = [
            f"{count} symbols skipped: {reason.replace('_', ' ')}"
            for reason, count in sorted(skipped.items())
        ]
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
