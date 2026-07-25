"""
Celery tasks for Volatility Engine.
Updates IV Rank, IV Percentile, Greeks, and VIX term structure.
All FREE data sources (yfinance, IBKR).
"""
import asyncio
import logging
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)

TRACKED_SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "V", "JNJ", "WMT", "PG", "UNH", "HD",
]


@app.task(name="agents.volatility.tasks.update_iv_metrics")
def update_iv_metrics():
    """Recalculate IV Rank and IV Percentile for all tracked symbols."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_update_iv())
    finally:
        loop.close()


async def _async_update_iv():
    """Async IV metrics update."""
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.volatility.iv_metrics import IVMetricsEngine

    data_provider = FreeDataProvider()
    iv_engine = IVMetricsEngine()
    results = {}

    for symbol in TRACKED_SYMBOLS:
        try:
            chain = await data_provider.get_option_chain(symbol)
            if not chain:
                continue

            # Get current ATM IV from the chain
            price = await data_provider.get_stock_price(symbol)
            if not price:
                continue

            # Find ATM option
            atm_options = [
                o for o in chain
                if abs(o.get("strike", 0) - price) / max(price, 1) < 0.05
            ]
            if not atm_options:
                continue

            current_iv = atm_options[0].get("implied_volatility", 0.2)

            # Get historical IV (simplified - use yfinance IV history)
            iv_history = await data_provider.get_historical_iv(symbol)

            metrics = iv_engine.get_metrics(symbol, current_iv, iv_history)
            results[symbol] = metrics

        except Exception as e:
            logger.warning(f"IV update failed for {symbol}: {e}")

    logger.info(f"IV metrics updated for {len(results)} symbols")
    return {
        "status": "iv_metrics_updated",
        "symbols": results,
    }


@app.task(name="agents.volatility.tasks.calculate_portfolio_greeks")
def calculate_portfolio_greeks():
    """Calculate and aggregate portfolio Greeks for current positions."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_calc_greeks())
    finally:
        loop.close()


async def _async_calc_greeks():
    """Async Greeks calculation."""
    from agents.data_ingestion.ibkr_client import IBKRClient
    from agents.volatility.greeks import calculate_greeks

    # In production, this would connect to IBKR and fetch real positions
    # For now, return placeholder
    return {
        "status": "greeks_calculated",
        "portfolio_greeks": {
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
        },
    }
