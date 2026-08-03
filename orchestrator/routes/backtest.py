"""
Backtester API routes.
Provides endpoints for running backtests and dark pool analysis.
"""
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter()

class BacktestRequest(BaseModel):
    strategy: str
    symbols: List[str] = ["SPY", "QQQ"]
    start_date: str = "2024-01-01"
    end_date: str = "2025-12-31"
    initial_capital: float = 100_000.0


@router.get("/darkpool/{symbol}")
async def get_dark_pool(symbol: str):
    """Get dark pool analysis for a symbol."""
    from agents.flow_analysis.dark_pool import DarkPoolDetector
    from agents.data_ingestion.free_data import FreeDataProvider

    provider = FreeDataProvider()
    chain = await provider.get_option_chain(symbol)

    if not chain:
        return {"error": f"No data for {symbol}"}

    detector = DarkPoolDetector()
    total_volume = sum(o.get("volume", 0) for o in chain)
    exchange_volume = int(total_volume * 0.6)  # Estimate
    analysis = detector.estimate_dark_pool_volume(total_volume, exchange_volume)

    # Analyze each option for unusual patterns
    block_prints = []
    for opt in chain:
        if opt.get("volume", 0) >= 500:
            blocks = detector.detect_block_prints([{
                "quantity": opt["volume"],
                "price": opt.get("last", 0),
                "option_type": opt.get("option_type"),
                "strike": opt.get("strike"),
                "expiry": opt.get("expiry"),
            }])
            block_prints.extend(blocks)

    analysis["block_prints"] = block_prints[:10]
    return analysis


@router.post("/backtest")
async def run_backtest(request: BacktestRequest):
    """Run a backtest for a strategy."""
    from agents.backtest.backtester import Backtester

    bt = Backtester(initial_capital=request.initial_capital)

    # Load strategy
    strategy_map = {
        "wheel": "agents.strategies.wheel.WheelStrategy",
        "iron_condor": "agents.strategies.iron_condor.IronCondorStrategy",
        "credit_spread": "agents.strategies.credit_spread.CreditSpreadStrategy",
        "vertical_spreads": "agents.strategies.vertical_spreads.VerticalSpreadStrategy",
        "covered_call": "agents.strategies.covered_call.CoveredCallStrategy",
        "leaps": "agents.strategies.leaps.LEAPSStrategy",
    }

    if request.strategy not in strategy_map:
        return {"error": f"Unknown strategy: {request.strategy}. Available: {list(strategy_map.keys())}"}

    # Import and instantiate strategy
    module_path, class_name = strategy_map[request.strategy].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)
    strategy = strategy_class()

    # Run backtest
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            bt.run(strategy, request.symbols, request.start_date, request.end_date)
        )
    finally:
        loop.close()

    return result
