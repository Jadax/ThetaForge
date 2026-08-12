"""Read-only IBKR market-data proxy.

Runs as its own process on the VM, separate from bridge/main.py in every way
that matters for security: its own port (8003 vs 8002), its own IB API
client ID, its own access token, and it exposes nothing that can place an
order or see account positions -- only option-chain reads. Exposed directly
on the VM's public IP (port 8003, token-gated) rather than through a
Cloudflare Tunnel, since astraiva.app's DNS lives at Spaceship, not
Cloudflare, and moving the zone just for this wasn't worth touching the live
company domain for. The token is the only thing standing between this and
the public internet, so the worst case of it leaking is someone sees option
quotes, not that they touch the account.

Returns option chain entries in the exact same dict shape
agents/data_ingestion/cboe_data.py already produces (symbol, strike, expiry,
dte, option_type, bid, ask, last, volume, open_interest,
implied_volatility, delta, gamma, theta, vega, rho), so
agents/data_ingestion/free_data.py can call this as a new first-priority
source ahead of CBOE with no reshaping needed downstream.

Greeks are computed locally (Black-Scholes, IV backed out from the mid
price via Newton-Raphson/bisection) rather than pulled from IBKR's
genericTick 106: that tick requires a separate analytics entitlement this
account's OPRA subscription doesn't include, and requesting it anyway is
what caused every contract in a batch to silently stall (Error 10091 on
each one, no fast failure). Plain bid/ask/last has no such gap.
"""
import math
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from ib_insync import IB, Option, Stock
import ib_insync.connection as ib_connection
import asyncio

HOST = os.getenv("IBKR_HOST", "127.0.0.1")
PORT = int(os.getenv("IBKR_PAPER_PORT", "4002"))
CLIENT_ID = int(os.getenv("IBKR_MARKET_DATA_CLIENT_ID", "18"))
ACCESS_TOKEN = os.getenv("MARKET_DATA_ACCESS_TOKEN", "")

# How much of the chain to pull. Firing ~96 reqMktData calls in one burst
# was enough to trip IBKR's per-second message pacing limit and get the
# whole batch rejected with Error 10091 (not a real entitlement gap -- a
# smaller burst of the same contracts succeeded fine). This strategy only
# ever trades strikes near a target delta anyway, so 2 expiries x 12 strikes
# x 2 rights = 48 contracts covers the useful range without needing batching
# complexity to stay under the pacing limit.
MAX_EXPIRIES = 2
MAX_STRIKES_PER_SIDE = 6
STRIKE_WINDOW_PCT = 0.15  # +/- 15% of spot, tighter since fewer strikes are kept
QUOTE_WAIT_SECONDS = 2.5
REQUEST_TIMEOUT_SECONDS = 20.0
# Rough short-term risk-free rate used only for Black-Scholes greeks
# estimation, not pricing -- a few tenths of a point of error here barely
# moves delta/IV for the short-dated, near-the-money contracts this scanner
# actually trades.
RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_price(is_call: bool, spot: float, strike: float, t: float, r: float, iv: float) -> float:
    if t <= 0 or iv <= 0:
        intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
        return intrinsic
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _implied_vol(is_call: bool, spot: float, strike: float, t: float, r: float, price: float) -> float | None:
    """Back out IV from a mid price. Newton-Raphson with a vega guard, falling
    back to bisection since vega collapses to ~0 for deep ITM/OTM contracts
    and would otherwise blow up the Newton step."""
    if t <= 0 or price <= 0:
        return None
    intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    if price < intrinsic:
        return None

    iv = 0.3
    for _ in range(25):
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
        vega = spot * _norm_pdf(d1) * math.sqrt(t)
        diff = _bs_price(is_call, spot, strike, t, r, iv) - price
        if abs(diff) < 1e-4:
            return iv
        if vega < 1e-8:
            break
        iv -= diff / vega
        if iv <= 0 or iv > 5:
            break
    else:
        return iv if 0 < iv <= 5 else None

    low, high = 1e-4, 5.0
    for _ in range(60):
        mid = (low + high) / 2
        if _bs_price(is_call, spot, strike, t, r, mid) > price:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def _bs_greeks(is_call: bool, spot: float, strike: float, t: float, r: float, iv: float) -> dict[str, float]:
    if t <= 0 or iv <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    pdf_d1 = _norm_pdf(d1)
    gamma = pdf_d1 / (spot * iv * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t / 100  # per 1 vol point
    if is_call:
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt_t) - r * strike * math.exp(-r * t) * _norm_cdf(d2)) / 365
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt_t) + r * strike * math.exp(-r * t) * _norm_cdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}

ib: IB | None = None
# All requests share one IB connection, and IBKR's message-pacing limit is
# per-connection, not per-request. Without this, concurrent requests (the
# live scanner fans out several symbols at once) each fire their own burst
# of reqMktData calls on the same connection simultaneously, blowing well
# past the pacing limit and getting the whole burst rejected with Error
# 10091 across every in-flight request -- this was mistaken for account/
# entitlement flakiness before the concurrency was the actual cause.
_chain_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ib
    ib_connection.getLoop = asyncio.get_running_loop
    ib = IB()
    try:
        yield
    finally:
        if ib and ib.isConnected():
            ib.disconnect()
        ib = None


app = FastAPI(title="ThetaForge Market Data (read-only)", lifespan=lifespan)


async def require_token(x_market_data_token: str | None = Header(default=None)) -> None:
    if not ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="MARKET_DATA_ACCESS_TOKEN is not configured")
    import hmac
    if not hmac.compare_digest(x_market_data_token or "", ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Market-Data-Token")


async def ensure_connected() -> None:
    assert ib is not None
    if not ib.isConnected():
        try:
            await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=8)
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"IBKR Gateway unavailable: {error}") from error


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


@app.get("/health")
async def health():
    return {"connected": bool(ib and ib.isConnected())}


@app.get("/option-chain/{symbol}")
async def option_chain(symbol: str, _: None = Depends(require_token)):
    try:
        # The timeout has to cover time spent waiting for the lock too, not
        # just the fetch itself -- otherwise a request stuck behind several
        # others in the queue could wait far longer than REQUEST_TIMEOUT_SECONDS
        # before its own clock even starts.
        return await asyncio.wait_for(_locked_fetch_option_chain(symbol), timeout=REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timed out fetching chain for {symbol.upper()}")


async def _locked_fetch_option_chain(symbol: str) -> list[dict[str, Any]]:
    async with _chain_lock:
        return await _fetch_option_chain(symbol)


async def _fetch_option_chain(symbol: str) -> list[dict[str, Any]]:
    await ensure_connected()
    assert ib is not None
    symbol = symbol.upper()

    stock = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        raise HTTPException(status_code=422, detail=f"Could not qualify {symbol}")
    stock = qualified[0]

    # Snapshot requests (one update, auto-closes) instead of a streaming
    # subscription you have to babysit and cancel -- this is the pattern
    # IBKR itself recommends for "poll the current price and move on"
    # use cases, and streaming tickers here were intermittently never
    # populating even in isolated single-request tests, for reasons that
    # never showed up as a clean error. Try live data first (type 1); this
    # paper account has no live-data subscription for most symbols, so this
    # deliberately falls back to IBKR's free delayed feed (type 3) rather
    # than failing outright -- delayed-but-from-our-own-dedicated-connection
    # is still a real upgrade over CBOE's public endpoint, which Render's IP
    # gets rate-limited on under load (see SCAN_CONCURRENCY's comment in
    # background_scanner.py).
    data_quality = "live"
    ib.reqMarketDataType(1)
    ticker = ib.reqMktData(stock, "", True, False)
    await asyncio.sleep(2.0)
    spot = ticker.marketPrice()
    if not spot or not math.isfinite(spot) or spot <= 0:
        data_quality = "delayed"
        ib.reqMarketDataType(3)
        ticker = ib.reqMktData(stock, "", True, False)
        await asyncio.sleep(2.0)
        spot = ticker.marketPrice()
    if not spot or not math.isfinite(spot) or spot <= 0:
        raise HTTPException(status_code=422, detail=f"No live or delayed price available for {symbol}")

    chains = await ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        raise HTTPException(status_code=422, detail=f"No option chain definition for {symbol}")
    # reqSecDefOptParams returns one definition per exchange. The literal
    # "SMART" entry is NOT reliably the full chain -- for SPY it came back
    # with only 3 strikes/3 expirations, while the real chain (491 strikes,
    # 35 expirations) was under AMEX/NASDAQOM. Picking the definition with
    # the most strikes is what actually finds the real chain regardless of
    # which exchange happens to hold it for a given symbol.
    chain = max(chains, key=lambda c: len(c.strikes))

    today = date.today()
    expiries = sorted(
        e for e in chain.expirations
        if (datetime.strptime(e, "%Y%m%d").date() - today).days >= 0
    )[:MAX_EXPIRIES]

    low, high = spot * (1 - STRIKE_WINDOW_PCT), spot * (1 + STRIKE_WINDOW_PCT)
    all_strikes = sorted(s for s in chain.strikes if low <= s <= high)
    # Keep strikes closest to spot if the window still has too many.
    all_strikes.sort(key=lambda s: abs(s - spot))
    strikes = sorted(all_strikes[: MAX_STRIKES_PER_SIDE * 2])

    contracts = [
        Option(symbol, expiry, strike, right, "SMART")
        for expiry in expiries
        for strike in strikes
        for right in ("C", "P")
    ]
    qualified_contracts = await ib.qualifyContractsAsync(*contracts)
    qualified_contracts = [c for c in qualified_contracts if c.conId]
    if not qualified_contracts:
        raise HTTPException(status_code=422, detail=f"No option contracts qualified for {symbol}")

    tickers = [ib.reqMktData(c, "", True, False) for c in qualified_contracts]
    await asyncio.sleep(QUOTE_WAIT_SECONDS)

    result: list[dict[str, Any]] = []
    for contract, mkt_ticker in zip(qualified_contracts, tickers):
        expiry_str = contract.lastTradeDateOrContractMonth
        try:
            dte = max((datetime.strptime(expiry_str, "%Y%m%d").date() - today).days, 0)
        except (TypeError, ValueError):
            dte = 0

        bid = _finite(mkt_ticker.bid)
        ask = _finite(mkt_ticker.ask)
        last = _finite(mkt_ticker.last)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last

        is_call = contract.right == "C"
        t_years = max(dte, 1) / 365.0
        iv = _implied_vol(is_call, spot, contract.strike, t_years, RISK_FREE_RATE, mid) if mid > 0 else None
        greeks = _bs_greeks(is_call, spot, contract.strike, t_years, RISK_FREE_RATE, iv) if iv else None

        result.append({
            "symbol": symbol,
            "strike": _finite(contract.strike),
            "expiry": f"{expiry_str[:4]}-{expiry_str[4:6]}-{expiry_str[6:8]}" if expiry_str else "",
            "dte": dte,
            "option_type": "CALL" if is_call else "PUT",
            "bid": bid,
            "ask": ask,
            "last": last,
            "volume": int(_finite(mkt_ticker.volume)),
            "open_interest": int(_finite(getattr(mkt_ticker, "openInterest", None))),
            # Extra field beyond CBOE's schema -- harmless, since every
            # consumer reads chain rows by key, not a strict key set. Lets a
            # future caller distinguish live from IBKR's free delayed feed
            # without guessing.
            "data_quality": data_quality,
            "implied_volatility": _finite(iv),
            "delta": _finite(greeks["delta"] if greeks else None),
            "gamma": _finite(greeks["gamma"] if greeks else None),
            "theta": _finite(greeks["theta"] if greeks else None),
            "vega": _finite(greeks["vega"] if greeks else None),
            "rho": 0.0,
        })

    return result
