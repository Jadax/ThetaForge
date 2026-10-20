"""
CBOE Delayed Quotes Provider - free, no API key.

Uses the same free public CBOE endpoints that public options tables rely on.
CBOE publishes 15-minute delayed NBBO options quotes (with full Greeks and IV)
and VIX term-structure indices on its public CDN. There is no authentication
and no rate-limit agreement; the endpoints are the same ones the CBOE
website's own tables use.

Endpoints:
  - Options chain:      https://cdn-api.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
  - Underlying quote:   https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/{SYMBOL}.json
  - VIX indices hist:   https://cdn-api.cboe.com/api/global/us_indices/daily_history/{INDEX}.json

Note: CBOE moved the CDN host from cdn.cboe.com (307-redirects now) to
cdn-api.cboe.com in Oct 2026 — the option-chain and quote endpoints survive
there, while the us_indices/daily_history and data/us/equities JSON feeds
returned 403 AccessDenied on the new host, so the VIX term-structure and PCR
files are effectively retired at the source (free_data's yfinance primary
for the term structure covers the former; the PCR history simply fails open).

Refresh behaviour observed 2026-10-20 (pre-market UTC, AAPL): the options
chain payload is a coarse periodic snapshot (~90 min stale in that
observation, rows still listed a contract that expired the prior session)
and `bid`/`ask` were populated on only 52 of 3422 rows. Treat this feed as
a chain-STRUCTURE + open-interest source for quote fields, not live NBBO —
prefer the IBKR market-data proxy (`free_data.IBKR_MARKET_DATA_URL`) for
executable bid/ask whenever the Gateway is up, with yfinance as the last
resort. The underlying-quote endpoint and scheduled VIX indices are
separate datasets and stay independently fresh.

This module is intentionally dependency-free apart from httpx and the standard
library, so it can also be copied into other projects verbatim.
"""
import asyncio
import logging
import math
import re
from datetime import datetime, date
from typing import Dict, Any, List, Optional

import httpx

logger = logging.getLogger(__name__)

CDN_BASE = "https://cdn-api.cboe.com/api/global/delayed_quotes"
INDEX_BASE = "https://cdn-api.cboe.com/api/global/us_indices/daily_history"

# The CDN returns a 403 to plain library user agents. A normal browser
# User-Agent plus the CBOE referer is all that is required to read the same
# public data the cboe.com option tables render.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.cboe.com/",
    "Accept": "application/json, text/plain, */*",
}

VIX_TERM_INDICES = ["VIX9D", "VIX3M", "VIX6M", "VIX1Y"]


def _finite_number(value: Any, default: float = 0.0) -> float:
    """Defensive parse mirroring yfinance handling: NaN/None -> default."""
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


class CBOEDataProvider:
    """Free 15-minute-delayed CBOE quotes (options + indices).

    A single ``httpx.AsyncClient`` is reused for the provider's lifetime,
    avoiding the ~520 client instantiations per 130-symbol scan that the
    previous per-request pattern created.  Each client allocated its own
    connection pool; on Render free tier (512 MB) those transient pools
    were a significant contributor to OOM events.
    """

    def __init__(self, timeout: float = 10.0, min_request_interval: float = 0.25):
        self.timeout = timeout
        # Be polite: the CDN is a free shared resource. ~4 req/s is far below
        # the threshold that triggers blocks and still scans a 300-symbol
        # universe in reasonable time.
        self.min_interval = min_request_interval
        self._last_request: float = 0.0
        self._client: Optional[httpx.AsyncClient] = httpx.AsyncClient(
            timeout=timeout, headers=BROWSER_HEADERS,
        )

    async def close(self):
        """Shut down the persistent client (call on process exit if desired)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_json(self, url: str) -> Optional[Dict[str, Any]]:
        """GET a JSON document with throttling, returning None on any failure."""
        now = asyncio.get_event_loop().time()
        wait = self.min_interval - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = now
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, headers=BROWSER_HEADERS,
            )
        try:
            response = await self._client.get(url)
            if response.status_code == 200:
                return response.json()
            logger.debug("CBOE %s returned HTTP %s", url, response.status_code)
        except Exception as error:
            logger.debug("CBOE fetch failed for %s: %s", url, error)
        return None

    async def get_option_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """Full CBOE option chain for a US-listed symbol, normalized to the
        same dict shape the engine expects (plus Greeks)."""
        payload = await self._get_json(f"{CDN_BASE}/options/{symbol.upper()}.json")
        if not payload or not isinstance(payload.get("data"), dict):
            return []
        raw_options = payload["data"].get("options") or []
        chain: List[Dict[str, Any]] = []
        for option in raw_options:
            parsed = self._parse_option(option)
            if parsed:
                chain.append(parsed)
        return chain

    _OCC_SYMBOL_RE = re.compile(
        r"^\s*([A-Z]+)\s*(\d{6})([CP])(\d{8})\s*$"
    )

    def _parse_option(self, option: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(option, dict):
            return None
        expiry = str(option.get("expiry") or "")
        strike = _finite_number(option.get("strike"))
        opt_type = str(option.get("type") or "").upper()
        raw_contract = str(option.get("option") or "")
        base_symbol = raw_contract.split()[0] if raw_contract else ""
        if not expiry or strike <= 0 or opt_type not in ("CALL", "PUT"):
            # Newer CBOE payloads omit the explicit expiry/strike/type fields
            # and carry only the OCC contract description, e.g.
            # "AAPL 261019C00245000" — decode it as the fallback.
            decoded = self._OCC_SYMBOL_RE.match(raw_contract)
            if decoded is None:
                return None
            base_symbol = decoded.group(1)
            yymmdd, flag, strike_digits = decoded.group(2), decoded.group(3), decoded.group(4)
            if not expiry:
                expiry = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
            try:
                strike = float(int(strike_digits)) / 1000.0
            except ValueError:
                strike = 0.0
            if strike <= 0:
                return None
            if opt_type not in ("CALL", "PUT"):
                opt_type = "CALL" if flag == "C" else "PUT"
        try:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 0)
        except (TypeError, ValueError):
            dte = 0
        return {
            "symbol": base_symbol,
            "strike": strike,
            "expiry": expiry,
            "dte": dte,
            "option_type": opt_type,
            "bid": _finite_number(option.get("bid")),
            "ask": _finite_number(option.get("ask")),
            "last": _finite_number(option.get("last_trade_price")),
            "volume": int(_finite_number(option.get("volume"))),
            "open_interest": int(_finite_number(option.get("open_interest"))),
            "implied_volatility": _finite_number(option.get("iv")),
            # Greeks come free with the CBOE feed and feed the portfolio
            # delta/vega gates, which previously had no per-leg source.
            "delta": _finite_number(option.get("delta")),
            "gamma": _finite_number(option.get("gamma")),
            "theta": _finite_number(option.get("theta")),
            "vega": _finite_number(option.get("vega")),
            "rho": _finite_number(option.get("rho")),
        }

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Underlying delayed quote (price, bid/ask, volume, 52-week range)."""
        payload = await self._get_json(f"{CDN_BASE}/quotes/{symbol.upper()}.json")
        data = (payload or {}).get("data")
        if not isinstance(data, dict):
            return None
        return {
            "symbol": str(data.get("symbol") or symbol.upper()),
            "price": _finite_number(data.get("current_price")),
            "bid": _finite_number(data.get("bid")),
            "ask": _finite_number(data.get("ask")),
            "volume": int(_finite_number(data.get("volume"))),
            "change_pct": _finite_number(data.get("percent_change")),
        }

    async def get_vix_term_structure(self) -> Dict[str, float]:
        """Latest closes of the CBOE volatility term structure indices.

        Contango (VIX9D < VIX3M < VIX6M) is the healthy regime for premium
        sellers; inverted structure warns of event/fear premium up front.
        """
        structure: Dict[str, float] = {}
        for index in VIX_TERM_INDICES:
            payload = await self._get_json(f"{INDEX_BASE}/{index}.json")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                continue
            # History rows are dictionaries with a 'date' and 'close'; pick the
            # most recent by date (order is not guaranteed by the endpoint).
            closes = []
            for row in data:
                if isinstance(row, dict) and row.get("close") is not None:
                    closes.append((str(row.get("date", "")), _finite_number(row.get("close"))))
            if closes:
                closes.sort(key=lambda pair: pair[0])
                structure[index] = closes[-1][1]
        return structure
