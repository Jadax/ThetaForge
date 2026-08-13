"""
Equity universe discovery for the general (stock/ETF) trader.

Candidate sources, in priority order:
  1. Liquid ETF / index-proxy core (static) — broad-market and sector ETFs are
     the rotation book and always tradeable at tight spreads.
  2. IBKR bridge — TWS scanner universe (hot by volume, gainers, losers) and
     current positions (so open equity positions keep being rescanned).
  3. Yahoo free screeners — most actives, day gainers, growth tech.

Deduplicated and capped. Discovery is not an assertion of quality; the Equity
Brain gates every symbol before anything is actionable.
"""
import logging
import os
from typing import List

import httpx

from agents.data_ingestion.free_data import FreeDataProvider

logger = logging.getLogger(__name__)

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8002")

# Static core: broad indices + sector ETFs. These are the rotation universe —
# liquid, cheap to trade, and their momentum is what dual-momentum rotation
# ranks. Individual mega-caps come from the free screeners / bridge scanner.
LIQUID_ETF_CORE = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLB", "XLC", "XLU", "XLRE",
]


async def _bridge_discoveries() -> List[str]:
    """Position + TWS-scanner symbols from the local paper Bridge (may be
    unreachable in hosted deployments — logged, never raised)."""
    token = os.getenv("BRIDGE_ACCESS_TOKEN", "")
    headers = {"X-ThetaForge-Bridge-Token": token} if token else {}
    symbols: List[str] = []
    try:
        async with httpx.AsyncClient(base_url=BRIDGE_URL, headers=headers, timeout=8) as client:
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


async def build_equity_universe(max_symbols: int = 150) -> List[str]:
    """Build a deduplicated, rank-ordered equity universe to scan."""
    seen: set = set()
    universe: List[str] = []

    def _add(sym: str) -> None:
        s = sym.upper().strip()
        if s and s not in seen:
            seen.add(s)
            universe.append(s)

    for sym in LIQUID_ETF_CORE:
        _add(sym)

    for sym in await _bridge_discoveries():
        _add(sym)

    try:
        active = await FreeDataProvider().get_active_stock_universe(limit=120)
        for sym in active or []:
            _add(sym)
    except Exception as error:
        logger.warning("Yahoo screener discovery failed: %s", error)

    return universe[:max_symbols]
