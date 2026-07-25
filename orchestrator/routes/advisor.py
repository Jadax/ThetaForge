"""
Advisor API Routes.
The main API that takes account info and returns specific trade recommendations.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from agents.trade_engine.recommender import TradeRecommender
from agents.trade_engine.models import (
    AccountInfo, RiskTolerance, StrategyType
)
from agents.data_ingestion.free_data import FreeDataProvider
from agents.technical.indicators import TechnicalAnalyzer
from agents.flow_analysis.gex_engine import GEXEngine

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

provider = FreeDataProvider()
recommender = TradeRecommender()
tech_analyzer = TechnicalAnalyzer()
gex_engine = GEXEngine()


class AdvisoryRequest(BaseModel):
    capital: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Available buying power")
    risk_tolerance: str = Field("moderate", description="conservative/moderate/aggressive")
    watchlist: List[str] = Field(default_factory=list, description="Symbols to analyze")
    max_positions: int = Field(10, description="Maximum open positions")
    current_positions: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/recommend")
async def get_recommendations(request: AdvisoryRequest):
    """
    MAIN ENDPOINT: Capital In → Specific Trade Recommendations Out.
    
    Takes your account info and returns ranked trade recommendations
    with exact entry/exit rules, position sizing, and risk management.
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

    # Fetch data for watchlist
    market_data = {}
    option_chains = {}
    technical_data = {}
    volatility_data = {}

    for symbol in request.watchlist:
        try:
            # Get stock price
            info = provider.get_stock_info(symbol)
            if info:
                market_data[f"{symbol}_price"] = info.get("regularMarketPrice", 0)

            # Get option chain
            chain = provider.get_option_chain(symbol)
            if chain:
                option_chains[symbol] = chain

            # Get technicals
            hist = provider.get_historical(symbol, period="6mo")
            if hist is not None and len(hist) > 0:
                technical_data[symbol] = tech_analyzer.analyze(hist)

            # GEX data
            if chain and market_data.get(f"{symbol}_price"):
                gex = gex_engine.calculate_gex(chain, market_data[f"{symbol}_price"])
                market_data[f"{symbol}_gex"] = gex

        except Exception as e:
            continue

    # Get VIX for context
    vix_data = provider.get_vix()
    if vix_data:
        market_data["vix"] = vix_data.get("regularMarketPrice", 20)

    volatility_data = {
        "iv": 0.20,
        "hv_20": 0.18,
        "iv_rank": 50,
        "dte": 30,
    }

    # Generate recommendations
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
    """
    Compare ROI across all available options chains.
    This is the OptionsellerROI killer feature.
    """
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

            # CSP opportunities
            csp_results = roi_calc.scan_all_strikes_csp(chain, stock_price, 30)
            for r in csp_results:
                r["symbol"] = symbol
                r["strategy"] = "csp"

            # CC opportunities
            cc_results = roi_calc.scan_all_strikes_cc(chain, stock_price, 30)
            for r in cc_results:
                r["symbol"] = symbol
                r["strategy"] = "cc"

            all_opportunities.extend(csp_results[:5])
            all_opportunities.extend(cc_results[:5])

        except Exception:
            continue

    # Rank by annualized return
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
    """List all available strategies with descriptions and win rates."""
    return {
        "strategies": [
            {
                "name": "Cash-Secured Put",
                "type": "csp",
                "description": "Sell OTM put, collect premium, buy stock if assigned",
                "win_rate": "70-85%",
                "best_iv": "High IV rank (>50)",
                "max_loss": "Strike × 100 - Premium",
                "capital": "Strike × 100",
            },
            {
                "name": "Covered Call",
                "type": "cc",
                "description": "Own stock, sell OTM call for income",
                "win_rate": "75-90%",
                "best_iv": "Any IV environment",
                "max_loss": "Stock cost - Premium",
                "capital": "100 shares per contract",
            },
            {
                "name": "Bull Put Credit Spread",
                "type": "bull_put",
                "description": "Sell OTM put spread, collect credit",
                "win_rate": "65-80%",
                "best_iv": "High IV rank (>50)",
                "max_loss": "Width - Credit",
                "capital": "Width × 100 - Credit",
            },
            {
                "name": "Bear Call Credit Spread",
                "type": "bear_call",
                "description": "Sell OTM call spread, collect credit",
                "win_rate": "65-80%",
                "best_iv": "High IV rank (>50)",
                "max_loss": "Width - Credit",
                "capital": "Width × 100 - Credit",
            },
            {
                "name": "Iron Condor",
                "type": "iron_condor",
                "description": "Sell OTM put and call spreads, collect credit",
                "win_rate": "65-80%",
                "best_iv": "High IV rank (>50)",
                "max_loss": "Wing width - Credit",
                "capital": "Wing width × 100 - Credit",
            },
            {
                "name": "Call Debit Spread",
                "type": "call_debit",
                "description": "Buy lower strike call, sell higher strike call",
                "win_rate": "45-55%",
                "best_iv": "Low IV rank (<50)",
                "max_loss": "Debit paid",
                "capital": "Debit × 100",
            },
            {
                "name": "Put Debit Spread",
                "type": "put_debit",
                "description": "Buy higher strike put, sell lower strike put",
                "win_rate": "45-55%",
                "best_iv": "Low IV rank (<50)",
                "max_loss": "Debit paid",
                "capital": "Debit × 100",
            },
            {
                "name": "Calendar Spread",
                "type": "calendar",
                "description": "Sell near-term, buy same-strike far-term",
                "win_rate": "55-65%",
                "best_iv": "Low IV, steep term structure",
                "max_loss": "Debit paid",
                "capital": "Debit × 100",
            },
            {
                "name": "Butterfly Spread",
                "type": "butterfly",
                "description": "Buy 1 ITM, sell 2 ATM, buy 1 OTM",
                "win_rate": "60-75%",
                "best_iv": "Low IV, pin expected",
                "max_loss": "Debit paid",
                "capital": "Debit × 100",
            },
            {
                "name": "Long Call",
                "type": "long_call",
                "description": "Buy call for directional bet",
                "win_rate": "35-45%",
                "best_iv": "Low IV (<25th percentile)",
                "max_loss": "Premium paid",
                "capital": "Premium × 100",
            },
            {
                "name": "Long Put",
                "type": "long_put",
                "description": "Buy put for directional bet",
                "win_rate": "35-45%",
                "best_iv": "Low IV (<25th percentile)",
                "max_loss": "Premium paid",
                "capital": "Premium × 100",
            },
            {
                "name": "Straddle",
                "type": "straddle",
                "description": "Buy call + put same strike/expiry",
                "win_rate": "55-65%",
                "best_iv": "Low IV, big move expected",
                "max_loss": "Total premium",
                "capital": "Total premium × 100",
            },
            {
                "name": "Wheel",
                "type": "wheel",
                "description": "CSP → Assigned → Covered Call → Called away → repeat",
                "win_rate": "70-85%",
                "best_iv": "High IV rank",
                "max_loss": "Strike - Premium",
                "capital": "Strike × 100",
            },
        ]
    }
