"""
Free Multi-Source Data Provider.
Aggregates data from IBKR (primary, via the VM's read-only market-data
proxy when configured), CBOE delayed quotes (no key), and yfinance
(fallback). All sources are FREE with a brokerage account or no account
at all.
"""
import asyncio
import json
import logging
import math
import os
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date

import httpx
import yfinance as yf
import pandas as pd
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from agents.data_ingestion.cboe_data import CBOEDataProvider

logger = logging.getLogger(__name__)

# The VM's read-only IBKR market-data proxy (deployment/vm_market_data_service.py).
# Unset by default so this stays inert everywhere except where these env vars
# are actually configured (Render) -- get_option_chain falls through to CBOE
# then yfinance on any missing config, timeout, or error, exactly as it did
# before this existed.
IBKR_MARKET_DATA_URL = os.getenv("IBKR_MARKET_DATA_URL", "")
IBKR_MARKET_DATA_TOKEN = os.getenv("IBKR_MARKET_DATA_TOKEN", "")
IBKR_MARKET_DATA_TIMEOUT = float(os.getenv("IBKR_MARKET_DATA_TIMEOUT", "12"))


# ── Process-isolated HTML scraping ─────────────────────────────────────────
# yfinance's quote-profile and earnings scrapes run curl_cffi + BeautifulSoup
# and leave ~10-13 MB of cyclic DOM garbage behind per symbol. That memory is
# not leaked (gc reclaims every object) but FRAGMENTED into pymalloc arenas:
# measured live-objects vs RSS after gc = 35 MB vs 282 MB. No amount of
# in-process collecting returns it to the OS, so the three scrape-heavy,
# slow-moving calls below run in short-lived worker PROCESSES that are
# recycled after a few tasks -- the OS reclaims everything at exit. Pure-API
# calls (prices, chains, history) stay in-process; only HTML scraping pays
# the process tax.
def _scrape_next_earnings(symbol: str) -> Optional[date]:
    ticker = yf.Ticker(symbol)
    frame = ticker.get_earnings_dates(limit=4)
    if frame is None or frame.empty:
        return None
    today = pd.Timestamp.today().normalize()
    for index in frame.index:
        candidate = index
        if hasattr(index, "tzinfo") and getattr(index, "tzinfo", None) is not None:
            candidate = index.tz_convert(None)
        candidate = pd.Timestamp(candidate).normalize()
        if candidate >= today:
            return candidate.date()
    return None


def _scrape_earnings_dates(symbol: str, limit: int) -> List[date]:
    ticker = yf.Ticker(symbol)
    frame = ticker.get_earnings_dates(limit=limit)
    if frame is None or frame.empty:
        return []
    dates: List[date] = []
    for index in frame.index:
        candidate = index
        if hasattr(index, "tzinfo") and getattr(index, "tzinfo", None) is not None:
            candidate = index.tz_convert(None)
        dates.append(pd.Timestamp(candidate).normalize().date())
    return sorted(dates)


def _scrape_short_interest(symbol: str) -> Optional[Dict[str, Any]]:
    ticker = yf.Ticker(symbol)
    info = ticker.info
    if not isinstance(info, dict):
        return None
    short_percent = info.get("shortPercentOfFloat")
    days_to_cover = info.get("shortRatio")
    shares_short = info.get("sharesShort")
    if short_percent is None and days_to_cover is None and shares_short is None:
        return None
    return {
        "short_percent_of_float": (
            round(float(short_percent) * 100, 2) if short_percent is not None else None
        ),
        "days_to_cover": (
            round(float(days_to_cover), 2) if days_to_cover is not None else None
        ),
        "shares_short": (
            int(float(shares_short)) if shares_short is not None else None
        ),
    }


_scrape_pool = None

# Recycled after this many scrape tasks: bounds how much fragmented curl_cffi/
# BeautifulSoup garbage a single still-alive scrape child can accumulate before
# the OS reclaims it at exit (see _get_scrape_pool).
SCRAPE_TASKS_PER_CHILD = 6


def _get_scrape_pool() -> Optional[ProcessPoolExecutor]:
    """Recycled process pool for the HTML-scraping calls -- ON by default.

    The three scrape-heavy calls (next_earnings, earnings_dates, short_interest)
    run curl_cffi + BeautifulSoup and leave ~10-13 MB of pyalloc-fragmented,
    unreclaimable RSS behind per call even after gc. That memory can only be
    returned to the OS by the process exiting, so scrape isolation is not an
    optional optimization -- it is what keeps a long sequential pass flat in
    memory when analysis runs in parent threads (fork workers disabled).

    A single spawn worker is recycled every SCRAPE_TASKS_PER_CHILD tasks, so at
    any moment at most one ~140 MB scrape interpreter exists alongside the
    ~120 MB parent -- comfortably under the 512 MB container (unlike the
    earlier pool(2)+fork+parent combination that crossed it). Pure-API calls
    (prices, chains, history) stay in-process; only HTML scraping pays the
    process tax. Set TF_SCRAPE_POOL=0 only to force in-process scraping on a
    host where you can guarantee a small universe.
    """
    global _scrape_pool
    if _scrape_pool is not None:
        return _scrape_pool or None
    if os.getenv("TF_SCRAPE_POOL", "1") != "1":
        _scrape_pool = False
        return None
    try:
        ctx = multiprocessing.get_context("spawn")
        _scrape_pool = ProcessPoolExecutor(
            max_workers=1,
            max_tasks_per_child=SCRAPE_TASKS_PER_CHILD,
            mp_context=ctx,
        )
        return _scrape_pool
    except Exception as error:  # pragma: no cover - platform-dependent
        logger.warning("Scrape worker pool unavailable (%s); scraping inline", error)
        _scrape_pool = False
        return None


class FreeDataProvider:
    """
    Multi-source data provider using only free APIs.
    Priority: IBKR (via the VM proxy, when configured) > CBOE (options) > yfinance
    """

    # Slow-moving fundamentals (earnings calendar, short interest) are memoized
    # for the rest of the UTC day. Scans run every 5 minutes; without this,
    # each pass re-scraped Yahoo's HTML profile pages for every symbol — the
    # curl_cffi/BeautifulSoup garbage from that is ~10-13 MB per symbol of
    # cyclic trash, and 25-symbol batches × two scanners peaked past Render's
    # 512 MB limit (SIGKILL/exit 137). Daily freshness is plenty for inputs
    # that change at most once per quarter.
    DAILY_MEMO_MAX = 600

    def __init__(self):
        self.cboe = CBOEDataProvider()
        self._daily_memo: Dict[str, Tuple[date, Any]] = {}
        self._memo_loaded = False

    # Disk-backed memo: scan analysis runs inside recycled fork workers whose
    # memory (including this dict) dies with each child; persisting it lets a
    # fresh fork inherit today's scrapes instead of re-fetching the whole
    # universe every generation. One small JSON file, atomic replace.
    MEMO_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "provider_daily_memo.json",
    )

    def _load_memo(self) -> None:
        if self._memo_loaded:
            return
        self._memo_loaded = True
        try:
            with open(self.MEMO_FILE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                for key, entry in raw.items():
                    try:
                        day, value = entry
                        self._daily_memo[key] = (
                            date.fromisoformat(day), value,
                        )
                    except (TypeError, ValueError):
                        continue
        except (OSError, ValueError):
            pass

    def _persist_memo(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.MEMO_FILE), exist_ok=True)
            payload = {
                key: [day.isoformat(), value]
                for key, (day, value) in self._daily_memo.items()
            }
            tmp = f"{self.MEMO_FILE}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp, self.MEMO_FILE)
        except OSError:
            pass

    def _daily_cached(self, key: str) -> Any:
        self._load_memo()
        hit = self._daily_memo.get(key)
        if hit and hit[0] == date.today():
            return hit[1]
        return None

    def _daily_store(self, key: str, value: Any) -> None:
        self._load_memo()
        today = date.today()
        if len(self._daily_memo) >= self.DAILY_MEMO_MAX:
            for stale in [k for k, (d, _) in self._daily_memo.items() if d != today]:
                del self._daily_memo[stale]
            if len(self._daily_memo) >= self.DAILY_MEMO_MAX:
                for old in list(self._daily_memo)[: len(self._daily_memo) // 2]:
                    del self._daily_memo[old]
        self._daily_memo[key] = (today, value)
        self._persist_memo()

    async def _get_ibkr_proxy_chain(self, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """Pull a chain from the VM's read-only IBKR proxy. Returns None (never
        raises) on any failure so the caller always falls through to CBOE --
        a slow or down VM should degrade the data source, not the scan."""
        if not IBKR_MARKET_DATA_URL or not IBKR_MARKET_DATA_TOKEN:
            return None
        try:
            async with httpx.AsyncClient(timeout=IBKR_MARKET_DATA_TIMEOUT) as client:
                response = await client.get(
                    f"{IBKR_MARKET_DATA_URL}/option-chain/{symbol}",
                    headers={"X-Market-Data-Token": IBKR_MARKET_DATA_TOKEN},
                )
            if response.status_code != 200:
                logger.debug("IBKR proxy chain unavailable for %s: HTTP %s", symbol, response.status_code)
                return None
            chain = response.json()
            return chain or None
        except Exception as error:
            logger.debug("IBKR proxy chain unavailable for %s: %s", symbol, error)
            return None

    async def _get_ibkr_proxy_stock_price(self, symbol: str) -> Optional[float]:
        """Pull a snapshot stock quote from the VM's read-only IBKR proxy.
        Returns None (never raises) on any failure so the caller falls through
        to yfinance."""
        if not IBKR_MARKET_DATA_URL or not IBKR_MARKET_DATA_TOKEN:
            return None
        try:
            async with httpx.AsyncClient(timeout=IBKR_MARKET_DATA_TIMEOUT) as client:
                response = await client.get(
                    f"{IBKR_MARKET_DATA_URL}/stock/{symbol}",
                    headers={"X-Market-Data-Token": IBKR_MARKET_DATA_TOKEN},
                )
            if response.status_code != 200:
                logger.debug("IBKR proxy stock quote unavailable for %s: HTTP %s", symbol, response.status_code)
                return None
            payload = response.json()
            last = payload.get("last")
            ask = payload.get("ask")
            bid = payload.get("bid")
            for value in (last, ask, bid):
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
            return None
        except Exception as error:
            logger.debug("IBKR proxy stock quote unavailable for %s: %s", symbol, error)
            return None

    async def _get_ibkr_proxy_stock_history(self, symbol: str, period: str = "1y") -> Optional[List[Dict[str, Any]]]:
        """Pull daily OHLCV bars from the VM's read-only IBKR proxy. Returns
        None (never raises) on any failure so the caller falls through."""
        if not IBKR_MARKET_DATA_URL or not IBKR_MARKET_DATA_TOKEN:
            return None
        try:
            async with httpx.AsyncClient(timeout=IBKR_MARKET_DATA_TIMEOUT) as client:
                response = await client.get(
                    f"{IBKR_MARKET_DATA_URL}/stock-history/{symbol}",
                    params={"period": period},
                    headers={"X-Market-Data-Token": IBKR_MARKET_DATA_TOKEN},
                )
            if response.status_code != 200:
                logger.debug("IBKR proxy stock history unavailable for %s: HTTP %s", symbol, response.status_code)
                return None
            return response.json() or None
        except Exception as error:
            logger.debug("IBKR proxy stock history unavailable for %s: %s", symbol, error)
            return None

    async def get_stock_price(self, symbol: str) -> Optional[float]:
        """Get current stock price from any available source."""
        proxy_price = await self._get_ibkr_proxy_stock_price(symbol)
        if proxy_price:
            return proxy_price

        try:
            def fetch_price() -> float:
                ticker = yf.Ticker(symbol)
                return float(ticker.fast_info.last_price)

            return await asyncio.wait_for(asyncio.to_thread(fetch_price), timeout=15)
        except Exception as e:
            logger.warning(f"yfinance price fetch failed for {symbol}: {e}")
        return None

    async def get_option_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """Get full option chain. IBKR (via the VM's own market-data proxy)
        is the priority source when configured, since it's the account's own
        live-ish OPRA feed rather than a shared public endpoint.

        Falls back to the free CBOE delayed-quotes feed (full Greeks, no API
        key, 15-minute delayed NBBO) and then to yfinance.
        """
        ibkr_chain = await self._get_ibkr_proxy_chain(symbol)
        if ibkr_chain:
            return ibkr_chain

        try:
            chain = await self.cboe.get_option_chain(symbol)
            if chain:
                return chain
        except Exception as error:
            logger.debug("CBOE chain unavailable for %s: %s", symbol, error)

        try:
            def fetch_chain() -> List[Dict[str, Any]]:
                ticker = yf.Ticker(symbol)
                chain_data = []
                for exp in ticker.options[:8]:
                    chain = ticker.option_chain(exp)
                    for _, row in chain.calls.iterrows():
                        chain_data.append(self._parse_yf_option(row, exp, "CALL"))
                    for _, row in chain.puts.iterrows():
                        chain_data.append(self._parse_yf_option(row, exp, "PUT"))
                return chain_data

            return await asyncio.wait_for(asyncio.to_thread(fetch_chain), timeout=15)
        except Exception as e:
            logger.error(f"Option chain fetch failed for {symbol}: {e}")
        return []

    async def get_historical_iv(self, symbol: str, period: str = "1y") -> List[float]:
        """Get historical implied volatility using yfinance options data."""
        try:
            def _fetch():
                ticker = yf.Ticker(symbol)
                expirations = ticker.options
                if not expirations:
                    return []
                chain = ticker.option_chain(expirations[0])
                atm_calls = chain.calls[
                    (chain.calls['strike'] - chain.calls['strike'].median()).abs().argsort()[:1]
                ]
                if not atm_calls.empty:
                    return [float(atm_calls['impliedVolatility'].iloc[0])]
                return []
            return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=15)
        except Exception:
            pass
        return []

    async def get_historical_prices(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        """Get historical OHLCV data. IBKR (via the VM's read-only proxy) is
        the priority source when configured since it is the account's own feed;
        yfinance is the fallback."""
        if interval == "1d":
            bars = await self._get_ibkr_proxy_stock_history(symbol, period=period)
            if bars:
                try:
                    frame = pd.DataFrame(bars)
                    if {"close", "high", "low", "volume"}.issubset(frame.columns):
                        frame["Date"] = pd.to_datetime(frame["date"], errors="coerce")
                        frame = frame.set_index("Date")[["open", "high", "low", "close", "volume"]]
                        # Keep the provider contract identical to yfinance's
                        # OHLCV frame. The scanner and technical engines use
                        # canonical capitalized names regardless of source.
                        frame.columns = ["Open", "High", "Low", "Close", "Volume"]
                        frame = frame.dropna(subset=["Close"])
                        if not frame.empty:
                            return frame
                except Exception as error:
                    logger.debug("IBKR proxy history reshape failed for %s: %s", symbol, error)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: yf.Ticker(symbol).history(period=period, interval=interval)
                ), timeout=15
            )
        except Exception as e:
            logger.error(f"Historical data failed for {symbol}: {e}")
        return pd.DataFrame()

    async def get_vix(self) -> Optional[float]:
        """Get current VIX level."""
        try:
            def _fetch_vix() -> float:
                return float(yf.Ticker("^VIX").fast_info.last_price)
            return await asyncio.wait_for(asyncio.to_thread(_fetch_vix), timeout=15)
        except Exception:
            return None

    async def get_vix_term_structure(self) -> Dict[str, Optional[float]]:
        """Latest closes of VIX9D / VIX3M / VIX6M / VIX1Y.

        Primary source is Yahoo's VIX index tickers; the CBOE daily-history
        feed is the fallback.  A missing index stays None rather than being
        replaced with a placeholder.
        """
        structure: Dict[str, Optional[float]] = {
            index: None for index in ("VIX9D", "VIX3M", "VIX6M", "VIX1Y")
        }
        yahoo_map = {"VIX9D": "^VIX9D", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VIX1Y": "^VIX1Y"}

        def _fetch_yf_vix(ticker_symbol: str) -> Optional[float]:
            try:
                price = float(yf.Ticker(ticker_symbol).fast_info.last_price)
                return price if price > 0 else None
            except Exception:
                return None

        # Fetch all four VIX indices concurrently instead of sequentially.
        vix_tasks = {
            index: asyncio.wait_for(asyncio.to_thread(_fetch_yf_vix, sym), timeout=15)
            for index, sym in yahoo_map.items()
        }
        results = await asyncio.gather(*vix_tasks.values(), return_exceptions=True)
        for index, result in zip(vix_tasks.keys(), results):
            if isinstance(result, (int, float)):
                structure[index] = round(result, 2)

        if all(value is not None for value in structure.values()):
            return structure
        # Fill any gaps from the CBOE daily-history feed (no key required).
        try:
            cboe_structure = await self.cboe.get_vix_term_structure()
            for index, value in cboe_structure.items():
                if structure.get(index) is None:
                    structure[index] = value
        except Exception as error:
            logger.debug("CBOE VIX term structure unavailable: %s", error)
        return structure
        # Fill any gaps from the CBOE daily-history feed (no key required).
        try:
            cboe_structure = await self.cboe.get_vix_term_structure()
            for index, value in cboe_structure.items():
                if structure.get(index) is None:
                    structure[index] = value
        except Exception as error:
            logger.debug("CBOE VIX term structure unavailable: %s", error)
        return structure

    def is_vix_contango(self, term_structure: Dict[str, Optional[float]]) -> Optional[bool]:
        """True when the VIX term structure is in healthy contango
        (VIX9D < VIX3M < VIX6M), False when inverted, None when data is missing.

        Inversion means front-month fear is elevated — the worst regime for
        selling premium (Tastytrade pauses sellers on inverted structure).
        """
        vix9d = term_structure.get("VIX9D")
        vix3m = term_structure.get("VIX3M")
        vix6m = term_structure.get("VIX6M")
        if vix9d is None or vix3m is None:
            return None
        if vix9d >= vix3m:
            return False
        # Contango also fails when the mid part of the curve is already
        # dipping below the front (partial inversion).
        if vix6m is not None and vix6m < vix3m:
            return False
        return True

    async def _scrape(self, fn, *args):
        """Run a scrape worker in the recycling process pool; fall back to an
        inline thread if the pool is unavailable for any reason."""
        pool = _get_scrape_pool()
        if pool is not None:
            try:
                # run_in_executor bridges the concurrent.futures.Future from
                # the process pool into asyncio without blocking the loop.
                return await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(pool, fn, *args),
                    timeout=60,
                )
            except Exception as error:
                logger.debug("Scrape pool call failed (%s); inline retry", error)
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=30)

    async def get_next_earnings_date(self, symbol: str) -> Optional[date]:
        """Next scheduled earnings date via yfinance (ETFs return None)."""
        memo_key = f"next_earnings:{symbol.upper()}"
        cached = self._daily_cached(memo_key)
        if cached is not None or self._daily_memo.get(memo_key):
            return cached
        try:
            result = await self._scrape(_scrape_next_earnings, symbol)
        except Exception as error:
            logger.debug("Earnings date unavailable for %s: %s", symbol, error)
            return None
        self._daily_store(memo_key, result)
        return result

    async def get_short_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Short-interest profile via yfinance fundamentals (free).

        Desks read heavy short interest as a squeeze / gamma-amplification
        input: when shorts dominate and price turns up, covering forces fuel
        the move and retail option buyers get hurt selling into it. Returns
        None fail-closed when the ticker lacks the fields.
        """
        memo_key = f"short_interest:{symbol.upper()}"
        cached = self._daily_cached(memo_key)
        if cached is not None or self._daily_memo.get(memo_key):
            return cached
        try:
            result = await self._scrape(_scrape_short_interest, symbol)
        except Exception as error:
            logger.debug("Short interest unavailable for %s: %s", symbol, error)
            return None
        self._daily_store(memo_key, result)
        return result

    async def get_earnings_dates(
        self, symbol: str, limit: int = 12
    ) -> List[date]:
        """Past AND upcoming earnings dates via yfinance, oldest first."""
        memo_key = f"earnings_dates:{symbol.upper()}:{limit}"
        cached = self._daily_cached(memo_key)
        if cached is not None or self._daily_memo.get(memo_key):
            return cached or []
        try:
            result = await self._scrape(_scrape_earnings_dates, symbol, limit)
        except Exception as error:
            logger.debug("Earnings dates unavailable for %s: %s", symbol, error)
            return []
        self._daily_store(memo_key, result)
        return result

    async def get_vix_history(self, period: str = "1y") -> pd.DataFrame:
        """Get historical VIX data."""
        try:
            def _fetch():
                return yf.Ticker("^VIX").history(period=period)
            return await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=15)
        except Exception:
            return pd.DataFrame()

    async def get_put_call_ratio(self) -> Optional[float]:
        """Get CBOE put/call ratio (free from CBOE website)."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://cdn.cboe.com/data/us/equities/daily_statistics/TotalPutCallRatio.json",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "data" in data and data["data"]:
                        latest = data["data"][0]
                        return float(latest.get("P/C Ratio", 0))
        except Exception as e:
            logger.warning(f"CBOE put/call ratio fetch failed: {e}")
        return None

    async def get_active_stock_universe(self, limit: int = 80) -> List[str]:
        """Discover actively traded US equities from free Yahoo screeners.

        This broadens the static liquid-options seed list with current market
        leaders, movers, and liquid technology names. It is a discovery pass,
        not an assertion that every returned equity has usable options; the
        Advisor still validates chain liquidity before it can recommend one.
        """
        screeners = ("most_actives", "day_gainers", "day_losers", "growth_technology_stocks")

        def fetch_screener(name: str) -> List[str]:
            try:
                response = yf.screen(name, count=75)
                quotes = response.get("quotes", []) if isinstance(response, dict) else []
                return [str(item.get("symbol", "")).upper() for item in quotes if item.get("symbol")]
            except Exception as error:
                logger.warning("Yahoo screener failed for %s: %s", name, error)
                return []

        groups = await asyncio.gather(*(
            asyncio.wait_for(asyncio.to_thread(fetch_screener, name), timeout=30)
            for name in screeners
        ), return_exceptions=True)
        symbols: List[str] = []
        for group in groups:
            for symbol in group:
                if symbol not in symbols:
                    symbols.append(symbol)
                if len(symbols) >= limit:
                    return symbols
        return symbols

    async def get_sector_performance(self) -> Dict[str, float]:
        """Get sector ETF performance from yfinance."""
        sectors = {
            "XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare",
            "XLE": "Energy", "XLI": "Industrials", "XLP": "Consumer Staples",
            "XLY": "Consumer Discretionary", "XLU": "Utilities",
            "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Communication",
        }
        perf = {}

        def _fetch_one(etf: str) -> Optional[float]:
            try:
                hist = yf.Ticker(etf).history(period="5d")
                if len(hist) >= 2:
                    return round((hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100, 2)
            except Exception:
                pass
            return None

        tasks = {
            name: asyncio.wait_for(asyncio.to_thread(_fetch_one, etf), timeout=15)
            for etf, name in sectors.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for name, result in zip(tasks.keys(), results):
            if isinstance(result, (int, float)):
                perf[sectors[name]] = result
        return perf

    def _parse_yf_option(self, row, expiry: str, opt_type: str) -> Dict[str, Any]:
        # Yahoo frequently represents unavailable option fields as NaN. A
        # single missing volume/OI value must not discard the entire chain;
        # downstream liquidity rules will reject an individual incomplete leg.
        def finite_number(value: Any, default: float = 0.0) -> float:
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) else default
            except (TypeError, ValueError):
                return default

        try:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 0)
        except (TypeError, ValueError):
            dte = 0

        return {
            "symbol": row.get("contractSymbol", ""),
            "strike": finite_number(row.get("strike")),
            "expiry": expiry,
            "dte": dte,
            "option_type": opt_type,
            "bid": finite_number(row.get("bid")),
            "ask": finite_number(row.get("ask")),
            "last": finite_number(row.get("lastPrice")),
            "volume": int(finite_number(row.get("volume"))),
            "open_interest": int(finite_number(row.get("openInterest"))),
            "implied_volatility": finite_number(row.get("impliedVolatility")),
        }
