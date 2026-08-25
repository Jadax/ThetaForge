"""
Advisor API Routes.
The main API that takes account info and returns specific trade recommendations.
Wired to the AI Brain for unified signal analysis.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union
import asyncio
import math
import statistics
from datetime import date

from orchestrator.security import (
    analysis_rate_limit,
    require_advisor_token,
    scan_rate_limit,
)
from orchestrator.decision_log import append as log_executor_decisions
from orchestrator.decision_log import recent as recent_executor_decisions

from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.ai_brain import AIBrain, TimeHorizon
from agents.trade_engine.watchlist import FavoritesStore
from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, StrategyType
)
from agents.data_ingestion.free_data import FreeDataProvider
from agents.general_trader.market_overview import MarketOverview
from agents.technical.indicators import TechnicalEngine as TechAnalyzer
from agents.flow_analysis.gex_engine import GEXEngine
from agents.trade_engine.alerts import AlertEngine, AlertPriority, AlertType
from agents.trade_engine.signal_tracker import SignalTracker
from agents.trade_engine.background_scanner import get_background_scanner, LIQUID_OPTIONS_UNIVERSE
from agents.trade_engine.trade_manager import (
    OpenPosition,
    evaluate_position,
    portfolio_plan,
)
from agents.trade_engine.portfolio_analytics import analyze_ledger
from agents.trade_engine.macro_calendar import macro_days_until
from agents.equity_trader.equity_scanner import get_background_equity_scanner
from agents.equity_trader.equity_recommender import EquityRecommender
from agents.equity_trader.equity_manager import evaluate_position as equity_evaluate_position
from agents.equity_trader.equity_signals import atr as equity_atr

# Every Advisor route reads or mutates one shared, single-user state set, so
# authentication is applied at the router rather than per endpoint.
router = APIRouter(
    prefix="/api/advisor",
    tags=["advisor"],
    dependencies=[Depends(require_advisor_token)],
)

provider = FreeDataProvider()
recommender = TradeRecommender()
tech_analyzer = TechAnalyzer()
gex_engine = GEXEngine()
brain = AIBrain()
watchlist_store = FavoritesStore()
market_overview = MarketOverview(provider)


async def _market_snapshot(symbol: str, supplied_price: float = 0) -> Dict[str, Any]:
    """Fetch and normalize the inputs consumed by ``AIBrain``.

    The data provider is asynchronous. Keeping that boundary here prevents
    coroutine objects and missing legacy method names from being treated as
    market data by the API routes.
    """
    tasks: Dict[str, Any] = {}
    tasks["price"] = None if supplied_price > 0 else provider.get_stock_price(symbol)
    tasks["option_chain"] = provider.get_option_chain(symbol)
    tasks["history"] = provider.get_historical_prices(symbol, period="1y")
    tasks["vix"] = provider.get_vix()
    tasks["pcr"] = provider.get_put_call_ratio()

    results = await asyncio.gather(
        *(v for v in tasks.values() if v is not None),
        return_exceptions=True,
    )
    result_iter = iter(results)
    fetched: Dict[str, Any] = {}
    for key in tasks:
        if tasks[key] is not None:
            fetched[key] = next(result_iter, None)
        else:
            fetched[key] = supplied_price if key == "price" else None

    stock_price = float(fetched["price"]) if isinstance(fetched.get("price"), (int, float)) else 0.0
    option_chain = fetched.get("option_chain") if isinstance(fetched.get("option_chain"), list) else []
    historical_frame = fetched.get("history")
    vix = fetched.get("vix")
    pcr = fetched.get("pcr")
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
                        macd_histogram = float(raw_technical.get("macd", {}).get("histogram", 0) or 0)
                        technical_data = {
                            **raw_technical,
                            "trend": "bullish" if "bullish" in trend else "bearish" if "bearish" in trend else "neutral",
                            # A non-positive MACD histogram is not automatically
                            # bearish. Preserving neutral readings prevents a
                            # systematic bias toward bearish credit spreads.
                            "macd_signal": "bullish" if macd_histogram > 0 else "bearish" if macd_histogram < 0 else "neutral",
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

    # A decision endpoint must never turn an unavailable market feed into a
    # plausible-looking neutral regime.  The Brain's direct-call defaults are
    # useful for unit tests, but live API requests fail closed instead.
    if not option_chain:
        raise HTTPException(status_code=502, detail=f"Unable to retrieve a live option chain for {symbol}")
    if not isinstance(vix, (int, float)) or not math.isfinite(float(vix)) or float(vix) <= 0:
        raise HTTPException(status_code=502, detail="Unable to retrieve a live VIX reading")

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

    # Desk analytics — IV skew, short interest, and the earnings IV-vs-history
    # read. Each degrades to None when the free data can't produce it.
    iv_skew = None
    if option_chain:
        try:
            from agents.volatility.desk_analytics import calculate_iv_skew
            iv_skew = calculate_iv_skew(option_chain)
        except Exception:
            iv_skew = None

    short_interest = None
    try:
        short_interest = await provider.get_short_interest(symbol)
    except Exception:
        short_interest = None

    earnings_move = None
    try:
        from agents.volatility.desk_analytics import (
            implied_earnings_move,
            historical_earnings_moves,
            earnings_move_edge,
        )
        implied = implied_earnings_move(option_chain, stock_price) if option_chain else None
        if implied and historical_frame is not None and not historical_frame.empty:
            earnings_dates = await provider.get_earnings_dates(symbol, limit=12)
            past_dates = [event for event in earnings_dates if event < date.today()]
            if past_dates:
                moves = historical_earnings_moves(historical_frame, past_dates)
                earnings_move = earnings_move_edge(implied, moves)
    except Exception:
        earnings_move = None

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
        "vix": float(vix),
        "gex_data": gex_data,
        "flow_data": flow_data,
        "pcr_data": {"current": float(pcr), "historical": []} if isinstance(pcr, (int, float)) and pcr > 0 else None,
        "iv_skew": iv_skew,
        "short_interest": short_interest,
        "earnings_move": earnings_move,
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
    bridge_symbols: List[str] = Field(default_factory=list, max_length=150)


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


class AlertGalleryCreateRequest(BaseModel):
    template_id: str = Field(..., min_length=1, max_length=40)
    symbol: str = Field(..., min_length=1, max_length=12)
    threshold: Optional[Union[float, str]] = None


class WebhookConfigRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)


# === AI Brain Endpoints ===

@router.post("/brain/analyze", dependencies=[Depends(analysis_rate_limit)])
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
    try:
        days_to_macro = macro_days_until()
    except Exception:
        days_to_macro = None
    output = brain.analyze(
        symbol=symbol,
        days_to_macro=days_to_macro,
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


@router.get("/alerts/gallery")
async def alert_gallery():
    """Curated alert templates the dashboard can instantiate in one click."""
    from agents.trade_engine.alerts import ALERT_GALLERY
    return {"templates": ALERT_GALLERY}


@router.post("/alerts/gallery")
async def create_alert_from_gallery(request: AlertGalleryCreateRequest):
    """Instantiate a gallery template into a saved alert rule."""
    from agents.trade_engine.alerts import AlertEngine, AlertPriority, rule_from_template
    try:
        spec = rule_from_template(request.template_id, request.symbol, request.threshold)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    rule = AlertEngine().add_rule(
        symbol=spec["symbol"],
        alert_type=AlertType(spec["alert_type"]),
        threshold=spec["threshold"],
        message=f"{spec['name']} ({spec['symbol']})",
        priority=AlertPriority(spec["priority"]),
        one_time=True,
    )
    return {"status": "created", "rule": rule.__dict__}


@router.get("/alerts/notify")
async def get_webhook():
    """Current alert webhook configuration."""
    return AlertEngine().get_webhook()


@router.post("/alerts/notify")
async def set_webhook(request: WebhookConfigRequest):
    """Route triggered alerts to a Discord/Slack-compatible webhook URL.

    Delivery is fire-and-forget from a background thread; a down webhook never
    blocks the scan. The URL is stored only in the local data dir.
    """
    return AlertEngine().set_webhook(request.url)


@router.delete("/alerts/notify")
async def clear_webhook():
    """Disable webhook delivery."""
    return AlertEngine().clear_webhook()


@router.get("/signals/performance")
async def signal_performance():
    """Return recorded prediction accuracy; it is informational, not execution advice."""
    return SignalTracker().get_performance_summary()


@router.post("/signals/outcomes")
async def record_signal_outcome(request: SignalOutcomeRequest):
    """Evaluate due predictions for a symbol using a supplied current price."""
    updated = SignalTracker().record_outcome(request.symbol, request.current_price)
    return {"symbol": request.symbol.upper(), "outcomes_recorded": updated}


@router.post("/brain/analyze-watchlist", dependencies=[Depends(scan_rate_limit)])
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


@router.post("/opportunities", dependencies=[Depends(scan_rate_limit)])
async def automatic_opportunities(request: OpportunityScanRequest):
    """Find the best current paper-trade candidates without user-picked symbols."""
    active_symbols = await provider.get_active_stock_universe(limit=180)
    bridge_symbols = [symbol.upper().strip() for symbol in request.bridge_symbols if symbol and symbol.isalpha()]
    universe = list(dict.fromkeys(LIQUID_OPTIONS_UNIVERSE + bridge_symbols + active_symbols))[:300]
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


class MarketsRequest(BaseModel):
    """General-trader market map: optional per-symbol reads on top of the
    indices / bonds / commodities / sectors overview."""
    symbols: List[str] = Field(default_factory=list, max_length=25)


@router.post("/markets", dependencies=[Depends(scan_rate_limit)])
async def get_markets(request: MarketsRequest):
    """Read-only cross-asset market map (stocks/ETFs/bonds), free data only.

    Returns the daily tape across indices, bond yields and bond ETFs,
    commodities, sector performance, a yield-curve shape read, and a coarse
    risk-on/risk-off tilt — plus a per-symbol technical read for any
    requested symbol. This is the general-trader counterpart to the
    options-specific scanner; it produces context, never orders.
    """
    overview = await market_overview.overview()
    symbols = {
        symbol.strip().upper()
        for symbol in request.symbols
        if symbol and symbol.strip()
    }
    symbol_reads = await market_overview.analyze_symbols(list(symbols)) if symbols else {}
    return {
        "overview": overview,
        "symbols": symbol_reads,
    }


@router.post("/dashboard", dependencies=[Depends(scan_rate_limit)])
async def get_dashboard(request: DashboardRequest):
    """
    One-call full portfolio dashboard.
    Returns: VIX, regime, watchlist rankings, top opportunities,
    portfolio risk summary, and time-horizon breakdowns.
    """
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


# === Position Management ===

class PositionInput(BaseModel):
    """An open short-premium spread as reported by the Bridge."""
    symbol: str = Field(..., min_length=1, max_length=10)
    strategy: str = Field("bull_put", min_length=1, max_length=32)
    short_strike: float = Field(..., gt=0)
    long_strike: float = Field(0, gt=0)
    expiry: Optional[str] = None
    credit_received: float = Field(0.0, ge=0)
    quantity: int = Field(1, ge=1, le=100)
    spot: Optional[float] = Field(None, gt=0)
    dte: Optional[int] = Field(None, ge=0, le=365)
    short_leg_value: Optional[float] = Field(None, ge=0)
    days_to_earnings: Optional[int] = Field(None, ge=0, le=365)
    days_to_macro: Optional[int] = Field(None, ge=0, le=90)
    capital_required: Optional[float] = Field(None, ge=0)


class ManagementRequest(BaseModel):
    positions: List[PositionInput] = Field(default_factory=list, max_length=25)
    capital: float = Field(100_000, gt=0)
    realized_pnl: float = 0.0
    starting_capital: Optional[float] = None
    weekly_capital_limit: Optional[float] = None
    weekly_capital_used: float = Field(0.0, ge=0)


class EquityRecommendRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12)
    capital: float = Field(10_000, gt=0)
    current_positions: List[str] = Field(default_factory=list, max_length=50)


class EquityPositionInput(BaseModel):
    """An open long equity position as reported by the Bridge ledger."""
    symbol: str = Field(..., min_length=1, max_length=12)
    entry_price: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)
    target_price: Optional[float] = Field(None, gt=0)
    highest_high: float = Field(..., gt=0)
    risk_per_share: float = Field(..., gt=0)
    shares: int = Field(1, ge=1, le=100000)
    opened_at: Optional[str] = None
    current_price: Optional[float] = Field(None, gt=0)
    days_to_earnings: Optional[int] = Field(None, ge=0, le=365)
    days_to_macro: Optional[int] = Field(None, ge=0, le=90)


class EquityManagementRequest(BaseModel):
    positions: List[EquityPositionInput] = Field(default_factory=list, max_length=25)
    capital: float = Field(10_000, gt=0)


class PortfolioAnalyticsRequest(BaseModel):
    """The Bridge's paper-order ledger, supplied by the caller.

    The Advisor and the Bridge run on different hosts (Render vs the Oracle
    VM), so the ledger records travel here in the request body -- the same
    pattern as /positions/management, which the VM auto-manager already uses.
    """
    ledger: List[Dict[str, Any]] = Field(default_factory=list, max_length=2000)
    capital: float = Field(100_000, gt=0)
    starting_capital: Optional[float] = None


class BacktestCreditSpreadRequest(BaseModel):
    events: List[Dict[str, Any]] = Field(default_factory=list, max_length=5000)


class BacktestStrategyRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12)
    right: str = Field("put", pattern="^(put|call)$")
    dte: int = Field(14, ge=1, le=60)
    otm_pct: float = Field(0.02, gt=0, lt=0.50)
    width_pct: float = Field(0.05, gt=0, lt=0.50)
    credit_fraction: float = Field(0.25, gt=0, lt=1.0)
    contracts: int = Field(1, ge=1, le=10)
    period: str = Field("2y", pattern="^([1-9][0-9]*[dmy]|2y|5y)$")


class PnLCalculatorRequest(BaseModel):
    legs: List[Dict[str, Any]] = Field(default_factory=list, min_length=1, max_length=8)
    spot: float = Field(..., gt=0)
    contracts: int = Field(1, ge=1, le=50)
    iv: Optional[float] = Field(None, gt=0, lt=5)
    dte: Optional[int] = Field(None, ge=1, le=730)
    target_prices: Optional[List[float]] = Field(None, max_length=50)


class SymbolRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12)


class EquityBacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12)
    period: str = Field("2y", pattern="^([1-9][0-9]*[dmy]|2y|5y)$")
    rsi_max: float = Field(70, gt=5, lt=95)
    sma_fast: int = Field(50, ge=10, le=100)
    sma_slow: int = Field(200, ge=50, le=400)
    momentum_days: int = Field(126, ge=20, le=400)
    max_holding_days: int = Field(90, ge=5, le=500)


class ChainRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=12)
    expiry: Optional[str] = Field(None, max_length=12)
    target_dte: int = Field(30, ge=1, le=400)


def _find_short_leg_mid(chain: List[Dict], position: PositionInput) -> Optional[float]:
    """Current mid of the short leg(s) from a fresh chain, if present.

    Iron condors carry two short legs; the worst-case (highest-value) wing
    drives the management decision, so the max mid is returned.
    """
    if not chain:
        return None
    is_call = "call" in position.strategy.lower()
    legs = [position.short_strike, position.long_strike] if "condor" in position.strategy.lower() else [position.short_strike]
    mids = []
    for strike in legs:
        for opt in chain:
            if float(opt.get("strike") or 0) != strike:
                continue
            if position.expiry and opt.get("expiry") != position.expiry:
                continue
            if not is_call and str(opt.get("option_type", "")).upper() not in ("PUT", "P"):
                continue
            if is_call and str(opt.get("option_type", "")).upper() not in ("CALL", "C"):
                continue
            bid = float(opt.get("bid") or 0)
            ask = float(opt.get("ask") or 0)
            if bid > 0 and ask > 0:
                mids.append((bid + ask) / 2)
    return max(mids) if mids else None


@router.post("/positions/management", dependencies=[Depends(scan_rate_limit)])
async def positions_management(request: ManagementRequest):
    """Evaluate open short-premium positions against the exit framework.

    Applies the trade-management rules — 50% of max credit take-profit,
    the 21-DTE gamma window, the 2x-credit loss stop, the pre-earnings exit,
    and the pre-macro exit (no short vega through a scheduled FOMC/CPI/NFP
    print) — plus the portfolio plan (position cap, per-symbol capital slice,
    trailing-drawdown breaker). Missing spots and short-leg values are
    refreshed from free data; anything still unknown is left null so the
    management engine fails open on enrichment, never on safety inputs.

    This endpoint only *recommends* management actions — order submission
    remains exclusively in the Bridge, so there is no second execution path.
    """
    # Days until the next scheduled macro print is market-wide, so it is
    # computed once for every position. None (schedule exhausted or calendar
    # failure) fails open: no macro exit is minted from missing data.
    try:
        days_to_macro = macro_days_until()
    except Exception:
        days_to_macro = None

    actions = []
    for pos in request.positions:
        spot = pos.spot
        short_leg_value = pos.short_leg_value
        if not spot or spot <= 0:
            try:
                fetched = await provider.get_stock_price(pos.symbol)
                spot = float(fetched) if isinstance(fetched, (int, float)) and fetched > 0 else None
            except Exception:
                spot = None
        if short_leg_value is None or short_leg_value < 0:
            try:
                chain = await provider.get_option_chain(pos.symbol) or []
                short_leg_value = _find_short_leg_mid(chain, pos)
            except Exception:
                short_leg_value = None
        days_to_earnings = pos.days_to_earnings
        if days_to_earnings is None:
            try:
                next_earnings = await provider.get_next_earnings_date(pos.symbol)
                if next_earnings is not None:
                    days_to_earnings = (next_earnings - date.today()).days
            except Exception:
                days_to_earnings = None

        open_position = OpenPosition(
            symbol=pos.symbol,
            strategy=pos.strategy,
            short_strike=pos.short_strike,
            long_strike=pos.long_strike,
            expiry=pos.expiry,
            credit_received=pos.credit_received,
            quantity=pos.quantity,
            spot=spot,
            dte=pos.dte,
            short_leg_value=short_leg_value,
        )
        result = evaluate_position(
            open_position,
            days_to_earnings=days_to_earnings,
            days_to_macro=days_to_macro,
        )
        result["spot"] = spot
        result["short_leg_value"] = short_leg_value
        result["days_to_earnings"] = days_to_earnings
        result["days_to_macro"] = days_to_macro
        actions.append(result)

    plan = portfolio_plan(
        [{"symbol": p.symbol, "capital_required": p.capital_required or 0} for p in request.positions],
        request.capital,
        realized_pnl=request.realized_pnl,
        starting_capital=request.starting_capital,
        weekly_capital_limit=request.weekly_capital_limit,
        weekly_capital_used=request.weekly_capital_used,
    )
    return {"actions": actions, "portfolio": plan}


@router.post("/portfolio/analytics", dependencies=[Depends(scan_rate_limit)])
async def portfolio_analytics(request: PortfolioAnalyticsRequest):
    """Portfolio analytics over the caller-supplied paper-order ledger.

    The Bridge ledger is the single source of truth for every paper trade;
    this folds closing records into their entries and reports realized P&L,
    drawdown, per-strategy/symbol/sector outcomes, monthly breakdowns, an
    equity curve, and open-risk concentration vs the trade manager's caps.
    Nothing is fetched or fabricated: absent data reads as zero/None.
    """
    try:
        return analyze_ledger(
            request.ledger,
            request.capital,
            starting_capital=request.starting_capital,
        )
    except Exception:
        logger.exception("Portfolio analytics failed")
        raise HTTPException(status_code=500, detail="portfolio analytics failed")


@router.post("/backtest/credit-spread", dependencies=[Depends(scan_rate_limit)])
async def backtest_credit_spread(request: BacktestCreditSpreadRequest):
    """Replay caller-supplied realized credit-spread events.

    Each event: {'expiry_price', 'short_strike', 'long_strike', 'credit',
    'right'='put', 'expiry_date'?}. Returns overall stats, a monthly
    breakdown, an equity curve, and per-event P&L. The caller owns the data
    quality; this never fabricates inputs.
    """
    from agents.trade_engine.historical_backtest import backtest_credit_spread_detailed
    return backtest_credit_spread_detailed(request.events)


@router.post("/backtest/strategy", dependencies=[Depends(scan_rate_limit)])
async def backtest_strategy(request: BacktestStrategyRequest):
    """Rolling-window proxy backtest of a short vertical over free daily closes.

    TradeStation/EasyLanguage-style: opens a short vertical OTM by
    ``otm_pct``, width ``width_pct`` of spot, each ``dte`` days apart, and
    realizes it at the later close. CREDIT IS MODELED (width *
    credit_fraction) because free data has no historical option mids — every
    result is labeled ``proxy: true`` and must never be read as a backtest of
    real fills.
    """
    from agents.trade_engine.historical_backtest import backtest_strategy_series
    try:
        hist = await provider.get_historical_prices(request.symbol.upper(), period=request.period)
        if hist is None or len(hist) < 30:
            raise HTTPException(status_code=422, detail="insufficient price history")
        closes = [float(value) for value in hist["Close"].tolist()]
        dates = [str(day)[:10] for day in hist.index]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Strategy backtest data fetch failed for %s", request.symbol)
        raise HTTPException(status_code=502, detail="price history unavailable")
    return backtest_strategy_series(
        closes,
        dates=dates,
        right=request.right,
        dte=request.dte,
        otm_pct=request.otm_pct,
        width_pct=request.width_pct,
        credit_fraction=request.credit_fraction,
        contracts=request.contracts,
    )


@router.post("/analytics/pnl-calculator", dependencies=[Depends(scan_rate_limit)])
async def pnl_calculator(request: PnLCalculatorRequest):
    """At-expiry P/L profile for a multi-leg structure (Market Chameleon/tastytrade pattern).

    legs: [{'action': 'SELL'|'BUY', 'option_type': 'call'|'put', 'strike': float,
    'entry_price': float}]. Returns max profit/loss, breakevens, risk/reward,
    probability-of-profit at expiry (when iv+dte supplied), and P/L curve
    points. Pure math; premium comes from the caller, never invented here.
    """
    from agents.trade_engine.pnl_calculator import calculate_pnl
    return calculate_pnl(
        request.legs,
        request.spot,
        contracts=request.contracts,
        iv=request.iv,
        dte=request.dte,
        target_prices=request.target_prices,
    )


@router.post("/analytics/gex-heatmap", dependencies=[Depends(scan_rate_limit)])
async def gex_heatmap(request: SymbolRequest):
    """Per-strike dealer GEX heatmap (Flowasis pattern) from the free chain."""
    symbol = request.symbol.upper()
    try:
        price = await provider.get_stock_price(symbol)
        if not price or price <= 0:
            raise HTTPException(status_code=422, detail="price unavailable")
        chain = await provider.get_option_chain(symbol) or []
        if not chain:
            raise HTTPException(status_code=422, detail="option chain unavailable")
        gex_data = GEXEngine(underlying_price=price).calculate_chain_gex(chain, price)
        if "error" in gex_data:
            raise HTTPException(status_code=422, detail=gex_data["error"])
        return gex_data
    except HTTPException:
        raise
    except Exception:
        logger.exception("GEX heatmap failed for %s", symbol)
        raise HTTPException(status_code=502, detail="GEX heatmap unavailable")


@router.post("/analytics/chain", dependencies=[Depends(scan_rate_limit)])
async def chain_explorer(request: ChainRequest):
    """Desk-style option chain table (Market Chameleon / thinkorswim pattern).

    One row per strike with call and put sides side-by-side (bid/ask/mid, IV,
    open interest, volume, greeks), plus the expiry's desk readings: ATM IV,
    the ATM straddle's expected move, max pain, put/call ratios, and IV skew.
    Free-chain data only; NVRP and IV rank are enriched from history when the
    provider and local store have them, and fail open on any miss.
    """
    from agents.trade_engine.chain_explorer import build_chain_explorer
    from agents.volatility.iv_metrics import realized_volatility
    from agents.volatility.iv_history import IVHistoryStore
    symbol = request.symbol.upper()
    try:
        price = await provider.get_stock_price(symbol)
        if not price or price <= 0:
            raise HTTPException(status_code=422, detail="price unavailable")
        chain = await provider.get_option_chain(symbol) or []
        if not chain:
            raise HTTPException(status_code=422, detail="option chain unavailable")
        result = build_chain_explorer(chain, price, expiry=request.expiry, target_dte=request.target_dte)
        if result.get("error"):
            raise HTTPException(status_code=422, detail=result["error"])

        summary = result["summary"]
        # NVRP (IV vs realized vol) — a missed history fetch must not sink the
        # chain, so this enrichment is best-effort and fail-open.
        try:
            hist = await provider.get_historical_prices(symbol, period="6mo")
            if hist is not None and len(hist) >= 21:
                closes = [float(value) for value in hist["Close"].tolist()]
                hv_20 = realized_volatility(closes, 20)
                if hv_20 and summary.get("atm_iv"):
                    nvrp = summary["atm_iv"] - hv_20
                    summary["hv_20"] = round(hv_20, 4)
                    summary["nvrp"] = round(nvrp, 4)
                    summary["nvrp_regime"] = (
                        "sell_premium" if nvrp > 0.02
                        else "neutral" if nvrp >= -0.02
                        else "buy_premium"
                    )
        except Exception:
            logger.debug("Chain NVRP enrichment unavailable for %s", symbol)
        # IV rank/percentile vs the local per-symbol history store (if any).
        try:
            store = IVHistoryStore()
            atm_iv = summary.get("atm_iv")
            if atm_iv:
                rank = store.iv_rank(symbol, atm_iv)
                pct = store.iv_percentile(symbol, atm_iv)
                if rank is not None:
                    summary["iv_rank"] = round(rank, 1)
                if pct is not None:
                    summary["iv_percentile"] = round(pct, 1)
        except Exception:
            logger.debug("Chain IV-rank enrichment unavailable for %s", symbol)
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("Chain explorer failed for %s", symbol)
        raise HTTPException(status_code=502, detail="chain explorer unavailable")


@router.post("/backtest/equity-momentum", dependencies=[Depends(scan_rate_limit)])
async def backtest_equity_momentum(request: EquityBacktestRequest):
    """Long-equity momentum backtest over free daily closes (TradeStation pattern).

    Enters when the equity brain's trend gates agree (price > SMA200, SMA50 >
    SMA200, RSI below the cap, positive 126-day momentum) and exits on trend
    failure or the holding-period stop. Simulated equity curve — no fills,
    commissions, or slippage; results carry ``proxy: true``.
    """
    from agents.equity_trader.equity_backtest import backtest_momentum
    try:
        hist = await provider.get_historical_prices(request.symbol.upper(), period=request.period)
        if hist is None or len(hist) < 300:
            raise HTTPException(status_code=422, detail="insufficient price history")
        closes = [float(value) for value in hist["Close"].tolist()]
        dates = [str(day)[:10] for day in hist.index]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Equity backtest data fetch failed for %s", request.symbol)
        raise HTTPException(status_code=502, detail="price history unavailable")
    return backtest_momentum(
        closes,
        dates=dates,
        rsi_max=request.rsi_max,
        sma_fast=request.sma_fast,
        sma_slow=request.sma_slow,
        momentum_days=request.momentum_days,
        max_holding_days=request.max_holding_days,
    )


@router.get("/playbooks")
async def list_playbooks():
    """Strategy playbook library (education, never an order path)."""
    from agents.trade_engine.playbooks import list_playbooks as _list
    return {"playbooks": _list()}


@router.get("/playbooks/{playbook_id}")
async def get_playbook(playbook_id: str):
    """One full strategy playbook."""
    from agents.trade_engine.playbooks import get_playbook as _get
    playbook = _get(playbook_id.lower())
    if playbook.get("error"):
        raise HTTPException(status_code=404, detail=playbook["error"])
    return playbook


# === Legacy Endpoints ===

@router.post("/recommend", dependencies=[Depends(scan_rate_limit)])
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
    flow_data: Dict[str, Dict[str, Any]] = {}
    volatility_data: Dict[str, Dict[str, float]] = {}

    for symbol in request.watchlist:
        try:
            snapshot = await _market_snapshot(symbol)
            market_data[f"{symbol}_price"] = snapshot["stock_price"]
            option_chains[symbol] = snapshot["option_chain"]
            technical_data[symbol] = snapshot.get("technical_data", {})
            # The snapshot already computed directional flow for this symbol;
            # dropping it here (an earlier version hardcoded {}) muted the
            # scorer's flow-confirmation term (±10 of edge) and systematically
            # under-scored every executor-requested symbol vs the scan that
            # produced its notification.
            if snapshot.get("flow_data"):
                flow_data[symbol] = snapshot["flow_data"]
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
        flow_data=flow_data,
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
                "expected_value": r.expected_value,
                "alpha": r.alpha,
                "theoretical_edge_pct": r.theoretical_edge_pct,
                "model_value": r.model_value,
                "expected_move_pct": r.expected_move_pct,
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


@router.post("/compare", dependencies=[Depends(scan_rate_limit)])
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


# ── Background Scanner & Notification Endpoints ──────────────────────────


@router.get("/notifications")
async def get_notifications(unacknowledged_only: bool = True, limit: int = 50):
    """Get trade notifications from the background Brain scanner."""
    scanner = await get_background_scanner()
    notifs = await scanner.get_notifications(
        unacknowledged_only=unacknowledged_only, limit=limit
    )
    return {"notifications": notifs, "count": len(notifs)}


@router.post("/notifications/{notification_id}/acknowledge")
async def acknowledge_notification(notification_id: str):
    """Mark a single notification as acknowledged."""
    scanner = await get_background_scanner()
    ok = await scanner.acknowledge_notification(notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "acknowledged"}


@router.post("/notifications/acknowledge-all")
async def acknowledge_all_notifications():
    """Acknowledge all pending notifications."""
    scanner = await get_background_scanner()
    await scanner.acknowledge_all()
    return {"status": "all_acknowledged"}


@router.get("/scanner/status")
async def scanner_status():
    """Get background scanner status (last run, next run, pending count)."""
    scanner = await get_background_scanner()
    return await scanner.get_status()


@router.post("/scanner/trigger", dependencies=[Depends(scan_rate_limit)])
async def trigger_scan():
    """Manually trigger an immediate background Brain scan."""
    scanner = await get_background_scanner()
    new = await scanner.scan_once()
    return {"status": "scan_completed", "new_notifications": new}


# ── Equity (stock/ETF) Engine Endpoints ──────────────────────────────────


def _equity_recommendation_payload(recommendation, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data or {}
    return {
        "id": recommendation.id,
        "symbol": recommendation.symbol,
        "strategy": recommendation.strategy,
        "is_etf": recommendation.is_etf,
        "price": recommendation.price,
        "shares": recommendation.shares,
        "entry_price": recommendation.entry_limit,
        "entry_limit": recommendation.entry_limit,
        "stop_price": recommendation.stop_price,
        "target_price": recommendation.target_price if recommendation.target_price else None,
        "risk_per_share": recommendation.risk_per_share,
        "max_loss_total": recommendation.max_loss_total,
        "notional": recommendation.notional,
        "max_loss_pct": recommendation.max_loss_pct,
        "reasoning": recommendation.reasoning,
        "rationale": recommendation.reasoning,
        "gate": recommendation.gate,
        "gate_reason": recommendation.reasoning if recommendation.gate else None,
        "score": round(float(data.get("score") or 0), 1),
        "read": data.get("signal"),
        "trend": "above_200d" if data.get("above_200d") else "below_200d",
        "rsi_14": data.get("rsi_14"),
        "adx_14": data.get("adx"),
        "atr_14": data.get("atr_value"),
        "timestamp": data.get("as_of") or date.today().isoformat(),
    }


@router.post("/equity/recommend", dependencies=[Depends(scan_rate_limit)])
async def equity_recommend(request: EquityRecommendRequest):
    """Recommend a sized, gated long for one stock/ETF.

    Runs the same gated EquityBrain path the background scanner uses, then
    sizes the position (1% account risk at a 2x ATR stop) and applies the
    sector correlation cap against the positions already held. The returned
    trade is only a recommendation — order submission stays in the Bridge,
    which independently re-verifies live quotes and the weekly capital ledger.
    """
    scanner = await get_background_equity_scanner()
    data, skip = await scanner._analyze_one(request.symbol)
    if skip or data is None:
        return {
            "recommendations": [],
            "recommendation": None,
            "reason": skip or "data_unavailable",
        }
    recommendation = EquityRecommender().build(
        data,
        capital=request.capital,
        current_positions=request.current_positions,
    )
    payload = _equity_recommendation_payload(recommendation, data)
    return {
        "recommendations": [payload] if recommendation.gate is None else [],
        "recommendation": payload,
        "reason": recommendation.gate or "ok",
    }


@router.get("/equity/notifications")
async def get_equity_notifications(unacknowledged_only: bool = True, limit: int = 50):
    """Get equity trade notifications from the background equity scanner."""
    scanner = await get_background_equity_scanner()
    notifs = await scanner.get_notifications(
        unacknowledged_only=unacknowledged_only, limit=limit
    )
    return {"notifications": notifs, "count": len(notifs)}


@router.post("/equity/notifications/{notification_id}/acknowledge")
async def acknowledge_equity_notification(notification_id: str):
    """Mark a single equity notification as acknowledged."""
    scanner = await get_background_equity_scanner()
    ok = await scanner.acknowledge_notification(notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "acknowledged"}


@router.get("/equity/scanner/status")
async def equity_scanner_status():
    """Get the equity background scanner's status."""
    scanner = await get_background_equity_scanner()
    return await scanner.get_status()


class ExecutorDecisionBatch(BaseModel):
    """One poll-cycle's worth of executor decision records (observability)."""
    decisions: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/executor/decisions")
async def post_executor_decisions(batch: ExecutorDecisionBatch):
    """Append the executor's per-notification decisions (placed / skipped /
    bridge-rejected, with reasons) to the persisted decision trail. Pure
    observability: nothing here gates or places orders."""
    stored = await log_executor_decisions(batch.decisions)
    return {"stored": stored}


@router.get("/executor/decisions")
async def get_executor_decisions(limit: int = 50):
    """Newest-first view of the executor's decision trail — answers *why*
    notifications did not become paper orders without VM log access."""
    return {"decisions": await recent_executor_decisions(limit)}


@router.post("/equity/positions/management", dependencies=[Depends(scan_rate_limit)])
async def equity_positions_management(request: EquityManagementRequest):
    """Evaluate open long equity positions against the exit framework.

    Rules (first match wins): ATR stop, chandelier trail once +1R in profit,
    2R target, time exit, pre-earnings and pre-macro exits. Current price and
    ATR are refreshed from free data; anything missing is left null so the
    engine fails open on enrichment, never on safety inputs.

    This endpoint only *recommends* management actions — order submission
    remains exclusively in the Bridge (POST /orders/close-stock).
    """
    try:
        days_to_macro = macro_days_until()
    except Exception:
        days_to_macro = None

    actions = []
    for pos in request.positions:
        current_price = pos.current_price
        if not current_price or current_price <= 0:
            try:
                fetched = await provider.get_stock_price(pos.symbol)
                current_price = float(fetched) if isinstance(fetched, (int, float)) and fetched > 0 else None
            except Exception:
                current_price = None

        atr_value = None
        if current_price:
            try:
                hist = await provider.get_historical_prices(pos.symbol, period="6mo")
                if hist is not None and len(hist) >= 15:
                    closes = hist["Close"].tolist() if hasattr(hist, "tolist") else list(hist["Close"])
                    highs = hist["High"].tolist() if "High" in hist.columns else closes
                    lows = hist["Low"].tolist() if "Low" in hist.columns else closes
                    atr_value = equity_atr(highs, lows, closes)
            except Exception:
                atr_value = None

        days_to_earnings = pos.days_to_earnings
        if days_to_earnings is None:
            try:
                next_earnings = await provider.get_next_earnings_date(pos.symbol)
                days_to_earnings = (next_earnings - date.today()).days if next_earnings else None
            except Exception:
                days_to_earnings = None

        days_held = 0
        if pos.opened_at:
            try:
                opened = date.fromisoformat(str(pos.opened_at)[:10])
                days_held = max((date.today() - opened).days, 0)
            except ValueError:
                days_held = 0

        action, reason, updated_high = equity_evaluate_position(
            symbol=pos.symbol,
            current_price=current_price if current_price else 0.0,
            entry_price=pos.entry_price,
            stop_price=pos.stop_price,
            target_price=pos.target_price or 0.0,
            highest_high=pos.highest_high,
            atr=atr_value,
            risk_per_share=pos.risk_per_share,
            days_held=days_held,
            days_to_earnings=days_to_earnings,
            days_to_macro=days_to_macro,
        )
        actions.append({
            "symbol": pos.symbol,
            "action": action,
            "reason": reason,
            "current_price": current_price,
            "highest_high": round(updated_high, 2),
            "atr": round(atr_value, 4) if atr_value else None,
            "days_held": days_held,
            "days_to_earnings": days_to_earnings,
            "days_to_macro": days_to_macro,
            "shares": pos.shares,
        })

    return {"actions": actions}
