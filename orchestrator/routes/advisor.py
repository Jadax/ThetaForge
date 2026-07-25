"""
Advisor API Routes.
The main API that takes account info and returns specific trade recommendations.
Wired to the AI Brain for unified signal analysis.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.ai_brain import AIBrain, TimeHorizon
from agents.trade_engine.watchlist import FavoritesStore
from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, StrategyType
)
from agents.data_ingestion.free_data import FreeDataProvider
from agents.technical.indicators import TechnicalEngine as TechAnalyzer
from agents.flow_analysis.gex_engine import GEXEngine

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

provider = FreeDataProvider()
recommender = TradeRecommender()
tech_analyzer = TechAnalyzer()
gex_engine = GEXEngine()
brain = AIBrain()
watchlist_store = FavoritesStore()


# === Request/Response Models ===

class AdvisoryRequest(BaseModel):
    capital: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Available buying power")
    risk_tolerance: str = Field("moderate", description="conservative/moderate/aggressive")
    watchlist: List[str] = Field(default_factory=list, description="Symbols to analyze")
    max_positions: int = Field(10, description="Maximum open positions")
    current_positions: List[Dict[str, Any]] = Field(default_factory=list)


class BrainAnalysisRequest(BaseModel):
    symbol: str
    stock_price: float = 0
    horizon: str = Field("1m", description="1w/1m/3m/6m")


class WatchlistAddRequest(BaseModel):
    symbol: str
    notes: str = ""
    tags: List[str] = Field(default_factory=list)
    custom_delta: float = 0.3
    custom_dte: int = 45
    custom_strategies: List[str] = Field(default_factory=list)


class WatchlistUpdateRequest(BaseModel):
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    custom_delta: Optional[float] = None
    custom_dte: Optional[int] = None
    custom_strategies: Optional[List[str]] = None


# === AI Brain Endpoints ===

@router.post("/brain/analyze")
async def brain_analyze(request: BrainAnalysisRequest):
    """
    AI Brain analysis for a single symbol.
    Returns unified signals, regime, strategy recommendations,
    and time-horizon specific trade ideas.
    """
    symbol = request.symbol.upper()

    # Fetch market data
    stock_price = request.stock_price
    option_chain = []
    historical = []
    current_iv = 0.20
    hv_20 = 0.18
    iv_52w_high = 0.40
    iv_52w_low = 0.12
    vix = 20.0
    gex_data = None
    flow_data = None
    pcr_data = None

    try:
        info = provider.get_stock_info(symbol)
        if info:
            stock_price = stock_price or info.get("regularMarketPrice", 0)
    except Exception:
        pass

    try:
        chain = provider.get_option_chain(symbol)
        if chain:
            option_chain = chain if isinstance(chain, list) else []
    except Exception:
        pass

    try:
        hist = provider.get_historical(symbol, period="1y")
        if hist is not None and len(hist) > 0:
            historical = hist["Close"].tolist() if hasattr(hist, "tolist") else list(hist["Close"])
            if "High" in hist.columns:
                high_prices = hist["High"].tolist()
            else:
                high_prices = historical
            if "Low" in hist.columns:
                low_prices = hist["Low"].tolist()
            else:
                low_prices = historical

            # Calculate HV
            import math
            if len(historical) >= 20:
                returns = [
                    math.log(historical[i] / historical[i - 1])
                    for i in range(1, min(21, len(historical)))
                ]
                hv_20 = (sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)) ** 0.5 * math.sqrt(252)
        else:
            high_prices = [stock_price * 1.01]
            low_prices = [stock_price * 0.99]
    except Exception:
        historical = [stock_price]
        high_prices = [stock_price * 1.01]
        low_prices = [stock_price * 0.99]

    try:
        vix_data = provider.get_vix()
        if vix_data:
            vix = vix_data.get("regularMarketPrice", 20)
    except Exception:
        pass

    # GEX
    try:
        if option_chain and stock_price:
            gex_data = gex_engine.calculate_gex(option_chain, stock_price)
    except Exception:
        pass

    # Run Brain
    output = brain.analyze(
        symbol=symbol,
        stock_price=stock_price,
        option_chain=option_chain,
        historical_prices=historical,
        high_prices=high_prices,
        low_prices=low_prices,
        current_iv=current_iv,
        hv_20=hv_20,
        iv_52w_high=iv_52w_high,
        iv_52w_low=iv_52w_low,
        vix=vix,
        gex_data=gex_data,
        flow_data=flow_data,
        pcr_data=pcr_data,
    )

    return {
        "symbol": output.symbol,
        "stock_price": output.stock_price,
        "overall_signal": output.overall_signal.value,
        "overall_score": output.overall_score,
        "confidence": output.confidence,
        "regime": output.regime,
        "best_strategy": output.best_strategy,
        "best_strategy_reasoning": output.best_strategy_reasoning,
        "cpr_signal": output.cpr_signal,
        "iv_signal": output.iv_signal,
        "sentiment_signal": output.sentiment_signal,
        "sideways_signal": output.sideways_signal,
        "recommendations_1w": output.recommendations_1w,
        "recommendations_1m": output.recommendations_1m,
        "recommendations_3m": output.recommendations_3m,
        "recommendations_6m": output.recommendations_6m,
        "all_signals": output.all_signals,
    }


@router.post("/brain/analyze-watchlist")
async def brain_analyze_watchlist(request: AdvisoryRequest):
    """
    AI Brain analysis for the full watchlist.
    Returns a ranked list of symbols with their Brain scores.
    """
    symbols = request.watchlist
    if not symbols:
        # Load from watchlist store
        items = watchlist_store.list_symbols()
        symbols = [item.symbol for item in items]

    results = []
    for symbol in symbols:
        try:
            info = provider.get_stock_info(symbol)
            stock_price = info.get("regularMarketPrice", 0) if info else 0
        except Exception:
            stock_price = 0

        if stock_price <= 0:
            continue

        brain_req = BrainAnalysisRequest(symbol=symbol, stock_price=stock_price)
        result = await brain_analyze(brain_req)
        results.append(result)

    # Rank by overall_score descending
    results.sort(key=lambda x: x["overall_score"], reverse=True)

    return {
        "total_analyzed": len(results),
        "rankings": results,
    }


# === Watchlist Endpoints ===

@router.get("/watchlist")
async def get_watchlist():
    """Get all symbols in the watchlist."""
    items = watchlist_store.list_symbols()
    return {
        "count": len(items),
        "items": [
            {
                "symbol": item.symbol,
                "added_at": item.added_at,
                "notes": item.notes,
                "tags": item.tags,
                "custom_delta": item.custom_delta,
                "custom_dte": item.custom_dte,
                "custom_strategies": item.custom_strategies,
            }
            for item in items
        ],
    }


@router.post("/watchlist/add")
async def add_to_watchlist(request: WatchlistAddRequest):
    """Add a symbol to the watchlist."""
    item = watchlist_store.add_symbol(
        symbol=request.symbol,
        notes=request.notes,
        tags=request.tags,
        custom_delta=request.custom_delta,
        custom_dte=request.custom_dte,
        custom_strategies=request.custom_strategies,
    )
    return {"status": "added", "symbol": item.symbol}


@router.delete("/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str):
    """Remove a symbol from the watchlist."""
    removed = watchlist_store.remove_symbol(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist")
    return {"status": "removed", "symbol": symbol.upper()}


@router.patch("/watchlist/{symbol}")
async def update_watchlist_item(symbol: str, request: WatchlistUpdateRequest):
    """Update a watchlist item's preferences."""
    item = watchlist_store.update_symbol(
        symbol=symbol,
        notes=request.notes,
        tags=request.tags,
        custom_delta=request.custom_delta,
        custom_dte=request.custom_dte,
        custom_strategies=request.custom_strategies,
    )
    if not item:
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist")
    return {"status": "updated", "symbol": item.symbol}


# === Dashboard Endpoint ===

class DashboardRequest(BaseModel):
    capital: float = Field(100000, description="Total account equity")
    buying_power: float = Field(50000, description="Available buying power")
    current_positions: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/dashboard")
async def get_dashboard(request: DashboardRequest):
    """
    One-call full portfolio dashboard.
    Returns: VIX, regime, watchlist rankings, top opportunities,
    portfolio risk summary, and time-horizon breakdowns.
    """
    import math

    # Load watchlist
    items = watchlist_store.list_symbols()
    symbols = [item.symbol for item in items]
    if not symbols:
        symbols = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]

    # VIX
    vix = 20.0
    try:
        vix_data = provider.get_vix()
        if vix_data:
            vix = vix_data.get("regularMarketPrice", 20)
    except Exception:
        pass

    regime = "neutral"
    if vix > 30:
        regime = "high_vol"
    elif vix > 22:
        regime = "bearish"
    elif vix < 15:
        regime = "bullish"

    # Analyze all symbols
    rankings = []
    for symbol in symbols:
        try:
            info = provider.get_stock_info(symbol)
            stock_price = info.get("regularMarketPrice", 0) if info else 0
        except Exception:
            stock_price = 0
        if stock_price <= 0:
            continue

        try:
            brain_req = BrainAnalysisRequest(
                symbol=symbol, stock_price=stock_price, horizon="1m"
            )
            result = await brain_analyze(brain_req)
            rankings.append(result)
        except Exception:
            continue

    rankings.sort(key=lambda x: x["overall_score"], reverse=True)

    # Portfolio risk
    net_delta = sum(p.get("delta", 0) for p in request.current_positions)
    net_vega = sum(p.get("vega", 0) for p in request.current_positions)
    capital_deployed = sum(
        p.get("margin", 0) for p in request.current_positions
    )
    capital_pct = (capital_deployed / request.capital * 100) if request.capital > 0 else 0

    # Top picks per horizon
    top_1w = [r for r in rankings if any(
        rec.get("suitability", 0) >= 70 for rec in r.get("recommendations_1w", [])
    )][:3]
    top_1m = [r for r in rankings if any(
        rec.get("suitability", 0) >= 70 for rec in r.get("recommendations_1m", [])
    )][:3]

    return {
        "vix": round(vix, 2),
        "regime": regime,
        "account": {
            "equity": request.capital,
            "buying_power": request.buying_power,
            "capital_deployed": round(capital_deployed, 2),
            "capital_deployed_pct": round(capital_pct, 1),
            "num_positions": len(request.current_positions),
        },
        "portfolio_risk": {
            "net_delta": round(net_delta, 4),
            "net_vega": round(net_vega, 4),
            "within_limits": abs(net_delta) < 0.20 and abs(net_vega) < 0.05,
        },
        "watchlist_rankings": rankings,
        "top_picks_1w": [{"symbol": r["symbol"], "signal": r["overall_signal"], "score": r["overall_score"]} for r in top_1w],
        "top_picks_1m": [{"symbol": r["symbol"], "signal": r["overall_signal"], "score": r["overall_score"]} for r in top_1m],
    }


# === Legacy Endpoints ===

@router.post("/recommend")
async def get_recommendations(request: AdvisoryRequest):
    """
    MAIN ENDPOINT: Capital In -> Specific Trade Recommendations Out.
    """
    try:
        risk = RiskTolerance(request.risk_tolerance)
    except ValueError:
        risk = RiskTolerance.MODERATE

    account = AccountInfo(
        total_equity=request.capital,
        buying_power=request.buying_power,
        cash_available=request.buying_power,
        current_positions=request.current_positions,
        risk_tolerance=risk,
        max_positions=request.max_positions,
    )

    market_data = {}
    option_chains = {}
    technical_data = {}
    volatility_data = {}

    for symbol in request.watchlist:
        try:
            info = provider.get_stock_info(symbol)
            if info:
                market_data[f"{symbol}_price"] = info.get("regularMarketPrice", 0)

            chain = provider.get_option_chain(symbol)
            if chain:
                option_chains[symbol] = chain

            hist = provider.get_historical(symbol, period="6mo")
            if hist is not None and len(hist) > 0:
                technical_data[symbol] = tech_analyzer.calculate_all_indicators(hist)

            if chain and market_data.get(f"{symbol}_price"):
                gex = gex_engine.calculate_gex(chain, market_data[f"{symbol}_price"])
                market_data[f"{symbol}_gex"] = gex

        except Exception:
            continue

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

    return {
        "account_summary": {
            "total_equity": output.account_summary.total_equity,
            "buying_power": output.account_summary.buying_power,
            "risk_tolerance": output.account_summary.risk_tolerance.value,
            "current_positions": len(output.account_summary.current_positions),
        },
        "market_context": output.market_context,
        "portfolio_analysis": output.portfolio_analysis,
        "total_capital_deployed": output.total_capital_deployed,
        "remaining_buying_power": output.remaining_buying_power,
        "recommendations": [
            {
                "id": r.recommendation_id,
                "strategy": r.strategy_type.value,
                "symbol": r.symbol,
                "underlying_price": r.underlying_price,
                "legs": [
                    {
                        "action": leg.action,
                        "strike": leg.contract.strike,
                        "expiry": leg.contract.expiry,
                        "type": leg.contract.option_type,
                        "bid": leg.contract.bid,
                        "ask": leg.contract.ask,
                    }
                    for leg in r.legs
                ],
                "quantity": r.quantity,
                "net_credit": r.net_credit,
                "net_debit": r.net_debit,
                "max_profit": r.max_profit,
                "max_loss": r.max_loss,
                "breakeven": r.breakeven,
                "probability_of_profit": r.probability_of_profit,
                "return_on_capital_pct": r.return_on_capital_pct,
                "annualized_return_pct": r.annualized_return_pct,
                "composite_score": r.composite_score,
                "confidence_score": r.confidence_score,
                "iv_rank": r.iv_rank,
                "vix": r.vix,
                "market_regime": r.market_regime.value,
                "reasoning": r.reasoning,
                "risk_warning": r.risk_warning,
                "entry_rules": r.entry_rules,
                "exit_rules": r.exit_rules,
            }
            for r in output.recommendations
        ],
        "warnings": output.warnings,
    }


@router.post("/compare")
async def compare_opportunities(request: AdvisoryRequest):
    """Compare ROI across all available options chains."""
    from agents.trade_engine.roi_calculator import ROICalculator
    roi_calc = ROICalculator()

    all_opportunities = []
    for symbol in request.watchlist:
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

            cc_results = roi_calc.scan_all_strikes_cc(chain, stock_price, 30)
            for r in cc_results:
                r["symbol"] = symbol
                r["strategy"] = "cc"

            all_opportunities.extend(csp_results[:5])
            all_opportunities.extend(cc_results[:5])

        except Exception:
            continue

    ranked = roi_calc.rank_opportunities(all_opportunities, "annualized_return_pct")

    return {
        "total_opportunities": len(ranked),
        "top_opportunities": ranked[:20],
        "watchlist": request.watchlist,
    }


@router.get("/analytics/{symbol}")
async def get_analytics(symbol: str):
    """Get complete options analytics for a symbol."""
    from agents.trade_engine.analytics import OptionsAnalytics
    analytics = OptionsAnalytics()

    stock_price = 0
    chain = []

    try:
        info = provider.get_stock_info(symbol)
        stock_price = info.get("regularMarketPrice", 0) if info else 0
    except Exception:
        pass

    try:
        chain = provider.get_option_chain(symbol) or []
    except Exception:
        pass

    max_pain = analytics.max_pain(chain) if chain else {}
    exp_move = analytics.expected_move(stock_price, 0.20, 30) if stock_price else {}
    support_resistance = analytics.support_resistance_from_oi(chain, stock_price) if chain and stock_price else {}

    return {
        "symbol": symbol,
        "stock_price": stock_price,
        "max_pain": max_pain,
        "expected_move": exp_move,
        "support_resistance": support_resistance,
    }


@router.get("/strategies")
async def list_strategies():
    """List all available strategies."""
    return {
        "strategies": [
            {"name": "Cash-Secured Put", "type": "csp", "win_rate": "70-85%", "best_iv": "High (>50)"},
            {"name": "Covered Call", "type": "cc", "win_rate": "75-90%", "best_iv": "Any"},
            {"name": "Bull Put Credit Spread", "type": "bull_put", "win_rate": "65-80%", "best_iv": "High (>50)"},
            {"name": "Bear Call Credit Spread", "type": "bear_call", "win_rate": "65-80%", "best_iv": "High (>50)"},
            {"name": "Iron Condor", "type": "iron_condor", "win_rate": "65-80%", "best_iv": "High (>50)"},
            {"name": "Iron Butterfly", "type": "iron_butterfly", "win_rate": "60-75%", "best_iv": "High (>60)"},
            {"name": "Wheel", "type": "wheel", "win_rate": "70-85%", "best_iv": "High (>50)"},
            {"name": "LEAPS", "type": "leaps", "win_rate": "40-55%", "best_iv": "Low (<30)"},
            {"name": "PMCC", "type": "pmcc", "win_rate": "55-70%", "best_iv": "Low IV"},
            {"name": "Call Debit Spread", "type": "call_debit", "win_rate": "45-55%", "best_iv": "Low (<30)"},
            {"name": "Put Debit Spread", "type": "put_debit", "win_rate": "45-55%", "best_iv": "Low (<30)"},
            {"name": "Calendar Spread", "type": "calendar", "win_rate": "55-65%", "best_iv": "Low + steep term structure"},
            {"name": "Butterfly", "type": "butterfly", "win_rate": "60-75%", "best_iv": "Low + pin expected"},
            {"name": "Long Call", "type": "long_call", "win_rate": "35-45%", "best_iv": "Low (<25th pct)"},
            {"name": "Long Put", "type": "long_put", "win_rate": "35-45%", "best_iv": "Low (<25th pct)"},
            {"name": "Straddle", "type": "straddle", "win_rate": "55-65%", "best_iv": "Low + big move expected"},
            {"name": "Strangle", "type": "strangle", "win_rate": "55-65%", "best_iv": "High (>50)"},
            {"name": "0DTE Gamma Blast", "type": "0dte_gamma", "win_rate": "30-40%", "best_iv": "High + catalyst"},
            {"name": "Earnings Straddle", "type": "earnings_straddle", "win_rate": "55-65%", "best_iv": "Pre-earnings IV expansion"},
            {"name": "Vertical Spread", "type": "vertical_spread", "win_rate": "65-80%", "best_iv": "Any"},
        ]
    }
