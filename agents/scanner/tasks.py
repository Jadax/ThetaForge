"""
Celery tasks for Scanner Agent.
Wired to the multi-layer scanner pipeline and Go microservice.
"""
import asyncio
import logging
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)

# Default universe of symbols to scan
DEFAULT_UNIVERSE = [
    # Large Caps
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "V", "JNJ", "WMT", "PG", "UNH", "HD", "MA",
    "DIS", "BAC", "XOM", "CVX", "ABBV", "KO", "PEP", "COST", "AVGO",
    "MRK", "LLY", "TMO", "ADBE", "CRM", "NFLX", "AMD", "INTC", "QCOM",
    # Sector ETFs
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE",
    # Volatility
    "VXX", "UVXY",
]


@app.task(name="agents.scanner.tasks.run_full_scan")
def run_full_scan():
    """
    Full multi-layer scan of the entire universe.
    Layers: Flow -> Dark Pool -> GEX -> Technical -> Catalyst -> Risk
    """
    logger.info("Starting full multi-layer scan...")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_async_full_scan())
        return result
    finally:
        loop.close()


async def _async_full_scan():
    """Async implementation of full scan."""
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.flow_analysis.unusual_activity import UnusualActivityDetector
    from agents.flow_analysis.dark_pool import DarkPoolDetector
    from agents.flow_analysis.gex_engine import GEXEngine
    from agents.flow_analysis.scanner_pipeline import MultiLayerScanner

    data_provider = FreeDataProvider()
    flow_detector = UnusualActivityDetector()
    dark_pool = DarkPoolDetector()
    gex_engine = GEXEngine()
    scanner_pipeline = MultiLayerScanner()

    candidates = []

    # Fetch option chains for each symbol and detect unusual activity
    for symbol in DEFAULT_UNIVERSE:
        try:
            chain = await data_provider.get_option_chain(symbol)
            if not chain:
                continue

            price = await data_provider.get_stock_price(symbol)
            if not price:
                continue

            # Detect unusual activity in the chain
            alerts = flow_detector.scan(chain)

            for alert in alerts:
                alert["underlying_price"] = price
                alert["underlying_trend"] = "NEUTRAL"  # Would be calculated from technicals
                candidates.append(alert)

        except Exception as e:
            logger.warning(f"Scan failed for {symbol}: {e}")

    logger.info(f"Flow scan found {len(candidates)} candidates")

    # Run through multi-layer pipeline
    if candidates:
        final = await scanner_pipeline.scan(candidates, data_provider)
        logger.info(f"Multi-layer pipeline: {len(final)} final candidates")
        return {
            "status": "scan_complete",
            "candidates": len(candidates),
            "final_setups": len(final),
            "layer_breakdown": scanner_pipeline.layer_results,
            "top_picks": final[:10] if final else [],
        }

    return {
        "status": "scan_complete",
        "candidates": 0,
        "final_setups": 0,
        "layer_breakdown": {},
        "top_picks": [],
    }


@app.task(name="agents.scanner.tasks.scan_symbol")
def scan_symbol(symbol: str):
    """Scan a single symbol through the pipeline."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_scan_symbol(symbol))
    finally:
        loop.close()


async def _async_scan_symbol(symbol: str):
    """Async scan of a single symbol."""
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.flow_analysis.unusual_activity import UnusualActivityDetector
    from agents.technical.indicators import TechnicalEngine

    data_provider = FreeDataProvider()
    flow_detector = UnusualActivityDetector()

    price = await data_provider.get_stock_price(symbol)
    chain = await data_provider.get_option_chain(symbol)
    hist = await data_provider.get_historical_prices(symbol, period="3mo")

    result = {
        "symbol": symbol,
        "price": price,
        "chain_length": len(chain),
    }

    if not hist.empty:
        from agents.technical.indicators import TechnicalEngine
        result["technicals"] = TechnicalEngine.calculate_all_indicators(hist)

    if chain:
        alerts = flow_detector.scan(chain)
        result["flow_alerts"] = alerts

    return result
