"""
Advisor API Routes.
The main API that takes account info and returns specific trade recommendations.
Wired to the AI Brain for unified signal analysis.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import asyncio
import math
import statistics

from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.ai_brain import AIBrain, TimeHorizon
from agents.trade_engine.watchlist import FavoritesStore
from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, StrategyType
)
from agents.data_ingestion.free_data import FreeDataProvider
from agents.technical.indicators import TechnicalEngine as TechAnalyzer
from agents.flow_analysis.gex_engine import GEXEngine
from agents.trade_engine.alerts import AlertEngine, AlertPriority, AlertType
from agents.trade_engine.signal_tracker import SignalTracker

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

provider = FreeDataProvider()
recommender = TradeRecommender()
tech_analyzer = TechAnalyzer()
gex_engine = GEXEngine()
brain = AIBrain()
watchlist_store = FavoritesStore()

# This is deliberately a broad, liquid options universe rather than every US
# listing. Free sources do not provide a reliable real-time option chain for
# every stock; scanning illiquid names creates misleading recommendations. The
# first pass covers the most actively traded ETFs and large-cap underlyings,
# then the Brain performs a full option-chain analysis on the strongest names.
LIQUID_OPTIONS_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLC", "XLU",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "AMD", "NFLX", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "CSCO",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "COIN", "HOOD",
    "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "AMGN", "TMO", "ISRG",
    "XOM", "CVX", "OXY", "SLB", "CAT", "DE", "GE", "BA", "LMT", "NKE", "COST", "WMT", "HD", "MCD", "SBUX", "DIS", "UBER", "PLTR", "SMCI",
]


async def _market_snapshot(symbol: str, supplied_price: float = 0) -> Dict[str, Any]:
    """Fetch and normalize the inputs consumed by ``AIBrain``.

    The data provider is asynchronous. Keeping that boundary here prevents
    coroutine objects and missing legacy method names from being treated as
    market data by the API routes.
    """
    price_task = None if supplied_price > 0 else provider.get_stock_price(symbol)
    option_chain_task = provider.get_option_chain(symbol)
    history_task = provider.get_historical_prices(symbol, period="1y")
    vix_task = provider.get_vix()
    pcr_task = provider.get_put_call_ratio()

    results = await asyncio.gather(
        *(task for task in (price_task, option_chain_task, history_task, vix_task, pcr_task) if task is not None),
        return_exceptions=True,
    )
    result_iter = iter(results)
    fetched_price = supplied_price if supplied_price > 0 else next(result_iter, None)
    option_chain = next(result_iter, [])
    historical_frame = next(result_iter, None)
    vix = next(result_iter, None)
    pcr = next(result_iter, None)

    stock_price = float(fetched_price) if isinstance(fetched_price, (int, float)) else 0.0
    option_chain = option_chain if isinstance(option_chain, list) else []
    historical: List[float] = []
    high_prices: List[float] = []
    low_prices: List[float] = []
    technical_data: Dict[str, Any] = {}
    hv_history: List[float] = []
    if historical_frame is not None and not isinstance(historical_frame, Exception) and not historical_frame.empty:
        try:
            clean_history = historical_frame.dropna(subset=["Close", "High", "Low"])
            historical = [float(value) for value in clean_history["Close"].tolist() if float(value) > 0]
            high_prices = [float(value) for value in clean_history["High"].tolist() if float(value) > 0]
            low_prices = [float(value) for value in clean_history["Low"].tolist() if float(value) > 0]
            if "Volume" in clean_history.columns:
                try:
                    raw_technical = tech_analyzer.calculate_all_indicators(clean_history)
                    if "error" not in raw_technical:
                        trend = str(raw_technical.get("trend", "NEUTRAL")).lower()
                        technical_data = {
                            **raw_technical,
                            "trend": "bullish" if "bullish" in trend else "bearish" if "bearish" in trend else "neutral",
                            "macd_signal": "bullish" if raw_technical.get("macd", {}).get("bullish") else "bearish",
                        }
                except (KeyError, TypeError, ValueError):
                    technical_data = {}
            for index in range(21, len(historical) + 1):
                window = historical[index - 21:index]
                log_returns = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
                if len(log_returns) >= 2:
                    hv_history.append(statistics.stdev(log_returns) * math.sqrt(252))
        except (KeyError, TypeError, ValueError):
            historical, high_prices, low_prices = [], [], []

    if not stock_price and historical:
        stock_price = historical[-1]
    if stock_price <= 0:
        raise HTTPException(status_code=502, detail=f"Unable to retrieve a valid price for {symbol}")

    if len(historical) >= 21:
        returns = [math.log(historical[i] / historical[i - 1]) for i in range(1, len(historical))]
        hv_20 = statistics.stdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 2 else 0.18
    else:
        hv_20 = 0.18

    implied_vols = [
        float(option.get("implied_volatility", 0))
        for option in option_chain
        if isinstance(option.get("implied_volatility", 0), (int, float)) and option.get("implied_volatility", 0) > 0
    ]
    current_iv = statistics.median(implied_vols) if implied_vols else max(hv_20, 0.20)
    iv_52w_high = max(hv_history + [current_iv]) if hv_history else current_iv
    iv_52w_low = min(hv_history + [current_iv]) if hv_history else current_iv
    flow_data = None
    if option_chain:
        unusual = brain.flow_detector.scan_chain(option_chain, stock_price, current_iv) if brain.flow_detector else []
        sweeps = brain.flow_detector.detect_sweep_orders(option_chain, stock_price) if brain.flow_detector else []
        dark_pool = brain.flow_detector.detect_dark_pool_prints(option_chain, stock_price) if brain.flow_detector else []
        flow_data = brain.flow_detector.aggregate_signals(unusual, sweeps, dark_pool) if brain.flow_detector else None

    gex_data = None
    if option_chain:
        gex = gex_engine.calculate_chain_gex(option_chain, stock_price)
        if "error" not in gex:
            gex_data = {"regime": gex.get("gex_regime", "NEUTRAL").lower(), **gex}

    return {
        "stock_price": stock_price,
        "option_chain": option_chain,
        "historical_prices": historical,
        "high_prices": high_prices or historical,
        "low_prices": low_prices or historical,
        "current_iv": current_iv,
        "hv_20": hv_20,
        # Historical IV is not available from the free provider. Historical
        # realized volatility provides a clearly labelled IV-rank proxy rather
        # than a permanently fabricated neutral value.
        "iv_52w_high": iv_52w_high,
        "iv_52w_low": iv_52w_low,
        "technical_data": technical_data,
        "vix": float(vix) if isinstance(vix, (int, float)) and vix > 0 else 20.0,
        "gex_data": gex_data,
        "flow_data": flow_data,
        "pcr_data": {"current": float(pcr), "historical": []} if isinstance(pcr, (int, float)) and pcr > 0 else None,
    }


# === Request/Response Models ===

class AdvisoryRequest(BaseModel):
    capital: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Available buying power")
    risk_tolerance: str = Field("moderate", description="conservative/moderate/aggressive")
    watchlist: List[str] = Field(default_factory=list, description="Symbols to analyze")
    max_positions: int = Field(10, description="Maximum open positions")
    diversify_underlyings: bool = Field(
        True,
        description="Keep the headline scan to one trade per underlying; false permits qualified alternatives for one requested stock",
    )
    current_positions: List[Dict[str, Any]] = Field(default_factory=list)


class BrainAnalysisRequest(BaseModel):
    symbol: str
    stock_price: float = 0
    horizon: str = Field("1m", description="1w/1m/3m/6m")


class OpportunityScanRequest(BaseModel):
    """Budget and existing exposure for the automatic liquid-universe scan."""
    capital: float = Field(gt=0, description="Weekly options allocation")
    current_positions: List[Dict[str, Any]] = Field(default_factory=list)


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


class AlertRuleRequest(BaseModel):
    symbol: str
    alert_type: AlertType
    threshold: float
    message: str = ""
    priority: AlertPriority = AlertPriority.MEDIUM
    one_time: bool = True


class AlertCheckRequest(BaseModel):
    market_data: Dict[str, Dict[str, Any]]


class SignalOutcomeRequest(BaseModel):
    symbol: str
    current_price: float = Field(gt=0)


# === AI Brain Endpoints ===

@router.post("/brain/analyze")
async def brain_analyze(request: BrainAnalysisRequest):
    """
    AI Brain analysis for a single symbol.
    Returns unified signals, regime, strategy recommendations,
    and time-horizon specific trade ideas.
    """
    symbol = request.symbol.upper()

    snapshot = await _market_snapshot(symbol, request.stock_price)

    # Run Brain
    brain_snapshot = {key: value for key, value in snapshot.items() if key != "technical_data"}
    output = brain.analyze(
        symbol=symbol,
        **brain_snapshot,
    )

    # Store a point-in-time prediction for later, explicit outcome evaluation.
    # The tracker is deliberately independent of the recommendation score: sparse
    # or unvalidated history must never alter a live decision automatically.
    tracker = SignalTracker()
    tracker.record_prediction(
        symbol=output.symbol,
        stock_price=output.stock_price,
        overall_signal=output.overall_signal.value,
        overall_score=output.overall_score,
        confidence=output.confidence,
        regime=output.regime,
        best_strategy=output.best_strategy,
        signals=output.all_signals,
    )
    performance_summary = tracker.get_performance_summary()

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
        "portfolio_warnings": output.portfolio_warnings,
        "signal_accuracy": performance_summary["by_source"],
        "dynamic_weights": performance_summary["dynamic_weights"],
    }


# === Alert and signal-performance endpoints ===

@router.get("/alerts")
async def list_alerts(symbol: Optional[str] = None):
    """List saved alert rules, optionally filtered to one symbol."""
    return {"rules": AlertEngine().list_rules(symbol)}


@router.post("/alerts")
async def create_alert(request: AlertRuleRequest):
    """Create a price, volatility, signal, or portfolio-risk alert rule."""
    rule = AlertEngine().add_rule(
        symbol=request.symbol,
        alert_type=request.alert_type,
        threshold=request.threshold,
        message=request.message,
        priority=request.priority,
        one_time=request.one_time,
    )
    return {"status": "created", "rule": rule.__dict__}


@router.delete("/alerts/{rule_id}")
async def delete_alert(rule_id: str):
    """Delete a saved alert rule."""
    if not AlertEngine().remove_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/alerts/check")
async def check_alerts(request: AlertCheckRequest):
    """Evaluate saved alert rules against caller-supplied market data."""
    return {"events": AlertEngine().check(request.market_data)}


@router.get("/alerts/history")
async def alert_history(symbol: Optional[str] = None, limit: int = 50):
    """Return the newest triggered alerts first by storage order."""
    return {"events": AlertEngine().get_history(symbol, max(1, min(limit, 200))) }


@router.get("/signals/performance")
async def signal_performance():
    """Return recorded prediction accuracy; it is informational, not execution advice."""
    return SignalTracker().get_performance_summary()


@router.post("/signals/outcomes")
async def record_signal_outcome(request: SignalOutcomeRequest):
    """Evaluate due predictions for a symbol using a supplied current price."""
    updated = SignalTracker().record_outcome(request.symbol, request.current_price)
    return {"symbol": request.symbol.upper(), "outcomes_recorded": updated}


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
            results.append(await brain_analyze(BrainAnalysisRequest(symbol=symbol)))
        except HTTPException:
            continue

    # Rank by overall_score descending
    results.sort(key=lambda x: x["overall_score"], reverse=True)

    return {
        "total_analyzed": len(results),
        "rankings": results,
    }


async def _screen_liquid_universe(symbols: List[str]) -> List[Dict[str, Any]]:
    """Rank the liquid universe before requesting expensive option chains.

    The first pass only uses three months of price/volume history. It is run
    concurrently with a bounded fan-out, then the full recommendation engine
    gets a small, evidence-based shortlist instead of hammering public option
    endpoints for every listing.
    """
    semaphore = asyncio.Semaphore(8)

    async def score_symbol(symbol: str) -> Optional[Dict[str, Any]]:
        async with semaphore:
            history = await provider.get_historical_prices(symbol, period="3mo")
        if history is None or history.empty or len(history) < 22:
            return None
        try:
            # Public feeds can append an in-progress session row with no
            # price yet; exclude only that incomplete row, not the symbol.
            history = history.dropna(subset=["Close"])
            if len(history) < 22:
                return None
            closes = [float(value) for value in history["Close"].tolist()]
            if not all(math.isfinite(value) for value in closes):
                return None
            volumes = []
            for value in history["Volume"].tolist():
                parsed = float(value)
                volumes.append(parsed if math.isfinite(parsed) else 0.0)
            last = closes[-1]
            change_5d = (last / closes[-6] - 1) * 100
            change_20d = (last / closes[-21] - 1) * 100
            average_volume = statistics.fmean(volumes[-21:-1])
            volume_ratio = volumes[-1] / average_volume if average_volume else 0
            if not all(math.isfinite(value) for value in (last, change_5d, change_20d, volume_ratio)):
                return None
            # Movement plus unusual participation surfaces candidates for the
            # deeper, options-specific Brain analysis. Direction is decided
            # there, not by this preliminary ranking.
            score = abs(change_20d) * 1.5 + abs(change_5d) + min(volume_ratio, 3) * 4
            return {
                "symbol": symbol,
                "screen_score": round(score, 2),
                "change_5d": round(change_5d, 2),
                "change_20d": round(change_20d, 2),
                "volume_ratio": round(volume_ratio, 2),
            }
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    screened = await asyncio.gather(*(score_symbol(symbol) for symbol in symbols))
    ranked = [item for item in screened if item]
    ranked.sort(key=lambda item: item["screen_score"], reverse=True)
    return ranked


@router.post("/opportunities")
async def automatic_opportunities(request: OpportunityScanRequest):
    """Find the best current paper-trade candidates without user-picked symbols."""
    active_symbols = await provider.get_active_stock_universe(limit=80)
    universe = list(dict.fromkeys(LIQUID_OPTIONS_UNIVERSE + active_symbols))[:120]
    screened = await _screen_liquid_universe(universe)
    shortlist = [item["symbol"] for item in screened[:10]]
    if not shortlist:
        raise HTTPException(status_code=502, detail="Market sources did not return enough data for the automatic scan")

    recommendations = await get_recommendations(AdvisoryRequest(
        capital=request.capital,
        buying_power=request.capital,
        risk_tolerance="moderate",
        watchlist=shortlist,
        current_positions=request.current_positions,
    ))
    recommendations["universe_size"] = len(universe)
    recommendations["active_discoveries"] = len(active_symbols)
    recommendations["screened_symbols"] = screened[:10]
    recommendations["shortlisted_symbols"] = shortlist
    return recommendations


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

    # VIX is fetched by the Brain snapshot; this request only uses it for the
    # dashboard-level regime label.
    try:
        vix_value = await provider.get_vix()
        vix = float(vix_value) if vix_value else 20.0
    except Exception:
        vix = 20.0

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
            rankings.append(await brain_analyze(BrainAnalysisRequest(symbol=symbol, horizon="1m")))
        except HTTPException:
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
    volatility_data: Dict[str, Dict[str, float]] = {}

    for symbol in request.watchlist:
        try:
            snapshot = await _market_snapshot(symbol)
            market_data[f"{symbol}_price"] = snapshot["stock_price"]
            option_chains[symbol] = snapshot["option_chain"]
            technical_data[symbol] = snapshot.get("technical_data", {})
            volatility_data[symbol] = {
                "iv": snapshot["current_iv"],
                "hv_20": snapshot["hv_20"],
                "iv_rank": max(0, min(100, (snapshot["current_iv"] - snapshot["iv_52w_low"]) /
                                  max(snapshot["iv_52w_high"] - snapshot["iv_52w_low"], 0.0001) * 100)),
                "dte": 30,
            }
            if snapshot["gex_data"]:
                market_data[f"{symbol}_gex"] = snapshot["gex_data"]
        except HTTPException:
            continue

    try:
        market_data["vix"] = float(await provider.get_vix() or 20)
    except Exception:
        market_data["vix"] = 20

    output = recommender.generate_recommendations(
        account=account,
        market_data=market_data,
        option_chains=option_chains,
        technical_data=technical_data,
        flow_data={},
        volatility_data=volatility_data,
        diversify_underlyings=request.diversify_underlyings,
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
                "capital_required": r.capital_required,
                "capital_at_risk": r.capital_at_risk,
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
            snapshot = await _market_snapshot(symbol)
            stock_price = snapshot["stock_price"]
            chain = snapshot["option_chain"]
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

        except HTTPException:
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

    snapshot = await _market_snapshot(symbol)
    stock_price = snapshot["stock_price"]
    chain = snapshot["option_chain"]

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
