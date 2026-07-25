"""
Position management routes.
"""
from fastapi import APIRouter

router = APIRouter()

@router.get("/")
async def get_positions():
    # Placeholder for fetching current positions from IBKR
    return {"positions": [], "message": "Positions fetched from IBKR."}

@router.get("/greeks")
async def get_portfolio_greeks():
    # Placeholder for aggregated portfolio Greeks
    return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
