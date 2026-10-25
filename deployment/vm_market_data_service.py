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

Also serves CME micro futures options (/futures-option-chain/MES|MNQ) in the
same schema plus sec_type/trading_class/multiplier/underlying_price -- still
read-only quotes, nothing that can place an order or see the account.
"""
import math
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from ib_insync import IB, Future, FuturesOption, Option, Stock
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
# Expiry selection: one expiry nearest the 45-DTE sweet spot inside the
# 21-60 DTE standard playbook, plus the nearest short-dated expiry (earnings
# IV-crush condors, implied earnings move, front straddle). Taking the two
# NEAREST expiries -- the old rule -- gave every weekly/daily-listed name only
# 1-7 DTE contracts, which the 14-60 DTE recommender can never use.
STANDARD_DTE_MIN, STANDARD_DTE_TARGET, STANDARD_DTE_MAX = 21, 45, 60
SHORT_DTE_MAX = 14
# Strike ladder: distances from spot (fractions) on each side; every OTM rung
# also takes its next-outer strike as the defined-risk wing. The old "12
# strikes nearest spot" covered +/-$6 on SPY's $1 grid -- nowhere near a
# 45-DTE 1-SD short (~6% away) -- so no spread, condor or CSP could form.
STRIKE_LADDER = (0.03, 0.06, 0.09, 0.13, 0.18)
# Each rung's wing sits at least this far further out (or one strike, if the
# grid is coarser). On MES's 5-point grid a one-strike wing loses ~10% of its
# width to a single tick of slippage per leg; ~25 points keeps the
# credit/width honest while a 25-point spread still risks under $100.
MIN_WING_PCT = 0.003
QUOTE_WAIT_SECONDS = 4.5  # streaming subscriptions: once populated they stay populated
REQUEST_TIMEOUT_SECONDS = 20.0
# The chain fetch does its own qualify + definition + quote cycle and has
# to share the single IB connection with concurrent background scans, so
# it needs a larger budget than a quick stock snapshot pull. Bumped after
# live observation (2026-10-20): every chain request hit the 20s cap
# (504) while contract qualification for near-dated dailies churned
# through pacing limits on the same gateway session.
CHAIN_REQUEST_TIMEOUT_SECONDS = float(os.getenv("MARKET_DATA_CHAIN_TIMEOUT", "75.0"))
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
# Per-resource locks, not one global lock: the advisor's background scans
# continuously pull /stock and /stock-history for many symbols, and a single
# shared lock turned every /option-chain request into a minutes-long convoy
# behind them (that was a live 504 generator on 2026-10-20 -- chain calls
# timed out at their own budget while never even getting the lock). IB
# handles concurrent request types on one connection fine; the locks exist
# to serialize same-type bursts, not to cross-serialize different feeds.
_chain_lock = asyncio.Lock()
_stock_lock = asyncio.Lock()
_history_lock = asyncio.Lock()


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


def _finite_price(value: Any) -> float:
    """Same as _finite, but also clamps negative prices to 0. IBKR returns
    -1 as its sentinel for "no two-sided quote available" on bid/ask/last --
    a real finite float, so _finite() alone doesn't catch it, and a raw -1
    passed downstream could get misread as an actual negative price/credit
    by a caller that only checks for None/NaN."""
    parsed = _finite(value)
    return parsed if parsed > 0 else 0.0


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
        return await asyncio.wait_for(_locked_fetch_option_chain(symbol), timeout=CHAIN_REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timed out fetching chain for {symbol.upper()}")


@app.get("/stock/{symbol}")
async def stock_quote(symbol: str, _: None = Depends(require_token)):
    """Live (or IBKR's free delayed) snapshot quote for one stock/ETF."""
    try:
        return await asyncio.wait_for(_locked_stock_quote(symbol.upper()), timeout=REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timed out quoting {symbol.upper()}")


# "1 Y", "6 M", "3 M", "1 M" are the IBKR historical-duration strings for the
# free-data periods free_data.py requests. Anything else maps to 1 year so the
# caller never gets a parse error back from IBKR.
_HISTORY_DURATIONS = {"1y": "1 Y", "6mo": "6 M", "3mo": "3 M", "1mo": "1 M"}


@app.get("/stock-history/{symbol}")
async def stock_history(symbol: str, period: str = "1y", _: None = Depends(require_token)):
    """Daily OHLCV bars for one stock/ETF from the account's own IBKR feed."""
    try:
        duration = _HISTORY_DURATIONS.get(period.lower(), "1 Y")
        return await asyncio.wait_for(
            _locked_stock_history(symbol.upper(), duration),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timed out fetching history for {symbol.upper()}")


async def _locked_stock_quote(symbol: str) -> dict[str, Any]:
    async with _stock_lock:
        return await _fetch_stock_quote(symbol)


async def _snapshot_quote(contract, wait_seconds: float = 2.0) -> tuple[float, float, float, str]:
    """Live-then-delayed snapshot for one contract: try live type 1, then
    IBKR's free delayed feed (type 3) if nothing came back. A paper account
    usually only has delayed, and delayed-from-our-own-feed still beats
    CBOE's public endpoint. Returns (bid, ask, last, data_quality)."""
    assert ib is not None
    ib.reqMarketDataType(1)
    ticker = ib.reqMktData(contract, "", True, False)
    await asyncio.sleep(wait_seconds)
    bid, ask, last = _finite_price(ticker.bid), _finite_price(ticker.ask), _finite_price(ticker.last)
    if bid or ask or last:
        return bid, ask, last, "live"

    ib.reqMarketDataType(3)
    ticker = ib.reqMktData(contract, "", True, False)
    await asyncio.sleep(wait_seconds)
    bid, ask, last = _finite_price(ticker.bid), _finite_price(ticker.ask), _finite_price(ticker.last)
    return bid, ask, last, "delayed"


async def _fetch_stock_quote(symbol: str) -> dict[str, Any]:
    await ensure_connected()
    contract = await _qualify_stock(symbol)

    bid, ask, last, data_quality = await _snapshot_quote(contract)
    if not last and not bid and not ask:
        raise HTTPException(status_code=422, detail=f"No live or delayed price available for {symbol}")
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "last": last,
        "market_data_type": data_quality,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


async def _qualify_stock(symbol: str) -> Stock:
    assert ib is not None
    contract = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        raise HTTPException(status_code=422, detail=f"Could not qualify {symbol}")
    return qualified[0]


async def _locked_stock_history(symbol: str, duration: str) -> list[dict[str, Any]]:
    async with _history_lock:
        return await _fetch_stock_history(symbol, duration)


async def _fetch_stock_history(symbol: str, duration: str) -> list[dict[str, Any]]:
    await ensure_connected()
    assert ib is not None
    contract = await _qualify_stock(symbol)
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        raise HTTPException(status_code=422, detail=f"No historical bars available for {symbol}")
    return [
        {
            "date": str(bar.date),
            "open": _finite(bar.open),
            "high": _finite(bar.high),
            "low": _finite(bar.low),
            "close": _finite(bar.close),
            "volume": float(_finite(bar.volume)),
        }
        for bar in bars
    ]


async def _locked_fetch_option_chain(symbol: str) -> list[dict[str, Any]]:
    async with _chain_lock:
        return await _fetch_option_chain(symbol)


def _dte(expiry: str, today: date) -> int | None:
    try:
        return (datetime.strptime(expiry, "%Y%m%d").date() - today).days
    except (TypeError, ValueError):
        return None


def _is_monthly(expiry: str) -> bool:
    """Third-Friday expiry (the standard monthly / quarterly cycle)."""
    try:
        day = datetime.strptime(expiry, "%Y%m%d").date()
    except (TypeError, ValueError):
        return False
    return day.weekday() == 4 and 15 <= day.day <= 21


def _select_expiries(expirations, today: date, latest: str | None = None) -> list[str]:
    """[expiry nearest 45 DTE within 21-60, nearest expiry within 1-14 DTE]."""
    dated = sorted(
        (dte, expiry) for expiry in expirations
        if (dte := _dte(expiry, today)) is not None and dte >= 1
        and (latest is None or expiry <= latest)
    )
    in_window = [(dte, e) for dte, e in dated if STANDARD_DTE_MIN <= dte <= STANDARD_DTE_MAX]
    # Prefer the standard monthly (third Friday): weeklies near 45 DTE carry
    # a fraction of the open interest (live, Oct 2026: F's Dec-4 weekly strikes
    # showed 0-1 OI vs ~10k on the monthly).
    monthlies = [pair for pair in in_window if _is_monthly(pair[1])]
    standard = min(monthlies or in_window,
                   key=lambda pair: abs(pair[0] - STANDARD_DTE_TARGET), default=None)
    short = next(((dte, e) for dte, e in dated if dte <= SHORT_DTE_MAX), None)
    picks = [pair[1] for pair in (standard, short) if pair]
    if not picks:
        picks = [e for _, e in dated[:MAX_EXPIRIES]]
    return picks[:MAX_EXPIRIES]


def _ladder_strikes(strikes, spot: float) -> dict[str, list[float]]:
    """Puts below and calls above spot at each STRIKE_LADDER rung (plus a
    wing >= MIN_WING_PCT further out), and the ATM strike on both sides."""
    ordered = sorted(set(strikes))
    if not ordered:
        return {"P": [], "C": []}
    atm = min(ordered, key=lambda k: abs(k - spot))
    puts, calls = {atm}, {atm}
    below = [k for k in ordered if k < spot]
    above = [k for k in ordered if k > spot]
    wing = spot * MIN_WING_PCT
    for rung in STRIKE_LADDER:
        if below:
            target = min(below, key=lambda k: abs(k - spot * (1 - rung)))
            puts.add(target)
            further = [k for k in below if k <= target - wing] or [k for k in below if k < target]
            if further:
                puts.add(max(further))
        if above:
            target = min(above, key=lambda k: abs(k - spot * (1 + rung)))
            calls.add(target)
            further = [k for k in above if k >= target + wing] or [k for k in above if k > target]
            if further:
                calls.add(min(further))
    return {"P": sorted(puts), "C": sorted(calls)}


async def _quote_option_rows(
    label: str, spot: float, contracts: list, data_quality: str, rate: float,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Stream-quote qualified option contracts and build chain rows.

    Snapshot mode never fills for options on the delayed feed (observed live
    2026-10-20: 48 snapshot tickers all empty while the same contracts
    streamed full bid/ask within seconds), and the live market-data type is
    the session default, so force delayed up front. Generic tick 101 adds
    open interest (callOpenInterest/putOpenInterest on the option's ticker):
    snapshots carry none, which reported OI=0 on every IBKR contract and
    failed every OI-gated CSP and spread. Unreported OI is flagged
    open_interest_known=False. Every streaming ticker is cancelled before
    returning.
    """
    assert ib is not None
    ib.reqMarketDataType(3)
    tickers = [ib.reqMktData(c, "101", False, False) for c in contracts]
    await asyncio.sleep(QUOTE_WAIT_SECONDS)
    for ticker_ in tickers:
        ib.cancelMktData(ticker_.contract)

    today = date.today()
    rows: list[dict[str, Any]] = []
    for contract, mkt_ticker in zip(contracts, tickers):
        expiry_str = contract.lastTradeDateOrContractMonth
        dte = max(_dte(expiry_str, today) or 0, 0)
        bid = _finite_price(mkt_ticker.bid)
        ask = _finite_price(mkt_ticker.ask)
        last = _finite_price(mkt_ticker.last)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last

        is_call = contract.right == "C"
        raw_oi = mkt_ticker.callOpenInterest if is_call else mkt_ticker.putOpenInterest
        oi_known = isinstance(raw_oi, (int, float)) and math.isfinite(raw_oi) and raw_oi >= 0

        t_years = max(dte, 1) / 365.0
        iv = _implied_vol(is_call, spot, contract.strike, t_years, rate, mid) if mid > 0 else None
        greeks = _bs_greeks(is_call, spot, contract.strike, t_years, rate, iv) if iv else None
        row = {
            "symbol": label,
            "strike": _finite(contract.strike),
            "expiry": f"{expiry_str[:4]}-{expiry_str[4:6]}-{expiry_str[6:8]}" if expiry_str else "",
            "dte": dte,
            "option_type": "CALL" if is_call else "PUT",
            "bid": bid,
            "ask": ask,
            "last": last,
            "volume": int(_finite(mkt_ticker.volume)),
            # Numeric for every analytics consumer (max pain, GEX, flow sum
            # it); open_interest_known=False tells the liquidity gate to judge
            # the quote instead of reading the 0 as "no open interest".
            "open_interest": int(raw_oi) if oi_known else 0,
            "open_interest_known": oi_known,
            # Beyond CBOE's schema (consumers read rows by key): lets callers
            # tell live from IBKR's free delayed feed without guessing.
            "data_quality": data_quality,
            "implied_volatility": _finite(iv),
            "delta": _finite(greeks["delta"] if greeks else None),
            "gamma": _finite(greeks["gamma"] if greeks else None),
            "theta": _finite(greeks["theta"] if greeks else None),
            "vega": _finite(greeks["vega"] if greeks else None),
            "rho": 0.0,
        }
        if extra:
            row.update(extra)
            row["trading_class"] = getattr(contract, "tradingClass", None) or extra.get("trading_class")
        rows.append(row)
    return rows


async def _fetch_option_chain(symbol: str) -> list[dict[str, Any]]:
    await ensure_connected()
    assert ib is not None
    symbol = symbol.upper()

    stock = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified:
        raise HTTPException(status_code=422, detail=f"Could not qualify {symbol}")
    stock = qualified[0]

    bid, ask, last, data_quality = await _snapshot_quote(stock)
    spot = last or ((bid + ask) / 2 if bid and ask else 0) or ask or bid
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

    expiries = _select_expiries(chain.expirations, date.today())
    ladder = _ladder_strikes(chain.strikes, spot)
    contracts = [
        Option(symbol, expiry, strike, right, "SMART")
        for expiry in expiries
        for right in ("P", "C")
        for strike in ladder[right]
    ]
    qualified_contracts = [c for c in await ib.qualifyContractsAsync(*contracts) if c.conId]
    if not qualified_contracts:
        raise HTTPException(status_code=422, detail=f"No option contracts qualified for {symbol}")
    return await _quote_option_rows(symbol, spot, qualified_contracts, data_quality, RISK_FREE_RATE)


# CME micro equity-index futures whose options this proxy quotes. Read-only;
# the Bridge independently allow-lists the same roots for (defined-risk only)
# orders and takes the multiplier from the qualified contract, never from here.
FUTURES_OPTION_ROOTS = {"MES": "CME", "MNQ": "CME"}
# Roll to the next quarterly before the front contract's last weeks so option
# expiries in the standard window still reference a live underlying.
FUTURES_ROLL_DAYS = 21


@app.get("/futures-option-chain/{root}")
async def futures_option_chain(root: str, _: None = Depends(require_token)):
    root = root.upper()
    if root not in FUTURES_OPTION_ROOTS:
        raise HTTPException(status_code=404, detail=f"Unsupported futures root {root}")
    try:
        return await asyncio.wait_for(_locked_fetch_futures_option_chain(root), timeout=CHAIN_REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timed out fetching futures options for {root}")


async def _locked_fetch_futures_option_chain(root: str) -> list[dict[str, Any]]:
    async with _chain_lock:
        return await _fetch_futures_option_chain(root)


async def _fetch_futures_option_chain(root: str) -> list[dict[str, Any]]:
    """Options on the active quarterly micro future (Black-76: the futures
    price is the forward, so the shared BS helpers run with r = 0)."""
    await ensure_connected()
    assert ib is not None
    exchange = FUTURES_OPTION_ROOTS[root]
    today = date.today()
    details = await ib.reqContractDetailsAsync(Future(root, exchange=exchange))
    futures = sorted(
        (d.contract for d in details
         if (dte := _dte(d.contract.lastTradeDateOrContractMonth, today)) is not None and dte > FUTURES_ROLL_DAYS),
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    if not futures:
        raise HTTPException(status_code=422, detail=f"No active {root} future")
    future = futures[0]

    bid, ask, last, data_quality = await _snapshot_quote(future)
    spot = last or ((bid + ask) / 2 if bid and ask else 0) or ask or bid
    if not spot or not math.isfinite(spot) or spot <= 0:
        raise HTTPException(status_code=422, detail=f"No live or delayed price for {future.localSymbol}")

    definitions = await ib.reqSecDefOptParamsAsync(future.symbol, exchange, "FUT", future.conId)
    if not definitions:
        raise HTTPException(status_code=422, detail=f"No option definitions for {future.localSymbol}")
    # CME lists each weekly/monthly series as its own trading class with one
    # or two expirations; pick expiries across all of them, then quote each
    # under the trading class that lists it.
    by_expiry: dict[str, Any] = {}
    for definition in definitions:
        for expiry in definition.expirations:
            current = by_expiry.get(expiry)
            if current is None or len(definition.strikes) > len(current.strikes):
                by_expiry[expiry] = definition
    expiries = _select_expiries(by_expiry, today, latest=future.lastTradeDateOrContractMonth)

    contracts = []
    for expiry in expiries:
        definition = by_expiry[expiry]
        ladder = _ladder_strikes(definition.strikes, spot)
        contracts.extend(
            FuturesOption(root, expiry, strike, right, exchange, tradingClass=definition.tradingClass)
            for right in ("P", "C") for strike in ladder[right]
        )
    qualified = [c for c in await ib.qualifyContractsAsync(*contracts) if c.conId]
    if not qualified:
        raise HTTPException(status_code=422, detail=f"No {root} futures options qualified")
    return await _quote_option_rows(
        root, spot, qualified, data_quality, 0.0,
        extra={
            "sec_type": "FOP",
            "multiplier": int(float(qualified[0].multiplier or 0)) or None,
            "underlying_price": round(spot, 2),
            "underlying_symbol": future.localSymbol,
            "underlying_expiry": future.lastTradeDateOrContractMonth,
        },
    )
