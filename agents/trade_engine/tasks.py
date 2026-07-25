"""
Celery tasks for the Trade Advisor.
Periodically generates recommendations based on account state.
"""
import logging
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="agents.trade_engine.tasks.generate_advisory")
def generate_advisory(
    capital: float,
    buying_power: float,
    risk_tolerance: str = "moderate",
    watchlist: list = None,
    max_positions: int = 10,
):
    """
    Generate trade recommendations for the user's account.
    Called on-demand or periodically.
    """
    if watchlist is None:
        watchlist = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]

    from agents.trade_engine.recommender import TradeRecommender
    from agents.trade_engine.models import AccountInfo, RiskTolerance
    from agents.data_ingestion.free_data import FreeDataProvider
    from agents.technical.indicators import TechnicalAnalyzer
    from agents.flow_analysis.gex_engine import GEXEngine

    provider = FreeDataProvider()
    recommender = TradeRecommender()
    tech_analyzer = TechnicalAnalyzer()
    gex_engine = GEXEngine()

    try:
        risk = RiskTolerance(risk_tolerance)
    except ValueError:
        risk = RiskTolerance.MODERATE

    account = AccountInfo(
        total_equity=capital,
        buying_power=buying_power,
        cash_available=buying_power,
        risk_tolerance=risk,
        max_positions=max_positions,
    )

    # Fetch data
    market_data = {}
    option_chains = {}
    technical_data = {}
    volatility_data = {}

    for symbol in watchlist:
        try:
            info = provider.get_stock_info(symbol)
            if info:
                market_data[f"{symbol}_price"] = info.get("regularMarketPrice", 0)
            chain = provider.get_option_chain(symbol)
            if chain:
                option_chains[symbol] = chain
            hist = provider.get_historical(symbol, period="6mo")
            if hist is not None and len(hist) > 0:
                technical_data[symbol] = tech_analyzer.analyze(hist)
        except Exception as e:
            logger.warning(f"Failed to fetch data for {symbol}: {e}")

    vix_data = provider.get_vix()
    if vix_data:
        market_data["vix"] = vix_data.get("regularMarketPrice", 20)

    volatility_data = {"iv": 0.20, "hv_20": 0.18, "iv_rank": 50, "dte": 30}

    output = recommender.generate_recommendations(
        account=account,
        market_data=market_data,
        option_chains=option_chains,
        technical_data=technical_data,
        flow_data={},
        volatility_data=volatility_data,
    )

    logger.info(f"Generated {len(output.recommendations)} recommendations")

    return {
        "recommendations_count": len(output.recommendations),
        "capital_deployed": output.total_capital_deployed,
        "remaining_bp": output.remaining_buying_power,
        "warnings": output.warnings,
        "market_context": output.market_context,
    }


@app.task(name="agents.trade_engine.tasks.compare_opportunities")
def compare_opportunities(watchlist: list = None):
    """
    Compare ROI across all available options chains.
    The OptionsellerROI killer feature - runs periodically.
    """
    if watchlist is None:
        watchlist = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]

    from agents.trade_engine.roi_calculator import ROICalculator
    from agents.data_ingestion.free_data import FreeDataProvider

    roi_calc = ROICalculator()
    provider = FreeDataProvider()

    all_opportunities = []
    for symbol in watchlist:
        try:
            info = provider.get_stock_info(symbol)
            stock_price = info.get("regularMarketPrice", 0) if info else 0
            if stock_price <= 0:
                continue
            chain = provider.get_option_chain(symbol)
            if not chain:
                continue

            csp_results = roi_calc.scan_all_strikes_csp(chain, stock_price, 30)
            for r in csp_results:
                r["symbol"] = symbol
                r["strategy"] = "csp"
            all_opportunities.extend(csp_results[:5])

            cc_results = roi_calc.scan_all_strikes_cc(chain, stock_price, 30)
            for r in cc_results:
                r["symbol"] = symbol
                r["strategy"] = "cc"
            all_opportunities.extend(cc_results[:5])
        except Exception as e:
            logger.warning(f"ROI comparison failed for {symbol}: {e}")

    ranked = roi_calc.rank_opportunities(all_opportunities)
    logger.info(f"Compared {len(ranked)} opportunities across {len(watchlist)} symbols")

    return {
        "total_opportunities": len(ranked),
        "top_5": ranked[:5],
    }
