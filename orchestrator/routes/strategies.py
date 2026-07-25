"""
Strategy management routes.
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

ALL_STRATEGIES = [
    "wheel",
    "vertical_spreads",
    "iron_condor",
    "credit_spread",
    "covered_call",
    "earnings_straddle",
    "gamma_blast",
    "leaps",
    "calendar_spread",
    "butterfly_spread",
    "long_call_put",
]

class StrategyConfig(BaseModel):
    name: str
    enabled: bool = True
    allocation_pct: float = 10.0
    max_concurrent_positions: int = 5
    profit_target_pct: float = 50.0
    stop_loss_multiplier: float = 2.0
    min_iv_rank: float = 0.0
    max_iv_rank: float = 100.0
    risk_per_trade_pct: float = 2.0

@router.get("/")
async def list_strategies():
    return {"strategies": ALL_STRATEGIES, "count": len(ALL_STRATEGIES)}

@router.get("/{strategy_name}")
async def get_strategy_details(strategy_name: str):
    """Get details and parameters for a specific strategy."""
    if strategy_name not in ALL_STRATEGIES:
        return {"error": f"Unknown strategy: {strategy_name}"}
    return {
        "name": strategy_name,
        "status": "active",
        "description": f"ThetaForge {strategy_name} strategy",
    }

@router.post("/configure")
async def configure_strategy(config: StrategyConfig):
    # In production, this would update a database or Redis config
    return {"message": f"Strategy {config.name} configured.", "config": config}
