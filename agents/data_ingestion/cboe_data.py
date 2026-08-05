"""
CBOE Delayed Quotes Provider - free, no API key.

Uses the same free public CBOE endpoints that public options tables rely on.
CBOE publishes 15-minute delayed NBBO options quotes (with full Greeks and IV)
and VIX term-structure indices on its public CDN. There is no authentication
and no rate-limit agreement; the endpoints are the same ones the CBOE
website's own tables use.

Endpoints:
  - Options chain:      https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
  - Underlying quote:   https://cdn.cboe.com/api/global/delayed_quotes/quotes/{SYMBOL}.json
  - VIX indices hist:   https://cdn.cboe.com/api/global/us_indices/daily_history/{INDEX}.json

This module is intentionally dependency-free apart from httpx and the standard
library, so it can also be copied into other projects verbatim.
"""
import asyncio
import logging
import math
from datetime import datetime, date
from typing import Dict, Any, List, Optional

import httpx

logger = logging.getLogger(__name__)

CDN_BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
INDEX_BASE = "https://cdn.cboe.com/api/global/us_indices/daily_history"

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
    """Free 15-minute-delayed CBOE quotes (options + indices)."""

    def __init__(self, timeout: float = 10.0, min_request_interval: float = 0.25):
        self.timeout = timeout
        # Be polite: the CDN is a free shared resource. ~4 req/s is far below
        # the threshold that triggers blocks and still scans a 300-symbol
        # universe in reasonable time.
        self.min_interval = min_request_interval
        self._last_request: float = 0.0

    async def _get_json(self, url: str) -> Optional[Dict[str, Any]]:
        """GET a JSON document with throttling, returning None on any failure."""
        now = asyncio.get_event_loop().time()
        wait = self.min_interval - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = now
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=BROWSER_HEADERS) as client:
                response = await client.get(url)
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

    def _parse_option(self, option: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(option, dict):
            return None
        expiry = str(option.get("expiry") or "")
        strike = _finite_number(option.get("strike"))
        opt_type = str(option.get("type") or "").upper()
        if not expiry or strike <= 0:
            return None
        # CBOE encodes the type in the option symbol too, e.g. "AAPL 250 C".
        if opt_type not in ("CALL", "PUT"):
            option_symbol = str(option.get("option") or "").upper()
            opt_type = "CALL" if " C " in f" {option_symbol} " else "PUT"
        try:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 0)
        except (TypeError, ValueError):
            dte = 0
        base_symbol = str(option.get("option") or "").split()[0]
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
