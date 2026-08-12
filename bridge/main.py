"""Personal, localhost-only bridge between ThetaForge and IBKR paper trading."""
import os
import asyncio
import hmac
import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ib_insync import IB, Bag, ComboLeg, LimitOrder, Option, ScannerSubscription
import ib_insync.connection as ib_connection


HOST = os.getenv("IBKR_HOST", "127.0.0.1")
PAPER_PORT = int(os.getenv("IBKR_PAPER_PORT", "4002"))
CLIENT_ID = int(os.getenv("IBKR_BRIDGE_CLIENT_ID", "17"))
ACCESS_TOKEN = os.getenv("BRIDGE_ACCESS_TOKEN", "")
PAPER_ONLY = os.getenv("PAPER_TRADING_ONLY", "true").lower() == "true"
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
PAPER_ORDER_LEDGER_FILE = os.path.join(DATA_DIR, "paper_order_ledger.json")
# Do not construct IB() at import time. On Windows uvicorn creates its running
# asyncio loop after importing this module; an IB instance created too early can
# retain a Future from that old loop ("attached to a different loop").
ib: IB | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create and dispose the IB client on uvicorn's active event loop."""
    global ib
    # ib_insync 0.9.x asks the event-loop policy for a loop when opening its
    # socket. With Python 3.12 on Windows that policy can return a different
    # Proactor loop from the one running FastAPI, producing an
    # "_OverlappedFuture ... attached to a different loop" error. Always use
    # the loop currently executing this request/application instead.
    ib_connection.getLoop = asyncio.get_running_loop
    ib = IB()
    _ensure_order_ledger()
    try:
        yield
    finally:
        if ib and ib.isConnected():
            ib.disconnect()
        ib = None


app = FastAPI(title="ThetaForge Local IBKR Bridge", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "https://jadax.github.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-ThetaForge-Bridge-Token"],
    allow_private_network=True,
)


@app.middleware("http")
async def allow_private_network_dashboard(request, call_next):
    """Permit the authenticated GitHub dashboard to call this localhost API.

    Chromium browsers send a Private Network Access preflight when an HTTPS page
    reaches a loopback HTTP service. This header is required for that local-only
    connection and does not expose the Bridge to the public internet.
    """
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


class OptionQuoteLeg(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    expiry: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    strike: float = Field(gt=0)
    right: Literal["C", "P"]


class OptionQuoteRequest(BaseModel):
    legs: list[OptionQuoteLeg] = Field(min_length=1, max_length=12)


class ComboOrderLeg(OptionQuoteLeg):
    action: Literal["BUY", "SELL"]


class PaperComboOrder(BaseModel):
    """An Advisor structure submitted to the paper-only IBKR Bridge."""
    legs: list[ComboOrderLeg] = Field(min_length=1, max_length=4)
    quantity: int = Field(default=1, gt=0, le=10)
    capital_limit: float = Field(gt=0)
    recommendation_id: str | None = Field(default=None, max_length=120)
    strategy: str | None = Field(default=None, max_length=80)


class CloseComboRequest(BaseModel):
    """Close an open paper position by referencing its entry ledger id.

    The Bridge mirrors the parent record's own legs (reversing each action), so
    a caller can never mismatch the structure. No new capital is reserved for a
    close; the parent's reservation is released once the close order is placed.
    """
    ledger_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="managed_exit", min_length=1, max_length=80)
    capital_limit: float = Field(gt=0)


RESERVING_ORDER_STATUSES = {
    "ApiPending", "PendingSubmit", "PreSubmitted", "Submitted",
    "PendingCancel", "Filled", "Unknown",
}
TERMINAL_ORDER_STATUSES = {"ApiCancelled", "Cancelled", "Inactive"}


def _ensure_order_ledger() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(PAPER_ORDER_LEDGER_FILE):
        _write_order_ledger([])


def _read_order_ledger() -> list[dict[str, Any]]:
    _ensure_order_ledger()
    try:
        with open(PAPER_ORDER_LEDGER_FILE, "r", encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_order_ledger(records: list[dict[str, Any]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    temporary = f"{PAPER_ORDER_LEDGER_FILE}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)
    os.replace(temporary, PAPER_ORDER_LEDGER_FILE)


def _current_week_key(moment: datetime | None = None) -> str:
    current = moment or datetime.now(timezone.utc)
    year, week, _ = current.isocalendar()
    return f"{year}-W{week:02d}"


def _reserved_capital(records: list[dict[str, Any]], week_key: str | None = None) -> float:
    selected_week = week_key or _current_week_key()
    return round(sum(
        float(record.get("max_loss_total", 0) or 0)
        for record in records
        if record.get("week_key") == selected_week
        and record.get("status", "Unknown") in RESERVING_ORDER_STATUSES
        and not record.get("closed_by")
        and not record.get("close_of")
    ), 2)


def _sync_order_ledger() -> list[dict[str, Any]]:
    records = _read_order_ledger()
    if ib is None or not ib.isConnected():
        return records
    trades_by_order_id = {
        int(trade.order.orderId): trade
        for trade in ib.trades()
        if getattr(trade.order, "orderId", None) is not None
    }
    changed = False
    for record in records:
        order_id = record.get("ibkr_order_id")
        trade = trades_by_order_id.get(int(order_id)) if order_id is not None else None
        if trade is None:
            continue
        status = str(trade.orderStatus.status or "Unknown")
        update = {
            "status": status,
            "filled": float(trade.orderStatus.filled or 0),
            "remaining": float(trade.orderStatus.remaining or 0),
            "average_fill_price": float(trade.orderStatus.avgFillPrice or 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if any(record.get(key) != value for key, value in update.items()):
            record.update(update)
            changed = True
    if changed:
        _write_order_ledger(records)
    return records


async def require_access_token(x_thetaforge_bridge_token: str | None = Header(default=None)) -> None:
    """Authenticate a Bridge request.

    This fails closed. An unset token previously disabled authentication
    silently, which left the order endpoints reachable by anything able to
    address loopback. The comparison is constant-time so that a token cannot be
    recovered by measuring response latency.
    """
    if not ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="BRIDGE_ACCESS_TOKEN is not configured. Set it in .env and restart the Bridge.",
        )
    if not hmac.compare_digest(x_thetaforge_bridge_token or "", ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing Bridge access token")


async def ensure_connected() -> None:
    if not PAPER_ONLY or PAPER_PORT not in {4002, 7497}:
        raise HTTPException(status_code=503, detail="Bridge is locked to IBKR paper-trading ports only")
    client = ib
    if client is None:
        raise HTTPException(status_code=503, detail="Bridge is still starting; retry in a moment")
    if not client.isConnected():
        try:
            await client.connectAsync(HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=8)
            accounts = client.managedAccounts()
            if not any(account.upper().startswith("DU") for account in accounts):
                client.disconnect()
                raise HTTPException(status_code=503, detail="Connected IBKR session is not a paper account")
        except Exception as error:
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(status_code=503, detail=f"Paper TWS/IB Gateway is unavailable: {error}") from error


@app.get("/health")
async def health():
    return {"mode": "paper_only", "connected": bool(ib and ib.isConnected()), "host": HOST, "port": PAPER_PORT}


@app.get("/")
async def root():
    return {
        "service": "ThetaForge Local IBKR Bridge",
        "mode": "paper_only",
        "status_url": "/health",
        "message": "API service only. Use the ThetaForge dashboard for the trading interface.",
    }


@app.post("/connect")
async def connect(_: None = Depends(require_access_token)):
    await ensure_connected()
    return {"mode": "paper_only", "connected": True}


@app.get("/positions")
async def positions(_: None = Depends(require_access_token)):
    await ensure_connected()
    assert ib is not None
    return [
        {
            "id": str(item.contract.conId or f"{item.contract.symbol}-{item.contract.localSymbol}"),
            "symbol": item.contract.symbol,
            "position": item.position,
            "average_cost": item.avgCost,
            "contract_type": item.contract.secType,
            "strike": item.contract.strike or None,
            "expiry": item.contract.lastTradeDateOrContractMonth or None,
            "right": item.contract.right or None,
        }
        for item in ib.positions()
    ]


@app.get("/orders")
async def paper_orders(
    capital_limit: float | None = Query(default=None, gt=0),
    _: None = Depends(require_access_token),
):
    """Return the Bridge's paper-order ledger reconciled with this TWS session."""
    await ensure_connected()
    records = _sync_order_ledger()
    reserved = _reserved_capital(records)
    return {
        "mode": "paper_only",
        "week_key": _current_week_key(),
        "capital_limit": capital_limit,
        "capital_reserved": reserved,
        "capital_remaining": max(float(capital_limit) - reserved, 0) if capital_limit else None,
        "orders": sorted(records, key=lambda item: item.get("submitted_at", ""), reverse=True)[:100],
    }


@app.post("/orders/{ledger_id}/cancel")
async def cancel_paper_order(ledger_id: str, _: None = Depends(require_access_token)):
    """Cancel an unfilled paper order and release its reservation once IBKR confirms."""
    await ensure_connected()
    assert ib is not None
    records = _sync_order_ledger()
    record = next((item for item in records if item.get("id") == ledger_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Paper order was not found")
    if record.get("status") in TERMINAL_ORDER_STATUSES:
        return {"mode": "paper_only", "status": record.get("status"), "id": ledger_id}
    if float(record.get("filled", 0) or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="This order has a fill. Close the paper position in TWS; cancellation cannot undo a fill.",
        )
    order_id = record.get("ibkr_order_id")
    trade = next(
        (item for item in ib.trades() if int(item.order.orderId) == int(order_id)),
        None,
    ) if order_id is not None else None
    if trade is None:
        raise HTTPException(status_code=409, detail="IBKR no longer has this order in the current session")
    ib.cancelOrder(trade.order)
    record["status"] = "PendingCancel"
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_order_ledger(records)
    return {"mode": "paper_only", "status": "PendingCancel", "id": ledger_id}


@app.get("/scanner/universe")
async def scanner_universe(_: None = Depends(require_access_token)):
    """Return a broad, current US stock discovery set from TWS scanners."""
    await ensure_connected()
    assert ib is not None
    scans = ("HOT_BY_VOLUME", "TOP_PERC_GAIN", "TOP_PERC_LOSE")
    symbols: list[str] = []
    scan_results: dict[str, int] = {}
    for scan_code in scans:
        subscription = ScannerSubscription(
            numberOfRows=50,
            instrument="STK",
            locationCode="STK.US.MAJOR",
            scanCode=scan_code,
        )
        try:
            rows = await asyncio.to_thread(ib.reqScannerData, subscription)
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"IBKR scanner {scan_code} is unavailable: {error}") from error
        scan_results[scan_code] = len(rows)
        for row in rows:
            symbol = str(row.contractDetails.contract.symbol or "").upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return {"source": "IBKR TWS scanner", "symbols": symbols[:150], "scan_results": scan_results}


def _quote_number(value) -> float | None:
    """JSON-safe quote value; IBKR uses NaN for unavailable fields."""
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


async def _live_option_tickers(legs: list[OptionQuoteLeg]):
    """Qualify legs and request strict live snapshots from TWS."""
    await ensure_connected()
    assert ib is not None
    contracts = [Option(leg.symbol.upper(), leg.expiry.replace("-", ""), leg.strike, leg.right, "SMART") for leg in legs]
    qualified = await ib.qualifyContractsAsync(*contracts)
    if len(qualified) != len(contracts):
        raise HTTPException(status_code=422, detail="IBKR could not qualify one or more option contracts")
    ib.reqMarketDataType(1)
    tickers = await ib.reqTickersAsync(*qualified)
    return qualified, tickers


def _defined_risk_per_combo(legs: list[ComboOrderLeg], net_credit: float) -> float | None:
    """Calculate maximum loss only for structures this Bridge can prove defined-risk."""
    if len(legs) == 1:
        leg = legs[0]
        if leg.action == "SELL" and leg.right == "P" and net_credit > 0:
            return (leg.strike - net_credit) * 100
        # A covered call's stock downside belongs to the already-held shares;
        # ownership is checked separately before the call order is submitted.
        if leg.action == "SELL" and leg.right == "C" and net_credit > 0:
            return 0.0
        return None
    if len(legs) == 2 and {leg.action for leg in legs} == {"BUY", "SELL"} and legs[0].right == legs[1].right:
        width = abs(legs[0].strike - legs[1].strike)
        return (width - net_credit) * 100 if net_credit >= 0 else abs(net_credit) * 100
    if len(legs) == 4 and {leg.right for leg in legs} == {"C", "P"}:
        widths = []
        for right in ("C", "P"):
            side = [leg for leg in legs if leg.right == right]
            if len(side) != 2 or {leg.action for leg in side} != {"BUY", "SELL"}:
                return None
            widths.append(abs(side[0].strike - side[1].strike))
        return (max(widths) - net_credit) * 100 if net_credit >= 0 else None
    return None


def _available_usd_funds() -> float | None:
    assert ib is not None
    values = [
        _quote_number(item.value)
        for item in ib.accountValues()
        if item.tag == "AvailableFunds" and item.currency == "USD"
    ]
    usable = [value for value in values if value is not None]
    return max(usable) if usable else None


def _owned_shares(symbol: str) -> float:
    assert ib is not None
    return sum(
        max(float(position.position), 0)
        for position in ib.positions()
        if position.contract.secType == "STK" and position.contract.symbol.upper() == symbol.upper()
    )


@app.post("/options/quotes")
async def option_quotes(request: OptionQuoteRequest, _: None = Depends(require_access_token)):
    """Fetch a bounded IBKR quote snapshot and disclose its data quality.

    The request explicitly asks TWS for live data. IBKR labels the returned
    snapshot as live, frozen, delayed, or delayed-frozen, allowing the
    dashboard to reject anything other than live for executable calculations.
    """
    _, tickers = await _live_option_tickers(request.legs)
    quality = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}
    quotes = []
    for leg, ticker in zip(request.legs, tickers):
        bid = _quote_number(ticker.bid)
        ask = _quote_number(ticker.ask)
        quotes.append({
            "symbol": leg.symbol.upper(),
            "expiry": leg.expiry,
            "strike": leg.strike,
            "right": leg.right,
            "bid": bid,
            "ask": ask,
            "last": _quote_number(ticker.last),
            "market_data_type": quality.get(ticker.marketDataType, "unavailable"),
            "executable": ticker.marketDataType == 1 and bid is not None and ask is not None,
        })
    return {"source": "IBKR TWS", "requested_at": datetime.now(timezone.utc).isoformat(), "quotes": quotes}


@app.post("/orders/submit-combo")
async def submit_paper_combo(order: PaperComboOrder, _: None = Depends(require_access_token)):
    """Submit one live-quote-verified, defined-risk combo to IBKR paper TWS."""
    legs = [OptionQuoteLeg(**leg.model_dump(exclude={"action"})) for leg in order.legs]
    qualified, tickers = await _live_option_tickers(legs)
    live_prices = []
    for leg, ticker in zip(order.legs, tickers):
        bid, ask = _quote_number(ticker.bid), _quote_number(ticker.ask)
        if ticker.marketDataType != 1 or bid is None or ask is None:
            raise HTTPException(status_code=422, detail="Live executable IBKR bid/ask data is required before a paper order can be submitted")
        live_prices.append(bid if leg.action == "SELL" else -ask)

    net_credit = sum(live_prices)
    max_loss = _defined_risk_per_combo(order.legs, net_credit)
    if max_loss is None or max_loss < 0:
        raise HTTPException(status_code=422, detail="This structure cannot be proven defined-risk by the paper Bridge")
    total_risk = max_loss * order.quantity
    records = _sync_order_ledger()
    capital_reserved = _reserved_capital(records)
    if capital_reserved + total_risk > order.capital_limit:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Weekly reserved capital ${capital_reserved:.2f} plus this order's "
                f"${total_risk:.2f} maximum loss exceeds the ${order.capital_limit:.2f} limit"
            ),
        )

    if len(order.legs) == 1:
        leg = order.legs[0]
        if leg.action != "SELL" or leg.right not in {"C", "P"}:
            raise HTTPException(status_code=422, detail="Only Advisor-recommended cash-secured puts and covered calls support single-leg automation")
        if leg.right == "P":
            available_funds = _available_usd_funds()
            if available_funds is None or total_risk > available_funds:
                raise HTTPException(status_code=422, detail="Insufficient verified IBKR available funds for this cash-secured put")
        elif _owned_shares(leg.symbol) < 100 * order.quantity:
            raise HTTPException(status_code=422, detail="A covered call requires at least 100 owned shares per contract in IBKR")

    if len(order.legs) == 1:
        contract = qualified[0]
        limit_price = round(abs(net_credit), 2)
        ib_order = LimitOrder(order.legs[0].action, order.quantity, limit_price, tif="DAY")
    else:
        bag = Bag(
            symbol=order.legs[0].symbol.upper(),
            exchange="SMART",
            currency="USD",
            comboLegs=[ComboLeg(conId=contract.conId, ratio=1, action=leg.action, exchange="SMART") for contract, leg in zip(qualified, order.legs)],
        )
        # A combo is bought as one structure. Credit combinations therefore have a
        # negative limit price; IBKR interprets that as receiving cash for a buy
        # spread. This prevents execution at a worse net price than live quotes.
        limit_price = round(-net_credit, 2)
        contract = bag
        ib_order = LimitOrder("BUY", order.quantity, limit_price, tif="DAY")
    assert ib is not None
    trade = ib.placeOrder(contract, ib_order)
    now = datetime.now(timezone.utc).isoformat()
    ledger_id = str(uuid4())
    status = str(trade.orderStatus.status or "PendingSubmit")
    records.append({
        "id": ledger_id,
        "recommendation_id": order.recommendation_id,
        "strategy": order.strategy,
        "symbol": order.legs[0].symbol.upper(),
        "legs": [leg.model_dump() for leg in order.legs],
        "quantity": order.quantity,
        "ibkr_order_id": int(trade.order.orderId),
        "status": status,
        "filled": float(trade.orderStatus.filled or 0),
        "remaining": float(trade.orderStatus.remaining or order.quantity),
        "average_fill_price": float(trade.orderStatus.avgFillPrice or 0),
        "limit_price": limit_price,
        "net_credit": round(net_credit, 2),
        "max_loss_per_combo": round(max_loss, 2),
        "max_loss_total": round(total_risk, 2),
        "week_key": _current_week_key(),
        "submitted_at": now,
        "updated_at": now,
    })
    _write_order_ledger(records)
    return {
        "mode": "paper_only",
        "status": status,
        "ledger_id": ledger_id,
        "ibkr_order_id": int(trade.order.orderId),
        "limit_price": limit_price,
        "net_credit": round(net_credit, 2),
        "max_loss": round(max_loss, 2),
        "max_loss_total": round(total_risk, 2),
        "capital_reserved": round(capital_reserved + total_risk, 2),
        "capital_remaining": round(order.capital_limit - capital_reserved - total_risk, 2),
        "quantity": order.quantity,
        "message": "Paper combo submitted to IBKR Gateway",
    }


# The former /orders/stage and /orders/{id}/submit pair was removed. It placed
# orders without live-quote verification, defined-risk proof, capital-limit
# enforcement, or covered/cash-secured checks, and it never reached the ledger,
# so its risk was invisible to weekly capital reservation. Every paper order now
# goes through /orders/submit-combo, which applies all of those controls.


def _mirror_close_legs(parent_legs: list[dict[str, Any]]) -> list[ComboOrderLeg]:
    """Reverse every action on a parent entry's legs to build its close."""
    close_legs = []
    for leg in parent_legs:
        action = "BUY" if leg.get("action") == "SELL" else "SELL"
        close_legs.append(
            ComboOrderLeg(
                symbol=str(leg.get("symbol", "")).upper(),
                expiry=str(leg.get("expiry", "")),
                strike=float(leg.get("strike", 0)),
                right=leg.get("right") if leg.get("right") in {"C", "P"} else "C",
                action=action,
            )
        )
    return close_legs


def _same_leg_set(left: list, right: list) -> bool:
    """Structural equality (symbol/expiry/strike/right, ignoring action)."""
    return sorted(
        (str(leg.symbol if hasattr(leg, "symbol") else leg.get("symbol", "")).upper(),
         str(leg.expiry if hasattr(leg, "expiry") else leg.get("expiry", "")),
         round(float(leg.strike if hasattr(leg, "strike") else leg.get("strike", 0)), 4),
         leg.right if hasattr(leg, "right") else leg.get("right"))
        for leg in left
    ) == sorted(
        (str(leg.symbol if hasattr(leg, "symbol") else leg.get("symbol", "")).upper(),
         str(leg.expiry if hasattr(leg, "expiry") else leg.get("expiry", "")),
         round(float(leg.strike if hasattr(leg, "strike") else leg.get("strike", 0)), 4),
         leg.right if hasattr(leg, "right") else leg.get("right"))
        for leg in right
    )


@app.post("/orders/close-combo")
async def close_paper_combo(request: CloseComboRequest, _: None = Depends(require_access_token)):
    """Close an open paper position by mirroring its entry ledger record.

    The closing order reverses every action on the parent's own legs, so the
    caller only names *which* position to close and *why*. All the same safety
    rails as submit-combo apply to the close: live IBKR bid/ask verification,
    defined-risk structure continuity, and paper-only mode. A close never
    reserves new capital; instead the parent's weekly reservation is released.
    """
    await ensure_connected()
    assert ib is not None
    records = _sync_order_ledger()
    parent = next((item for item in records if item.get("id") == request.ledger_id), None)
    if parent is None:
        raise HTTPException(status_code=404, detail="Paper order was not found in the ledger")
    if parent.get("close_of"):
        raise HTTPException(status_code=409, detail="This order is itself a closing order; a position closes once")
    if parent.get("closed_by"):
        raise HTTPException(status_code=409, detail="This position is already closed")
    if float(parent.get("filled", 0) or 0) <= 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot close a paper position that has not filled",
        )
    if not parent.get("legs"):
        raise HTTPException(status_code=422, detail="Parent order has no legs; cannot build a closing order")
    if not (float(parent.get("max_loss_total", 0) or 0) > 0 or float(parent.get("max_loss_per_combo", 0) or 0) > 0):
        raise HTTPException(status_code=422, detail="Parent was not a proven defined-risk structure; refusing to close it")

    parent_legs = parent["legs"]
    close_legs = _mirror_close_legs(parent_legs)
    if len(close_legs) != len(parent_legs):
        raise HTTPException(status_code=422, detail="Could not mirror the parent structure")
    if not _same_leg_set(close_legs, parent_legs):
        raise HTTPException(status_code=422, detail="Closing legs do not match the parent structure")

    legs = [OptionQuoteLeg(**leg.model_dump(exclude={"action"})) for leg in close_legs]
    qualified, tickers = await _live_option_tickers(legs)
    live_prices = []
    for leg, ticker in zip(close_legs, tickers):
        bid, ask = _quote_number(ticker.bid), _quote_number(ticker.ask)
        if ticker.marketDataType != 1 or bid is None or ask is None:
            raise HTTPException(
                status_code=422,
                detail="Live executable IBKR bid/ask data is required before a closing order can be submitted",
            )
        live_prices.append(bid if leg.action == "SELL" else -ask)

    net_credit = sum(live_prices)
    if net_credit >= 0:
        raise HTTPException(
            status_code=422,
            detail="Closing this structure should cost money (net cash out); refusing a fill that would pay the position",
        )
    cost_to_close = -net_credit

    quantity = int(parent.get("quantity", 1) or 1)
    if len(close_legs) == 1:
        leg = close_legs[0]
        if leg.action != "BUY" or leg.right not in {"C", "P"}:
            raise HTTPException(status_code=422, detail="Only Advisor-recommended single-leg structures support closing")
        contract = qualified[0]
        limit_price = round(cost_to_close, 2)
        ib_order = LimitOrder("BUY", quantity, limit_price, tif="DAY")
    else:
        bag = Bag(
            symbol=close_legs[0].symbol.upper(),
            exchange="SMART",
            currency="USD",
            comboLegs=[
                ComboLeg(conId=contract.conId, ratio=1, action=leg.action, exchange="SMART")
                for contract, leg in zip(qualified, close_legs)
            ],
        )
        limit_price = round(cost_to_close, 2)
        contract = bag
        ib_order = LimitOrder("BUY", quantity, limit_price, tif="DAY")

    trade = ib.placeOrder(contract, ib_order)
    now = datetime.now(timezone.utc).isoformat()
    ledger_id = str(uuid4())
    status = str(trade.orderStatus.status or "PendingSubmit")
    entry_credit = float(parent.get("net_credit", 0) or 0)
    realized_pnl = (entry_credit - cost_to_close) * quantity
    close_record = {
        "id": ledger_id,
        "close_of": request.ledger_id,
        "reason": request.reason,
        "strategy": parent.get("strategy"),
        "symbol": parent.get("symbol"),
        "legs": [leg.model_dump() for leg in close_legs],
        "quantity": quantity,
        "ibkr_order_id": int(trade.order.orderId),
        "status": status,
        "filled": float(trade.orderStatus.filled or 0),
        "remaining": float(trade.orderStatus.remaining or quantity),
        "average_fill_price": float(trade.orderStatus.avgFillPrice or 0),
        "limit_price": limit_price,
        "net_credit": round(net_credit, 2),
        "cost_to_close": round(cost_to_close, 2),
        "realized_pnl": round(realized_pnl, 2),
        "week_key": _current_week_key(),
        "submitted_at": now,
        "updated_at": now,
    }
    parent["closed_by"] = ledger_id
    parent["closed_at"] = now
    parent["updated_at"] = now
    records.append(close_record)
    _write_order_ledger(records)
    reserved = _reserved_capital(records)
    return {
        "mode": "paper_only",
        "status": status,
        "ledger_id": ledger_id,
        "close_of": request.ledger_id,
        "reason": request.reason,
        "ibkr_order_id": int(trade.order.orderId),
        "limit_price": limit_price,
        "net_credit": round(net_credit, 2),
        "cost_to_close": round(cost_to_close, 2),
        "realized_pnl": round(realized_pnl, 2),
        "quantity": quantity,
        "capital_reserved": round(reserved, 2),
        "capital_remaining": round(max(request.capital_limit - reserved, 0), 2),
        "message": "Paper closing order submitted to IBKR Gateway",
    }
