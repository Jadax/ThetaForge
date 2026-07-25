"""Personal, localhost-only bridge between ThetaForge and IBKR paper trading."""
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ib_insync import IB, LimitOrder, Option
import ib_insync.connection as ib_connection


HOST = os.getenv("IBKR_HOST", "127.0.0.1")
PAPER_PORT = int(os.getenv("IBKR_PAPER_PORT", "4002"))
CLIENT_ID = int(os.getenv("IBKR_BRIDGE_CLIENT_ID", "17"))
ACCESS_TOKEN = os.getenv("BRIDGE_ACCESS_TOKEN", "")
PAPER_ONLY = os.getenv("PAPER_TRADING_ONLY", "true").lower() == "true"
# Do not construct IB() at import time. On Windows uvicorn creates its running
# asyncio loop after importing this module; an IB instance created too early can
# retain a Future from that old loop ("attached to a different loop").
ib: IB | None = None
staged_orders: dict[str, "PaperOrder"] = {}


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
    try:
        yield
    finally:
        if ib and ib.isConnected():
            ib.disconnect()
        ib = None


app = FastAPI(title="ThetaForge Local IBKR Bridge", version="0.1.1", lifespan=lifespan)
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


class PaperOrder(BaseModel):
    symbol: str
    expiry: str
    strike: float = Field(gt=0)
    right: Literal["C", "P"]
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=100)
    limit_price: float = Field(gt=0)


async def require_access_token(x_thetaforge_bridge_token: str | None = Header(default=None)) -> None:
    """Require a token whenever one is configured for remote/private-network use."""
    if ACCESS_TOKEN and x_thetaforge_bridge_token != ACCESS_TOKEN:
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
    return [{"symbol": item.contract.symbol, "position": item.position, "average_cost": item.avgCost} for item in ib.positions()]


@app.post("/orders/stage")
async def stage_order(order: PaperOrder, _: None = Depends(require_access_token)):
    order_id = str(uuid4())
    staged_orders[order_id] = order
    return {"order_id": order_id, "mode": "paper_only", "order": order, "requires_confirmation": True}


@app.post("/orders/{order_id}/submit")
async def submit_paper_order(order_id: str, confirm_paper_order: bool = False, _: None = Depends(require_access_token)):
    if not confirm_paper_order:
        raise HTTPException(status_code=400, detail="Set confirm_paper_order=true to submit this PAPER order")
    order = staged_orders.pop(order_id, None)
    if not order:
        raise HTTPException(status_code=404, detail="Staged order not found")
    await ensure_connected()
    assert ib is not None
    contract = Option(order.symbol.upper(), order.expiry, order.strike, order.right, "SMART")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise HTTPException(status_code=422, detail="IBKR could not qualify this option contract")
    trade = ib.placeOrder(contract, LimitOrder(order.action, order.quantity, order.limit_price))
    return {"mode": "paper_only", "status": str(trade.orderStatus.status), "order_id": order_id}
