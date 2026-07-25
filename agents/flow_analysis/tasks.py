"""
Celery tasks for Flow Analysis Agent.
Wired to GEX engine, dark pool detector, and unusual activity scanner.
"""
import asyncio
import logging
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="agents.flow_analysis.tasks.scan_unusual_activity")
def scan_unusual_activity():
    """Scan for unusual options activity across major symbols."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_scan_flow())
    finally:
        loop.close()


async def _async_scan_flow():
    """Async flow scan implementation."""
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.flow_analysis.unusual_activity import UnusualActivityDetector
    from agents.flow_analysis.dark_pool import DarkPoolDetector

    data_provider = FreeDataProvider()
    flow_detector = UnusualActivityDetector()
    dark_pool = DarkPoolDetector()

    symbols = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
    all_alerts = []

    for symbol in symbols:
        try:
            chain = await data_provider.get_option_chain(symbol)
            for option in chain:
                # Unusual activity detection
                vol = option.get("volume", 0)
                oi = option.get("open_interest", 0)
                price = option.get("last", 0) or option.get("ask", 0)

                if vol > 0 and oi > 0:
                    vol_oi_ratio = vol / max(oi, 1)
                    if vol_oi_ratio >= 2.0 and price * vol * 100 >= 25_000:
                        # Dark pool proxy analysis
                        dp_analysis = dark_pool.analyze_volume_anomaly(
                            current_volume=vol,
                            avg_volume_20d=max(oi // 10, 1),  # rough proxy
                            current_oi=oi,
                            prev_oi=max(oi - vol, 0),
                        )
                        all_alerts.append({
                            "symbol": symbol,
                            "strike": option.get("strike"),
                            "expiry": option.get("expiry"),
                            "option_type": option.get("option_type"),
                            "volume": vol,
                            "open_interest": oi,
                            "vol_oi_ratio": round(vol_oi_ratio, 2),
                            "premium": round(price * vol * 100, 2),
                            "dark_pool_signal": dp_analysis.get("dark_pool_signal", False),
                            "confidence": dp_analysis.get("confidence", 0),
                        })
        except Exception as e:
            logger.warning(f"Flow scan failed for {symbol}: {e}")

    # Sort by premium (highest first)
    all_alerts.sort(key=lambda x: x.get("premium", 0), reverse=True)

    logger.info(f"Flow scan complete: {len(all_alerts)} unusual activity signals")
    return {
        "status": "flow_scan_complete",
        "total_signals": len(all_alerts),
        "top_signals": all_alerts[:20],
    }


@app.task(name="agents.flow_analysis.tasks.update_gex")
def update_gex():
    """Update GEX calculations for SPX, SPY, QQQ."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_update_gex())
    finally:
        loop.close()


async def _async_update_gex():
    """Async GEX update implementation."""
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.flow_analysis.gex_engine import GEXEngine

    data_provider = FreeDataProvider()
    results = {}

    for symbol in ["SPY", "QQQ", "IWM"]:
        try:
            chain = await data_provider.get_option_chain(symbol)
            price = await data_provider.get_stock_price(symbol)

            if chain and price:
                engine = GEXEngine(underlying_price=price)
                gex_data = engine.calculate_chain_gex(chain, price)
                signals = engine.get_gex_trading_signals(gex_data)
                gex_data["trading_signals"] = signals
                results[symbol] = gex_data
            else:
                results[symbol] = {"error": "No data available"}

        except Exception as e:
            logger.warning(f"GEX calculation failed for {symbol}: {e}")
            results[symbol] = {"error": str(e)}

    logger.info(f"GEX update complete for {len(results)} symbols")
    return {
        "status": "gex_updated",
        "symbols": results,
    }
