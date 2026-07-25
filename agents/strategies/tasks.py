"""
Celery tasks for Strategy Agent.
Runs strategy-specific scans wired to actual strategy logic.
"""
import asyncio
import logging
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY = {}


def _get_strategy(name: str):
    """Lazy-load strategy instances."""
    if name not in STRATEGY_REGISTRY:
        _load_strategies()
    return STRATEGY_REGISTRY.get(name)


def _load_strategies():
    """Load all strategy instances into registry."""
    from agents.strategies.wheel import WheelStrategy
    from agents.strategies.vertical_spreads import VerticalSpreadStrategy
    from agents.strategies.iron_condor import IronCondorStrategy
    from agents.strategies.credit_spread import CreditSpreadStrategy
    from agents.strategies.covered_call import CoveredCallStrategy
    from agents.strategies.earnings_straddle import EarningsStraddleStrategy
    from agents.strategies.gamma_blast import GammaBlastStrategy
    from agents.strategies.leaps import LEAPSStrategy
    from agents.strategies.calendar_spread import CalendarSpreadStrategy
    from agents.strategies.butterfly_spread import ButterflySpreadStrategy
    from agents.strategies.long_call_put import LongCallPutStrategy

    STRATEGY_REGISTRY.update({
        "wheel": WheelStrategy(),
        "vertical_spreads": VerticalSpreadStrategy(),
        "iron_condor": IronCondorStrategy(),
        "credit_spread": CreditSpreadStrategy(),
        "covered_call": CoveredCallStrategy(),
        "earnings_straddle": EarningsStraddleStrategy(),
        "gamma_blast": GammaBlastStrategy(),
        "leaps": LEAPSStrategy(),
        "calendar_spread": CalendarSpreadStrategy(),
        "butterfly_spread": ButterflySpreadStrategy(),
        "long_call_put": LongCallPutStrategy(),
    })


async def _run_strategy_scan(strategy_name: str):
    """Run a single strategy scan with real data."""
    from agents.data_ingestion.free_data import FreeDataProvider

    data_provider = FreeDataProvider()
    strategy = _get_strategy(strategy_name)

    if not strategy:
        return {"error": f"Strategy {strategy_name} not found"}

    # Build market data snapshot
    market_data = {}
    symbols = getattr(strategy, "symbols", ["SPY", "QQQ", "IWM"])

    for symbol in symbols:
        try:
            price = await data_provider.get_stock_price(symbol)
            chain = await data_provider.get_option_chain(symbol)
            market_data[f"{symbol}_price"] = price
            market_data[f"{symbol}_chain"] = chain
            market_data[f"{symbol}_iv_rank"] = 50  # Would be calculated
        except Exception:
            pass

    try:
        signals = await strategy.scan(market_data)
        return {
            "status": f"{strategy_name}_scan_complete",
            "signals_found": len(signals),
            "signals": [
                {
                    "symbol": s.symbol,
                    "action": s.action,
                    "strike": s.strike,
                    "expiry": s.expiry,
                    "option_type": s.option_type,
                    "confidence": s.confidence_score,
                }
                for s in signals
            ],
        }
    except Exception as e:
        logger.error(f"Strategy scan failed for {strategy_name}: {e}")
        return {"status": "error", "error": str(e)}


@app.task(name="agents.strategies.tasks.run_wheel_scan")
def run_wheel_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("wheel"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_iron_condor_scan")
def run_iron_condor_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("iron_condor"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_credit_spread_scan")
def run_credit_spread_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("credit_spread"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_vertical_spread_scan")
def run_vertical_spread_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("vertical_spreads"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_covered_call_scan")
def run_covered_call_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("covered_call"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_earnings_scan")
def run_earnings_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("earnings_straddle"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_gamma_blast_scan")
def run_gamma_blast_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("gamma_blast"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_leaps_scan")
def run_leaps_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("leaps"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_calendar_spread_scan")
def run_calendar_spread_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("calendar_spread"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_butterfly_scan")
def run_butterfly_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("butterfly_spread"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_long_call_put_scan")
def run_long_call_put_scan():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_strategy_scan("long_call_put"))
    finally:
        loop.close()


@app.task(name="agents.strategies.tasks.run_all_strategies")
def run_all_strategies():
    """Run all strategy scans concurrently."""
    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(_async_run_all())
        return results
    finally:
        loop.close()


async def _async_run_all():
    """Run all strategy scans asynchronously."""
    strategies = [
        "wheel", "iron_condor", "credit_spread", "vertical_spreads",
        "covered_call", "earnings_straddle", "gamma_blast",
        "leaps", "calendar_spread", "butterfly_spread", "long_call_put",
    ]

    tasks = [_run_strategy_scan(s) for s in strategies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary = {}
    total_signals = 0
    for name, result in zip(strategies, results):
        if isinstance(result, Exception):
            summary[name] = {"error": str(result)}
        else:
            summary[name] = result
            total_signals += result.get("signals_found", 0)

    return {
        "status": "all_strategies_complete",
        "total_signals": total_signals,
        "strategies": summary,
    }
