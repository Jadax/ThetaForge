"""
Background Brain Scanner.
Runs the AI Brain on the full tradeable universe periodically in a background
asyncio task. Detects new actionable trade opportunities and generates
persistent notifications that the dashboard can surface.

Per-symbol analysis runs in FORKED WORKER PROCESSES recycled every few tasks
(Linux/Render) rather than threads: yfinance/curl_cffi/bs4 allocation churn
fragments pymalloc arenas -- measured 35 MB of live objects vs 282 MB RSS
after gc -- and fragmented arenas are never returned to the OS inside one
process, which is what produced the exit-137 OOM kills mid-pass. A recycled
child hands ALL of its memory back at exit. Windows (no fork) and pytest run
the identical analysis inline on threads instead.
Data flows: IBKR bridge (live) -> FreeDataProvider (yfinance fallback).
"""
import json
import logging
import math
import os
import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import httpx

from agents.data_ingestion.free_data import FreeDataProvider
from agents.trade_engine.macro_calendar import macro_days_until

logger = logging.getLogger(__name__)

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8002")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SCAN_RESULTS_FILE = os.path.join(DATA_DIR, "brain_scan_results.json")
SCAN_NOTIFICATIONS_FILE = os.path.join(DATA_DIR, "brain_notifications.json")
SCAN_STATE_FILE = os.path.join(DATA_DIR, "brain_scan_state.json")
PCR_HISTORY_FILE = os.path.join(DATA_DIR, "pcr_history.json")

# Background alerts are discovery candidates, but they should still meet the
# same high-conviction floor used by the detailed Advisor recommendation path.
NOTIFICATION_SCORE_FLOOR = 75
NON_ACTIONABLE_STRATEGIES = {"no_trade", "avoid_new_positions", "roll_or_close"}

# Bounded fan-out for per-symbol analysis in scan_once(). This is a rate-limit
# tradeoff, not just a speed one: at concurrency=20, Render's outbound IP got
# CBOE-429'd on almost every request during a live scan (confirmed via
# Render's logs), and the resulting burst of failed requests appears to have
# starved the app's ability to serve other concurrent API calls at the same
# time -- other /api/advisor/* routes returned 502 for the duration of the
# scan while /health/ (no CBOE calls) kept responding. The same test run from
# a residential IP hit zero 429s, so this is specific to Render's IP, not a
# universal CBOE limit -- re-verify against Render's actual logs after
# changing this, a local measurement will not reproduce the failure.
SCAN_CONCURRENCY = 3

# Hold the first scan tick after boot briefly so a fresh deploy can pass its
# platform health checks before any heavy work starts -- Render probes /health/
# right after boot, and a scan starting instantly on a 0.1-vCPU instance was
# failing those probes and flapping the service during market-hours deploys.
STARTUP_GRACE_SECONDS = 90

_EASTERN = ZoneInfo("America/New_York")
_nyse_calendar = None

# Plausibility bounds for annualized IV off the free chains. Pre-market/
# weekend CBOE snapshots routinely carry near-zero IVs on every contract;
# averaging those produced iv_rank clamped to 0, "very_cheap" vol bands, and
# garbage scores across the whole universe (fail-open poisoning). Anything
# outside these bounds is treated as chain data being unavailable.
MIN_PLAUSIBLE_IV = 0.01
MAX_PLAUSIBLE_IV = 5.0


class DegenerateChainError(Exception):
    """Chain IVs exist but are outside plausible bounds (e.g. pre-open zeros)."""


def _plausible_iv(value) -> bool:
    try:
        iv = float(value)
    except (TypeError, ValueError):
        return False
    return MIN_PLAUSIBLE_IV <= iv <= MAX_PLAUSIBLE_IV

def _get_nyse_calendar():
    global _nyse_calendar
    if _nyse_calendar is None:
        import pandas_market_calendars as mcal
        _nyse_calendar = mcal.get_calendar("NYSE")
    return _nyse_calendar
# Per-day cache of (market_open, market_close) as tz-aware UTC datetimes, or
# None on a weekend/holiday. A day's session never changes once computed, and
# this is checked on every scan-loop tick plus every dashboard status poll,
# so caching avoids re-querying the calendar library dozens of times a day
# for an answer that cannot have changed. Trimmed so a long-running process
# can't grow this unbounded.
_schedule_cache: Dict[date, Optional[Tuple[datetime, datetime]]] = {}
_SCHEDULE_CACHE_MAX_DAYS = 30


def is_market_hours(moment: Optional[datetime] = None) -> bool:
    """Whether the NYSE regular session is open at *moment* (default: now).

    Uses pandas_market_calendars' real NYSE calendar rather than a hand-rolled
    weekday + 9:30-16:00 ET check, so this gets holidays right -- including
    observed-date shifts (July 4 falling on a Saturday closes the preceding
    Friday) and half days (the day after Thanksgiving closes at 1pm ET) --
    without needing yearly maintenance. Falls back to the plain weekday/clock
    check only if the calendar lookup itself errors, so a bug in that library
    degrades this rather than silently stopping the scanner.
    """
    now_utc = moment or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    try:
        today = now_utc.astimezone(_EASTERN).date()
        if today not in _schedule_cache:
            schedule = _get_nyse_calendar().schedule(start_date=today, end_date=today)
            if schedule.empty:
                _schedule_cache[today] = None
            else:
                _schedule_cache[today] = (
                    schedule.iloc[0]["market_open"].to_pydatetime(),
                    schedule.iloc[0]["market_close"].to_pydatetime(),
                )
            if len(_schedule_cache) > _SCHEDULE_CACHE_MAX_DAYS:
                del _schedule_cache[min(_schedule_cache)]

        session = _schedule_cache[today]
        if session is None:
            return False
        market_open, market_close = session
        return market_open <= now_utc <= market_close
    except Exception:
        logger.exception("NYSE calendar lookup failed; falling back to a plain weekday/clock check")
        now_et = now_utc.astimezone(_EASTERN)
        if now_et.weekday() >= 5:
            return False
        open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_time = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_time <= now_et <= close_time


def _atm_iv(chain: List[Dict]) -> Optional[float]:
    """Best-effort ATM implied volatility from the nearest-expiry chain.

    Order of preference, all on the front (nearest-dte) expiry:
    1. average of the options closest to 50% delta (a 0.50-delta straddle);
    2. if deltas are missing, the strike where call IV == put IV (IV parity
       holds best at the money — a spot-free ATM estimate);
    3. front-expiry median IV (never the whole-chain mean, which distorts
       badly on wide chains via far-OTM inflated IVs).
    """
    if not chain:
        return None
    front_dte = min((int(opt.get("dte") or 10**6) for opt in chain), default=None)
    if front_dte is None:
        return None
    front = [opt for opt in chain if int(opt.get("dte") or 0) == front_dte]

    delta_pairs = []
    for opt in front:
        iv = opt.get("implied_volatility")
        if not _plausible_iv(iv):
            continue
        delta = opt.get("delta")
        if delta is not None:
            try:
                delta_pairs.append((abs(float(delta) - 0.5), float(iv)))
            except (TypeError, ValueError):
                continue
    if delta_pairs:
        near = [pair for pair in delta_pairs if pair[0] <= 0.05]
        pool = near if near else [min(delta_pairs, key=lambda pair: pair[0])]
        return sum(iv for _, iv in pool) / len(pool)

    # No deltas: find the strike where the call and put IVs converge.
    by_strike: Dict[float, Dict[str, float]] = {}
    for opt in front:
        iv = opt.get("implied_volatility")
        strike = opt.get("strike")
        if not _plausible_iv(iv) or strike is None:
            continue
        bucket = by_strike.setdefault(float(strike), {})
        bucket[opt.get("option_type", "").lower()] = float(iv)
    parity = [
        (abs(bucket["call"] - bucket["put"]), (bucket["call"] + bucket["put"]) / 2)
        for bucket in by_strike.values()
        if "call" in bucket and "put" in bucket
    ]
    if parity:
        return min(parity, key=lambda pair: pair[0])[1]

    front_ivs = sorted(
        float(opt["implied_volatility"])
        for opt in front
        if _plausible_iv(opt.get("implied_volatility"))
    )
    if not front_ivs:
        return None
    return front_ivs[len(front_ivs) // 2]


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


def _no_trade_reason_code(strategy: str, reasoning: str) -> str:
    """Short, tallyable reason a symbol is not tradeable.

    The Brain emits a full prose ``reasoning`` string; this derives a stable
    code so scan diagnostics can count how many symbols are paused by each
    gate (the reason this pipeline went two weeks with zero trades undiagnosed
    was that no-trade results were persisted without their reasoning).
    """
    if strategy != "no_trade":
        return strategy
    reason = (reasoning or "").lower()
    if "insufficient confirmation" in reason:
        return "low_confidence"
    if "term structure inverted" in reason:
        return "inverted_term_structure"
    if "macro event" in reason:
        return "macro_proximity"
    if "extreme" in reason and "vix" in reason:
        return "high_vix"
    if "earnings" in reason:
        return "earnings_proximity"
    if "differentiated edge" in reason:
        return "no_edge"
    if "confirmed downtrend" in reason or "confirmed uptrend" in reason:
        return "trend_mismatch"
    if "laggard" in reason:
        return "laggard"
    return "other"


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


def _flow_data(chain: List[Dict], stock_price: float, iv: Optional[float]) -> Optional[Dict]:
    """Directional smart-money flow summary the Brain's flow engine expects.

    Runs the UnusualActivityDetector over the free chain and aggregates the
    results into the exact shape AIBrain.analyze() consumes (total_signals,
    bias, premium totals, bullish/bearish counts). Fails closed: no unusual
    activity -> None, and the Brain treats a missing flow read as neutral --
    it can never mint a trade by itself.
    """
    try:
        from agents.flow_analysis.unusual_activity import UnusualActivityDetector
    except ImportError:
        return None
    if not chain or not stock_price or stock_price <= 0:
        return None
    detector = UnusualActivityDetector()
    unusual = detector.scan_chain(chain, stock_price, iv or 0.20)
    if not unusual:
        return None
    return detector.aggregate_signals(unusual, [], [])


def _pcr_read(symbol: str, chain: List[Dict], store=None) -> Optional[Dict]:
    """Symbol-level put/call volume ratio plus its own history.

    PCR is a contrarian read, and it only means something relative to the
    symbol's own recent distribution, so today's ratio is appended to a
    per-symbol daily history before the sentiment engine is run. Volume is
    preferred; when a chain carries no volume it degrades to an OI ratio, and
    an empty chain degrades to None (neutral), never a signal.
    """
    if not chain:
        return None
    call_volume = 0.0
    put_volume = 0.0
    call_oi = 0.0
    put_oi = 0.0
    for opt in chain:
        opt_type = str(opt.get("option_type", "")).upper()
        if opt_type not in ("CALL", "PUT"):
            continue
        if opt_type == "CALL":
            call_volume += float(opt.get("volume") or 0)
            call_oi += float(opt.get("open_interest") or 0)
        else:
            put_volume += float(opt.get("volume") or 0)
            put_oi += float(opt.get("open_interest") or 0)

    if call_volume > 0 and put_volume > 0:
        current = put_volume / call_volume
    elif call_oi > 0 and put_oi > 0:
        current = put_oi / call_oi
    else:
        return None

    try:
        if store is None:
            from agents.volatility.pcr_history import PCRHistoryStore
            store = PCRHistoryStore(PCR_HISTORY_FILE)
        store.record(symbol, current)
        historical = store.history(symbol)
    except Exception:
        logger.exception("PCR history store failed; sentiment degrades to absolute thresholds")
        historical = []

    return {
        "current": round(current, 4),
        "historical": historical,
        "put_volume": round(put_volume, 0),
        "call_volume": round(call_volume, 0),
    }


def _gex_data(chain: List[Dict], stock_price: float) -> Optional[Dict]:
    """Dealer gamma-exposure regime from the free chain.

    Full-chain GEX is CPU-cheap for a single symbol (pure numpy/scipy per-row
    gamma) and the chain's own dte field avoids a strptime per option. Missing
    or unpriced chains fail closed to None; the Brain treats that as neutral.
    """
    try:
        from agents.flow_analysis.gex_engine import GEXEngine
    except ImportError:
        return None
    if not chain or not stock_price or stock_price <= 0:
        return None
    try:
        return GEXEngine().calculate_chain_gex(chain, stock_price)
    except Exception:
        logger.debug("GEX computation failed; dealer-positioning signal disabled", exc_info=True)
        return None


def _rss_mb() -> Optional[float]:
    """Resident set size in MB (Linux: ru_maxrss KB; best-effort, optional)."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return None


# ── per-symbol worker process plumbing ─────────────────────────────────────

_process_executor: Optional[ProcessPoolExecutor] = None
_worker_scanner_instance: Optional["BackgroundBrainScanner"] = None


def _use_process_workers() -> bool:
    """Fork-based workers only where fork exists and tests aren't running."""
    if os.name == "nt":
        return False
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False
    return True


def _get_process_executor() -> Optional[ProcessPoolExecutor]:
    global _process_executor
    if not _use_process_workers():
        return None
    if _process_executor is None:
        try:
            ctx = multiprocessing.get_context("fork")
            _process_executor = ProcessPoolExecutor(
                max_workers=SCAN_CONCURRENCY,
                max_tasks_per_child=6,
                mp_context=ctx,
            )
            logger.info(
                "Scan workers: forked process pool (max_workers=%d, recycle every 6 tasks)",
                SCAN_CONCURRENCY,
            )
        except Exception as error:
            logger.warning("Process pool unavailable (%s); using threads", error)
            _process_executor = False
    return _process_executor or None


def _worker_scanner() -> "BackgroundBrainScanner":
    """Per-process singleton for forked children (cheap after fork: the
    child inherits the parent's already-imported modules copy-on-write)."""
    global _worker_scanner_instance
    if _worker_scanner_instance is None:
        _worker_scanner_instance = BackgroundBrainScanner(interval_seconds=9999)
    return _worker_scanner_instance


def process_analyze_symbol(payload: Tuple[str, Optional[float], Optional[dict]]):
    """Forked-worker entry: analyze one symbol, return JSON-safe results.

    Runs its own event loop inside the child. Shared market-wide inputs ride
    in the payload; history stores are file-backed so children read the same
    state the parent maintains.
    """
    symbol, vix, term = payload
    scanner = _worker_scanner()
    if vix is not None:
        scanner._scan_vix = vix
    if term is not None:
        scanner._scan_vix_term = term
    data, skip = asyncio.run(scanner._analyze_one(symbol))
    return symbol, data, skip


def _enrich_and_analyze(
    brain,
    chain, price, closes, high_prices, low_prices, vix,
    current_iv, hv_20, iv_store, pcr_store,
    days_to_earnings, days_to_macro, vix_term_structure,
    expected_move_pct, iv_percentile, iv_skew,
    short_interest, vol_risk_premium,
    relative_strength, symbol,
    hist_frame=None, past_earnings_dates=None,
):
    """CPU-bound enrichment + Brain analysis, run in a thread via to_thread.

    Keeps the event loop free so health checks and other requests are not
    blocked by the ~0.1-1s of signal computation per symbol. Everything that
    touches the chain or history frames happens here -- including the
    desk-analytics earnings-move edge and the flow/rv-band summaries that an
    earlier version recomputed on the loop after the thread returned.
    """
    hv_20_val = hv_20
    try:
        from agents.volatility.iv_metrics import realized_volatility
        hv_20_val = realized_volatility(closes)
    except Exception:
        pass

    cur_iv = current_iv
    exp_move = expected_move_pct
    try:
        from agents.trade_engine.analytics import OptionsAnalytics
        cur_iv = _atm_iv(chain)
        if not cur_iv:
            present_ivs = [
                opt.get("implied_volatility") for opt in chain
                if opt.get("implied_volatility") is not None
            ]
            ivs = [float(v) for v in present_ivs if _plausible_iv(v)]
            # IVs exist but every one is implausible: degenerate pre-open
            # snapshot -- stop here rather than fabricate a neutral default.
            if present_ivs and not ivs:
                sample = float(present_ivs[0])
                raise DegenerateChainError(
                    f"all {len(present_ivs)} chain IVs outside plausible "
                    f"bounds (sample {sample:.4f})"
                )
            if ivs:
                cur_iv = sorted(ivs)[len(ivs) // 2]
        # A chain whose IVs exist but are implausible (pre-open zero-IV
        # snapshots are the recurring case) must stop the analysis here --
        # every downstream read (IV rank, VRP, expected move, rv band)
        # inherits the poison and fabricates a coherent-looking signal.
        if cur_iv is not None and not _plausible_iv(cur_iv):
            raise DegenerateChainError(
                f"ATM IV {cur_iv:.4f} outside plausible bounds"
            )
        if cur_iv and cur_iv > 0 and price > 0:
            move = OptionsAnalytics().expected_move(price, cur_iv, 30)
            exp_move = move.get("expected_move_pct")
    except DegenerateChainError:
        raise
    except Exception:
        cur_iv = None

    iv_pct = iv_percentile
    iv_bounds = None
    vrp = vol_risk_premium
    try:
        store = iv_store
        if store is not None:
            from agents.volatility.iv_history import MIN_SAMPLES
            store.record(symbol, cur_iv, hv_20_val)
            iv_pct = store.iv_percentile(symbol, cur_iv)
            if store.sample_count(symbol) >= MIN_SAMPLES:
                iv_bounds = store.iv_52w_range(symbol, cur_iv)
            vrp_z = store.vrp_zscore(symbol, cur_iv, hv_20_val)
            vrp = {
                "vrp": round(cur_iv - hv_20_val, 4) if cur_iv and hv_20_val else None,
                "vrp_z": vrp_z,
                "iv_change_5d": store.iv_change_5d(symbol),
            }
    except Exception:
        pass

    try:
        days_to_macro = macro_days_until()
    except Exception:
        days_to_macro = None

    try:
        iv_skew_val = calculate_iv_skew(chain) if iv_skew is None else iv_skew
    except Exception:
        iv_skew_val = None

    # Earnings implied-vs-realized move edge: implied move needs ATM IV (only
    # known above), realized moves need the past earnings calendar fetched
    # async-side. Missing pieces degrade to None, never a fabricated edge.
    earnings_move_val = None
    try:
        from agents.volatility.desk_analytics import (
            implied_earnings_move,
            historical_earnings_moves,
            earnings_move_edge,
        )
        if cur_iv and price > 0 and hist_frame is not None and past_earnings_dates:
            implied = implied_earnings_move(chain, price)
            if implied:
                moves = historical_earnings_moves(hist_frame, past_earnings_dates)
                earnings_move_val = earnings_move_edge(implied, moves)
    except Exception:
        earnings_move_val = None

    flow_data = None
    pcr_data = None
    gex_data = None
    try:
        flow_data = _flow_data(chain, price, cur_iv)
        pcr_data = _pcr_read(symbol, chain, store=pcr_store)
        gex_data = _gex_data(chain, price)
    except Exception:
        pass

    rv_band_val = _rv_band(cur_iv, hv_20_val)
    flow_signals_val = _flow_signals(chain)

    analyze_kwargs = dict(
        symbol=symbol,
        stock_price=price,
        option_chain=chain,
        historical_prices=closes,
        high_prices=high_prices,
        low_prices=low_prices,
        vix=vix,
        current_iv=cur_iv if cur_iv else 0.20,
        hv_20=hv_20_val if hv_20_val else 0.18,
        days_to_earnings=days_to_earnings,
        days_to_macro=days_to_macro,
        vix_term_structure=vix_term_structure,
        expected_move_pct=exp_move,
        iv_percentile=iv_pct,
        iv_skew=iv_skew_val,
        short_interest=short_interest,
        earnings_move=earnings_move_val,
        vol_risk_premium=vrp,
        relative_strength=relative_strength,
    )
    if flow_data:
        analyze_kwargs["flow_data"] = flow_data
    if pcr_data:
        analyze_kwargs["pcr_data"] = pcr_data
    if gex_data:
        analyze_kwargs["gex_data"] = gex_data
    if iv_bounds:
        analyze_kwargs["iv_52w_high"] = iv_bounds["iv_52w_high"]
        analyze_kwargs["iv_52w_low"] = iv_bounds["iv_52w_low"]

    result = brain.analyze(**analyze_kwargs, record_feedback=True)

    return (
        result, flow_data, pcr_data, gex_data,
        cur_iv, hv_20_val, rv_band_val, flow_signals_val, earnings_move_val,
    )


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
        "thesis": "Elevated-IVR underlyings for cash-secured puts / covered calls / put credit",
        "match": lambda r: (r.get("iv_rank") or 0) >= 35
        and r.get("strategy") in {"cash_secured_put", "covered_call", "bull_put_credit"},
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
        self._spy_126_return_cache = None
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
        # Compact separators: these are machine-read state files; indent=2
        # roughly doubled multi-MB scan-result serialization time on the host.
        with open(path, "w") as f:
            json.dump(data, f, separators=(",", ":"))

    async def _read_json_async(self, path: str):
        return await asyncio.to_thread(self._read_json, path)

    async def _write_json_async(self, path: str, data) -> None:
        await asyncio.to_thread(self._write_json, path, data)

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

    async def _spy_126_return(self) -> float:
        """6-month SPY return (fractions) for relative-strength vs the market.

        Fetched once per process and shared across the concurrent per-symbol
        analysis fan-out, so a full scan adds at most one SPY request. Missing
        data degrades to 0.0 — relative strength then equals the symbol's own
        return, which keeps a scan pass alive instead of failing it.
        """
        if self._spy_126_return_cache is None:
            self._spy_126_return_cache = 0.0
            try:
                hist = await self._provider.get_historical_prices("SPY", period="6mo")
                if hist is not None:
                    closes = hist["Close"].tolist() if hasattr(hist, "Close") else []
                    if len(closes) >= 2 and closes[0]:
                        self._spy_126_return_cache = closes[-1] / closes[0] - 1
            except Exception:
                logger.warning("SPY history unavailable; relative strength disabled this scan")
        return self._spy_126_return_cache

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

        # Self-learning feedback: score any due recorded predictions for this
        # symbol against the price we already fetched (no extra network call).
        # Advisory-only and fail-closed -- a tracker hiccup never breaks a scan.
        # Offloaded to a thread: the tracker reads/writes its JSON store, and
        # per-symbol sync file I/O on the loop was part of the health-check
        # starvation.
        try:
            await asyncio.to_thread(self._brain.record_outcome, symbol, price)
        except Exception:
            pass

        try:
            chain = await self._provider.get_option_chain(symbol) or []
        except Exception:
            return None, "option_chain_unavailable"
        if not chain:
            return None, "option_chain_unavailable"

        try:
            vix = getattr(self, "_scan_vix", None)
            if vix is None:
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
        current_iv = None
        hv_20 = None
        expected_move_pct = None
        iv_percentile = None
        vol_risk_premium = None
        iv_skew = None
        days_to_macro = None
        relative_strength = None
        vix_term_structure = None
        days_to_earnings = None
        short_interest = None

        try:
            vix_term_structure = getattr(self, "_scan_vix_term", None)
            if vix_term_structure is None:
                vix_term_structure = await self._provider.get_vix_term_structure()
        except Exception:
            vix_term_structure = None

        try:
            next_earnings = await self._provider.get_next_earnings_date(symbol)
            days_to_earnings = (next_earnings - date.today()).days if next_earnings else None
        except Exception:
            days_to_earnings = None

        try:
            spy_ret = await self._spy_126_return()
            window = closes[-126:] if len(closes) >= 126 else closes
            if len(window) >= 2 and window[0]:
                symbol_ret = window[-1] / window[0] - 1
                relative_strength = symbol_ret - spy_ret
            else:
                relative_strength = None
        except Exception:
            relative_strength = None

        try:
            short_interest = await self._provider.get_short_interest(symbol)
        except Exception:
            short_interest = None

        # Past earnings dates for the implied-vs-realized move edge. The edge
        # itself is computed inside the worker thread once ATM IV is known --
        # an earlier version gated this block on `current_iv`, which is only
        # set inside the thread, so the feature silently never ran.
        past_earnings_dates = []
        if days_to_earnings is not None:
            try:
                earnings_dates = await self._provider.get_earnings_dates(symbol, limit=12)
                past_earnings_dates = [e for e in earnings_dates if e < date.today()]
            except Exception:
                past_earnings_dates = []

        try:
            brain = await self._lazy_brain()

            result, flow_data, pcr_data, gex_data, current_iv, hv_20, rv_band, flow_signals, earnings_move = (
                await asyncio.to_thread(
                    _enrich_and_analyze,
                    brain, chain, price, closes, high_prices, low_prices, vix,
                    None, None,
                    getattr(self, "_scan_iv_store", None),
                    getattr(self, "_scan_pcr_store", None),
                    days_to_earnings, days_to_macro, vix_term_structure,
                    expected_move_pct, iv_percentile, iv_skew,
                    short_interest, vol_risk_premium,
                    relative_strength, symbol,
                    hist_frame=hist,
                    past_earnings_dates=past_earnings_dates,
                )
            )
            return {
                "score": result.overall_score,
                "signal": result.overall_signal.value,
                "regime": result.regime,
                "strategy": result.best_strategy,
                "strategy_reasoning": result.best_strategy_reasoning,
                "no_trade_reason": _no_trade_reason_code(result.best_strategy, result.best_strategy_reasoning),
                "confidence": result.confidence,
                "price": price,
                "vix": vix,
                "days_to_earnings": days_to_earnings,
                "macro_days_until": days_to_macro,
                "iv_rank": (result.iv_signal or {}).get("iv_rank"),
                "iv_percentile": (result.iv_signal or {}).get("iv_percentile"),
                "eff_iv_rank": (result.iv_signal or {}).get("eff_iv_rank"),
                "iv_hv_ratio": (result.iv_signal or {}).get("ratio"),
                "iv_hv_signal": (result.iv_signal or {}).get("signal"),
                "rv_band": rv_band,
                "flow_signals": flow_signals,
                "flow_bias": (flow_data or {}).get("bias"),
                "pcr_signal": result.sentiment_signal or {},
                "pcr": (pcr_data or {}).get("current"),
                "gex_regime": (gex_data or {}).get("gex_regime"),
                "expected_move_pct": (result.iv_signal or {}).get("expected_move_pct"),
                "term_structure": (result.iv_signal or {}).get("term_structure"),
                "iv_skew": (result.iv_signal or {}).get("iv_skew"),
                "short_interest": (result.iv_signal or {}).get("short_interest"),
                "earnings_move": earnings_move,
                "vol_risk_premium": (result.iv_signal or {}).get("vol_risk_premium"),
                "relative_strength": result.relative_strength,
                "top_signal": "",
            }, None
        except DegenerateChainError as error:
            logger.info("%s skipped: %s", symbol, error)
            return None, "iv_degenerate"
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
        no_trade_reasons: Dict[str, int] = {}

        # Clean old no_trade notifications so stale entries don't linger
        old_notifs = await self._read_json_async(SCAN_NOTIFICATIONS_FILE)
        notifs = [
            notification for notification in old_notifs
            if notification.get("best_strategy") not in NON_ACTIONABLE_STRATEGIES
            and abs(float(notification.get("score", 0) or 0)) >= NOTIFICATION_SCORE_FLOOR
        ]

        # VIX and its term structure are market-wide — fetch once per scan
        # pass instead of per-symbol (130× fewer network calls).
        try:
            _scan_vix = await self._provider.get_vix()
        except Exception:
            _scan_vix = None
        try:
            _scan_vix_term = await self._provider.get_vix_term_structure()
        except Exception:
            _scan_vix_term = None
        self._scan_vix = _scan_vix
        self._scan_vix_term = _scan_vix_term

        # Shared history stores: one instance per scan pass instead of per
        # symbol (each caches the full JSON file -- per-symbol instances were
        # the v1.17.4 OOM). Forked analysis workers don't inherit these
        # per-pass attributes, so they lazily create their own file-backed
        # instances here; writes are atomic replaces, so parent and workers
        # can share the files safely.
        if getattr(self, "_scan_iv_store", None) is None:
            from agents.volatility.iv_history import IVHistoryStore
            self._scan_iv_store = IVHistoryStore()
        if getattr(self, "_scan_pcr_store", None) is None:
            from agents.volatility.pcr_history import PCRHistoryStore
            self._scan_pcr_store = PCRHistoryStore()

        # Every symbol's analysis is I/O-bound (price, chain, VIX, history,
        # desk analytics — each its own network round trip), so a sequential
        # loop over ~130+ symbols takes minutes. On a request-driven host
        # billed by wall-clock instance time, that directly costs money; it
        # also just makes the scan slow everywhere. Bounded concurrent
        # fan-out cuts wall time roughly by SCAN_CONCURRENCY. The semaphore
        # keeps us from hammering yfinance/CBOE with 100+ simultaneous
        # requests. Notification/results bookkeeping below stays a plain
        # sequential loop over the gathered results — unchanged from before —
        # so only one coroutine ever reads/writes the notifications file.
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _analyze_bounded(symbol: str) -> Tuple[str, Optional[dict], Optional[str]]:
            async with semaphore:
                data, skip_reason = await self._analyze_one(symbol)
                return symbol, data, skip_reason

        # Process in batches of _BATCH_SIZE instead of asyncio.gather on all
        # symbols at once: each analyzed symbol leaves ~10-13 MB of transient
        # allocation churn, so holding more than a handful of results at a
        # time only raises peak memory. With process workers the pool itself
        # caps concurrency; with threads the semaphore does. gc runs in a
        # thread (it can pause the loop for hundreds of ms with this many
        # live pandas objects) and each batch yields to the loop so health
        # checks are serviced even while workers saturate the single CPU.
        executor = _get_process_executor()
        _BATCH_SIZE = 6
        analyzed: List[Tuple[str, Optional[dict], Optional[str]]] = []
        for i in range(0, len(symbols), _BATCH_SIZE):
            batch = symbols[i : i + _BATCH_SIZE]
            if executor is not None:
                loop = asyncio.get_running_loop()
                payloads = [
                    (
                        symbol,
                        self._scan_vix if isinstance(getattr(self, "_scan_vix", None), (int, float)) else None,
                        self._scan_vix_term if isinstance(getattr(self, "_scan_vix_term", None), dict) else None,
                    )
                    for symbol in batch
                ]
                batch_results = await asyncio.gather(*(
                    loop.run_in_executor(executor, process_analyze_symbol, payload)
                    for payload in payloads
                ))
            else:
                batch_results = await asyncio.gather(
                    *(_analyze_bounded(symbol) for symbol in batch)
                )
            analyzed.extend(batch_results)
            import gc
            await asyncio.to_thread(gc.collect)
            # Peak-RSS breadcrumbs: exit 137 (OOM kill) during a pass was
            # invisible from logs until this. ru_maxrss is the high-water mark,
            # which is exactly the number racing the container limit.
            rss = _rss_mb()
            if rss:
                logger.info(
                    "scan batch %d/%d done (%d symbols), peak RSS %.0f MB",
                    i // _BATCH_SIZE + 1,
                    (len(symbols) + _BATCH_SIZE - 1) // _BATCH_SIZE,
                    len(analyzed),
                    rss,
                )
            await asyncio.sleep(0)

        new_notifications: List[dict] = []
        alert_rows: Dict[str, dict] = {}
        for symbol, data, skip_reason in analyzed:
            if data is None:
                reason = skip_reason or "unknown"
                skipped[reason] = skipped.get(reason, 0) + 1
                continue

            alert_rows[symbol] = data

            # Only alert on tradeable signals — skip no_trade. The no-trade
            # rows keep their full analysis payload (reasoning, confidence,
            # vol context) so the scan results answer *why* nothing traded —
            # the exact gap that hid the confidence-gate blockage for weeks.
            if data["strategy"] in NON_ACTIONABLE_STRATEGIES:
                reason_code = data.get("no_trade_reason") or data["strategy"]
                no_trade_reasons[reason_code] = no_trade_reasons.get(reason_code, 0) + 1
                results[symbol] = {
                    "score": data["score"],
                    "signal": data["signal"],
                    "regime": data.get("regime"),
                    "strategy": data["strategy"],
                    "filtered": "no_trade",
                    "strategy_reasoning": data.get("strategy_reasoning", ""),
                    "no_trade_reason": reason_code,
                    "confidence": data.get("confidence"),
                    "vix": data.get("vix"),
                    "days_to_earnings": data.get("days_to_earnings"),
                    "iv_rank": data.get("iv_rank"),
                    "iv_percentile": data.get("iv_percentile"),
                    "eff_iv_rank": data.get("eff_iv_rank"),
                    "iv_hv_signal": data.get("iv_hv_signal"),
                    "term_structure": data.get("term_structure"),
                    "rv_band": data.get("rv_band"),
                    "expected_move_pct": data.get("expected_move_pct"),
                    "vol_risk_premium": data.get("vol_risk_premium"),
                    "relative_strength": data.get("relative_strength"),
                    "flow_bias": data.get("flow_bias"),
                    "pcr_signal": data.get("pcr_signal"),
                    "gex_regime": data.get("gex_regime"),
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
                new_notifications.append(notif)
                notifs.append(notif)
                new_count += 1

            results[symbol] = {
                "score": data["score"],
                "signal": data["signal"],
                "regime": data.get("regime"),
                "strategy": data["strategy"],
                "iv_rank": data.get("iv_rank"),
                "iv_percentile": data.get("iv_percentile"),
                "eff_iv_rank": data.get("eff_iv_rank"),
                "iv_hv_signal": data.get("iv_hv_signal"),
                "rv_band": data.get("rv_band"),
                "expected_move_pct": data.get("expected_move_pct"),
                "term_structure": data.get("term_structure"),
                "flow_signals": data.get("flow_signals"),
                "flow_bias": data.get("flow_bias"),
                "pcr_signal": data.get("pcr_signal"),
                "gex_regime": data.get("gex_regime"),
                "vol_risk_premium": data.get("vol_risk_premium"),
            }

        # Threshold alerts run once per pass over every analyzed symbol --
        # tradeable or not -- so rules like "VIX above" fire regardless of
        # signal. One engine call instead of one file-read per symbol: the
        # per-symbol version was 130 sync reads (+ fsync writes on triggers)
        # on the event loop each pass. Off-thread and advisory-only.
        if alert_rows:
            try:
                await asyncio.to_thread(self._run_alert_checks, alert_rows)
            except Exception:
                logger.exception("Alert evaluation failed for the scan pass")

        # Single write per pass for results, notifications, and state -- an
        # earlier version re-read + re-wrote the notifications file for every
        # new notification (O(N^2) serialization on the loop).
        await self._write_json_async(SCAN_NOTIFICATIONS_FILE, notifs[-500:])
        await self._write_json_async(SCAN_RESULTS_FILE, {
            "symbols": results,
            "last_full_run": datetime.now(timezone.utc).isoformat(),
        })

        state = await self._read_json_async(SCAN_STATE_FILE)
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
            "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        }
        state["errors"] = [
            f"{count} symbols skipped: {reason.replace('_', ' ')}"
            for reason, count in sorted(skipped.items())
        ]
        await self._write_json_async(SCAN_STATE_FILE, state)

        return new_count

    @staticmethod
    def _run_alert_checks(alert_rows: Dict[str, dict]) -> None:
        """One AlertEngine pass over all analyzed symbols (runs in a thread)."""
        from agents.trade_engine.alerts import AlertEngine
        AlertEngine().check(alert_rows)

    async def _run_loop(self):
        # Post-deploy grace: hold the first heavy tick briefly so the platform
        # health probe passes before any scan work competes for the CPU (see
        # STARTUP_GRACE_SECONDS). A stop request during the grace exits clean.
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=STARTUP_GRACE_SECONDS
            )
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop_event.is_set():
            try:
                if is_market_hours():
                    await self.scan_once()
                else:
                    # Skip the expensive universe scan outside the NYSE
                    # session -- there is no fresh market data to act on, and
                    # this is what actually keeps the automatic loop inside
                    # the free-tier compute budget (see SCAN_CONCURRENCY
                    # above). Still record that the loop is alive and why it
                    # did nothing, so /scanner/status reads as "closed", not
                    # as "broken". A manual POST /scanner/trigger always
                    # runs regardless of market hours.
                    await self._mark_skipped_for_closed_market()
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

    async def _mark_skipped_for_closed_market(self) -> None:
        """Record that the loop is alive and checked, purely diagnostic.

        Deliberately does not touch last_run/next_run/scan_diagnostics --
        those describe the last real scan's results, which are still valid
        and should not look reset just because the market is closed right
        now. get_status() computes the live market_open flag itself rather
        than trusting a persisted value here, so there is nothing to keep in
        sync as time passes. The file write runs in a thread: it lands on
        every closed-market tick (weekends included).
        """
        def _update() -> None:
            state = self._read_json(SCAN_STATE_FILE)
            state["last_closed_market_check"] = datetime.now(timezone.utc).isoformat()
            self._write_json(SCAN_STATE_FILE, state)

        try:
            await asyncio.to_thread(_update)
        except Exception:
            logger.exception("Closed-market check marker write failed")

    async def start(self):
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        state = await self._read_json_async(SCAN_STATE_FILE)
        state["is_running"] = True
        await self._write_json_async(SCAN_STATE_FILE, state)

    async def stop(self):
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
        except asyncio.TimeoutError:
            # A mid-scan stop cannot finish in 10s; cancel rather than hang
            # process shutdown (the platform kills us shortly anyway).
            self._task.cancel()
        self._task = None
        state = await self._read_json_async(SCAN_STATE_FILE)
        state["is_running"] = False
        await self._write_json_async(SCAN_STATE_FILE, state)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def get_notifications(self, unacknowledged_only: bool = False,
                                limit: int = 50) -> List[Dict]:
        notifs = await self._read_json_async(SCAN_NOTIFICATIONS_FILE)
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
        notifs = await self._read_json_async(SCAN_NOTIFICATIONS_FILE)
        for n in notifs:
            if n.get("id") == notification_id:
                n["acknowledged"] = True
                await self._write_json_async(SCAN_NOTIFICATIONS_FILE, notifs)
                return True
        return False

    async def acknowledge_all(self):
        notifs = await self._read_json_async(SCAN_NOTIFICATIONS_FILE)
        for n in notifs:
            n["acknowledged"] = True
        await self._write_json_async(SCAN_NOTIFICATIONS_FILE, notifs)

    async def get_status(self) -> Dict:
        state, notifs, last_results = await asyncio.gather(
            self._read_json_async(SCAN_STATE_FILE),
            self._read_json_async(SCAN_NOTIFICATIONS_FILE),
            self._read_json_async(SCAN_RESULTS_FILE),
        )
        unacked = [n for n in notifs if not n.get("acknowledged")]
        return {
            "is_running": self.is_running,
            "market_open": is_market_hours(),
            "last_run": state.get("last_run"),
            "next_run": state.get("next_run"),
            "last_closed_market_check": state.get("last_closed_market_check"),
            "interval_seconds": self.interval,
            "symbols_scanned_last_run": state.get("symbols_scanned", 0),
            "symbols_with_trades": state.get("symbols_with_trades", 0),
            # Why symbols were skipped last pass (rate limits, degenerate
            # chains, missing history): without this, an all-skip pass is
            # indistinguishable from a healthy empty one from the outside.
            "scan_diagnostics": state.get("scan_diagnostics", {}),
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
