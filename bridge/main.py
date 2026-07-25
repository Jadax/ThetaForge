"""Personal, localhost-only bridge between ThetaForge and IBKR paper trading."""
import os
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ib_insync import IB, LimitOrder, Option


HOST = os.getenv("IBKR_HOST", "127.0.0.1")
PAPER_PORT = int(os.getenv("IBKR_PAPER_PORT", "4002"))
CLIENT_ID = int(os.getenv("IBKR_BRIDGE_CLIENT_ID", "17"))
ib = IB()
staged_orders: dict[str, "PaperOrder"] = {}

app = FastAPI(title="ThetaForge Local IBKR Bridge", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "https://jadax.github.io"],
    allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
)


class PaperOrder(BaseModel):
    symbol: str
    expiry: str
    strike: float = Field(gt=0)
    right: Literal["C", "P"]
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=100)
    limit_price: float = Field(gt=0)


async def ensure_connected() -> None:
    if not ib.isConnected():
        try:
            await ib.connectAsync(HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=8)
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"Paper TWS/IB Gateway is unavailable: {error}") from error


@app.get("/health")
async def health():
    return {"mode": "paper_only", "connected": ib.isConnected(), "host": HOST, "port": PAPER_PORT}


@app.post("/connect")
async def connect():
    await ensure_connected()
    return {"mode": "paper_only", "connected": True}


@app.get("/positions")
async def positions():
    await ensure_connected()
    return [{"symbol": item.contract.symbol, "position": item.position, "average_cost": item.avgCost} for item in ib.positions()]


@app.post("/orders/stage")
async def stage_order(order: PaperOrder):
    order_id = str(uuid4())
    staged_orders[order_id] = order
    return {"order_id": order_id, "mode": "paper_only", "order": order, "requires_confirmation": True}


@app.post("/orders/{order_id}/submit")
async def submit_paper_order(order_id: str, confirm_paper_order: bool = False):
    if not confirm_paper_order:
        raise HTTPException(status_code=400, detail="Set confirm_paper_order=true to submit this PAPER order")
    order = staged_orders.pop(order_id, None)
    if not order:
        raise HTTPException(status_code=404, detail="Staged order not found")
    await ensure_connected()
    contract = Option(order.symbol.upper(), order.expiry, order.strike, order.right, "SMART")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise HTTPException(status_code=422, detail="IBKR could not qualify this option contract")
    trade = ib.placeOrder(contract, LimitOrder(order.action, order.quantity, order.limit_price))
    return {"mode": "paper_only", "status": str(trade.orderStatus.status), "order_id": order_id}
