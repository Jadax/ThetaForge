"""
Scanner and Backtester API routes.
Provides endpoints for running scans and backtests.
"""
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

class BacktestRequest(BaseModel):
    strategy: str
    symbols: List[str] = ["SPY", "QQQ"]
    start_date: str = "2024-01-01"
    end_date: str = "2025-12-31"
    initial_capital: float = 100_000.0

class ScanRequest(BaseModel):
    symbols: Optional[List[str]] = None
    layers: List[str] = ["flow", "dark_pool", "gex", "technical", "catalyst", "risk"]


@router.get("/scan/status")
async def scan_status():
    """Get current scanner status and last scan results."""
    return {
        "status": "ready",
        "scanner_type": "multi-layer-pipeline",
        "layers": ["flow", "dark_pool", "gex", "technical", "catalyst", "risk"],
        "data_sources": ["IBKR", "yfinance", "Reddit"],
    }


@router.post("/scan/full")
async def run_full_scan(request: ScanRequest):
    """Trigger a full multi-layer scan."""
    from agents.scanner.tasks import run_full_scan
    result = run_full_scan.delay()
    return {"task_id": str(result.id), "status": "scan_queued"}


@router.get("/scan/results/{task_id}")
async def get_scan_results(task_id: str):
    """Get results of a scan task."""
    from orchestrator.celery_app import app as celery_app
    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }


@router.get("/gex/{symbol}")
async def get_gex(symbol: str):
    """Get GEX analysis for a symbol."""
    from agents.flow_analysis.gex_engine import GEXEngine
    from agents.data_ingestion.free_data import FreeDataProvider

    provider = FreeDataProvider()
    chain = await provider.get_option_chain(symbol)
    price = await provider.get_stock_price(symbol)

    if not chain or not price:
        return {"error": f"Insufficient data for {symbol}"}

    engine = GEXEngine(underlying_price=price)
    gex_data = engine.calculate_chain_gex(chain, price)
    signals = engine.get_gex_trading_signals(gex_data)
    gex_data["trading_signals"] = signals
    return gex_data


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


@router.get("/strategies")
async def list_strategies():
    """List all available strategies with their win rates."""
    return {
        "strategies": [
            {"name": "wheel", "win_rate": "70-85%", "difficulty": "Easy", "best_market": "Bullish/Neutral", "ivr": ">40"},
            {"name": "vertical_spreads", "win_rate": "65-80%", "difficulty": "Medium", "best_market": "Bullish/Sideways", "ivr": ">40"},
            {"name": "iron_condor", "win_rate": "65-80%", "difficulty": "Hard", "best_market": "Sideways", "ivr": ">50"},
            {"name": "credit_spread", "win_rate": "65-80%", "difficulty": "Medium", "best_market": "Bullish/Sideways", "ivr": ">40"},
            {"name": "covered_call", "win_rate": "75-90%", "difficulty": "Easy", "best_market": "Neutral", "ivr": "Any"},
            {"name": "earnings_straddle", "win_rate": "55-65%", "difficulty": "Hard", "best_market": "High movement", "ivr": "Variable"},
            {"name": "gamma_blast", "win_rate": "30-40%", "difficulty": "Expert", "best_market": "Catalyst", "ivr": "Low VIX"},
            {"name": "leaps", "win_rate": "40-55%", "difficulty": "Easy", "best_market": "Long-term bull", "ivr": "<30"},
            {"name": "calendar_spread", "win_rate": "55-65%", "difficulty": "Medium", "best_market": "Low IV -> High IV", "ivr": "20-50"},
            {"name": "butterfly_spread", "win_rate": "60-75%", "difficulty": "Hard", "best_market": "Range-bound", "ivr": "<30"},
            {"name": "long_call_put", "win_rate": "35-45%", "difficulty": "Easy", "best_market": "Strong trends", "ivr": "30-60"},
        ]
    }
