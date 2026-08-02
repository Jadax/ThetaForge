"""
Trade Recommender - The Core Engine.
Capital In → Specific Trades Out.

Stolen from: TastyTrade (mechanical rules), ORATS (NVRP filtering),
OptionStrat (strategy visualization), Barchart (signal confirmation),
OptionsellerROI (ROI comparison), Thinkorswim (probability analysis).

This is the brain that:
1. Takes account info (capital, risk tolerance, positions)
2. Scans market data (IV, HV, flow, technicals)
3. Scores all 13 strategies across all strikes/expirations
4. Sizes using Kelly Criterion
5. Validates against risk limits
6. Outputs specific trade recommendations with exact entry/exit rules
"""
import uuid
import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

from agents.trade_engine.models import (
    AccountInfo, OptionContract, StrategyLeg,
    TradeRecommendation, AdvisoryOutput,
    StrategyType, RiskTolerance, MarketRegime
)
from agents.trade_engine.roi_calculator import ROICalculator
from agents.trade_engine.analytics import OptionsAnalytics
from agents.trade_engine.strategy_scorer import StrategyScorer
from agents.risk_management.kelly_calculator import calculate_kelly
from agents.risk_management.portfolio_limits import RiskManager


# Risk parameters (non-negotiable)
MAX_RISK_PCT = {
    RiskTolerance.CONSERVATIVE: 0.01,
    RiskTolerance.MODERATE: 0.02,
    RiskTolerance.AGGRESSIVE: 0.03,
}
MAX_PORTFOLIO_DELTA = 20
MAX_PORTFOLIO_VEGA = 5.0
MAX_CORRELATED_POSITIONS = 3
# A candidate must demonstrate strong agreement across the strategy scorer's
# inputs before it is eligible for the dashboard. Ranking alone is never enough.
MIN_COMPOSITE_SCORE = 75.0
MIN_EDGE_SCORE = 60.0
MIN_PROBABILITY_OF_PROFIT = 55.0
MIN_LIQUIDITY_VOLUME = 10
MIN_LIQUIDITY_OI = 100
# TastyTrade / ORATS professional volatility gates. These mirror the
# market-maker playbook: only sell premium when it is expensive (elevated IV
# Rank, IV above realized volatility) and the market is not in a crash regime
# (VIX spike). Only buy premium when it is cheap. The Brain's strategy
# selection uses stricter per-strategy IVR bands; these gates apply uniformly
# to every scored candidate so no structure slips through on rank alone.
MIN_IV_RANK_SELL = 30
MIN_IV_RANK_BUY = 25
MAX_VIX_SELL = 35
# An iron condor collecting less than a third of its wing width is a
# lottery-ticket structure with a huge max-loss tail — not a core book trade.
MIN_IRON_CONDOR_CREDIT_TO_WIDTH = 0.33


class TradeRecommender:
    """
    The complete trade recommendation engine.
    Takes capital in → produces specific trades out.
    """

    def __init__(self):
        self.roi_calc = ROICalculator()
        self.analytics = OptionsAnalytics()
        self.scorer = StrategyScorer()
        self.risk_manager = RiskManager()

    def generate_recommendations(
        self,
        account: AccountInfo,
        market_data: Dict[str, Any],
        option_chains: Dict[str, List[Dict[str, Any]]],
        technical_data: Dict[str, Any],
        flow_data: Dict[str, Any] = None,
        volatility_data: Dict[str, Any] = None,
        diversify_underlyings: bool = True,
    ) -> AdvisoryOutput:
        """
        MAIN ENTRY POINT: Capital In → Trade Recommendations Out.
        
        Args:
            account: User's IBKR account info
            market_data: Current market data (VIX, IV rank, prices)
            option_chains: Option chains keyed by symbol
            technical_data: Technical indicators
            flow_data: Unusual activity, dark pool, sentiment
            volatility_data: IV, HV, term structure
        
        Returns:
            AdvisoryOutput with ranked recommendations
        """
        flow_data = flow_data or {}
        volatility_data = volatility_data or {}
        warnings = []

        # Step 1: Determine market regime
        regime = self._detect_market_regime(market_data, volatility_data)

        # Step 2: Calculate available capital
        risk_per_trade = self._calculate_risk_per_trade(account)
        max_capital_per_trade = risk_per_trade * 5  # Max 5x risk as capital

        # Step 3: Analyze existing positions
        portfolio_greeks = self._analyze_portfolio_greeks(account.current_positions)

        # Step 4: Generate candidates for each symbol
        all_candidates = []
        for symbol, chain in option_chains.items():
            if not chain:
                continue
            stock_price = market_data.get(f"{symbol}_price", 0)
            if stock_price <= 0:
                continue

            symbol_volatility = volatility_data.get(symbol, volatility_data)
            shares_owned = sum(
                max(float(position.get("position", 0)), 0)
                for position in account.current_positions
                if position.get("symbol", "").upper() == symbol.upper()
            )
            candidates = self._scan_symbol(
                symbol=symbol,
                stock_price=stock_price,
                chain=chain,
                regime=regime,
                market_data=market_data,
                technical_data=technical_data.get(symbol, {}),
                flow_data=flow_data.get(symbol, {}),
                volatility_data=symbol_volatility,
                risk_per_trade=risk_per_trade,
                max_capital=max_capital_per_trade,
                shares_owned=shares_owned,
            )
            all_candidates.extend(candidates)

        # Step 5: Rank all candidates
        ranked = self.scorer.rank_strategies(all_candidates)

        # Step 6: Select top recommendations (respecting portfolio limits)
        selected = self._select_recommendations(
            ranked=ranked,
            account=account,
            portfolio_greeks=portfolio_greeks,
            risk_per_trade=risk_per_trade,
            diversify_underlyings=diversify_underlyings,
        )

        # Step 7: Convert to TradeRecommendation objects
        recommendations = []
        total_deployed = 0
        for cand in selected:
            rec = self._build_recommendation(cand, regime, market_data, volatility_data)
            if rec:
                recommendations.append(rec)
                total_deployed += rec.capital_required

        # Step 8: Generate warnings
        warnings = self._generate_warnings(account, portfolio_greeks, recommendations)

        return AdvisoryOutput(
            account_summary=account,
            recommendations=recommendations,
            market_context={
                "regime": regime.value,
                "vix": market_data.get("vix", 0),
                "iv_rank": market_data.get("iv_rank", 50),
                "trend": technical_data.get("overall_trend", "neutral"),
            },
            portfolio_analysis={
                "current_positions": len(account.current_positions),
                "net_delta": portfolio_greeks.get("net_delta", 0),
                "net_vega": portfolio_greeks.get("net_vega", 0),
                "net_theta": portfolio_greeks.get("net_theta", 0),
                "portfolio_heat": portfolio_greeks.get("portfolio_heat", 0),
            },
            total_capital_deployed=total_deployed,
            remaining_buying_power=account.buying_power - total_deployed,
            warnings=warnings,
        )

    def _scan_symbol(
        self,
        symbol: str,
        stock_price: float,
        chain: List[Dict],
        regime: MarketRegime,
        market_data: Dict,
        technical_data: Dict,
        flow_data: Dict,
        volatility_data: Dict,
        risk_per_trade: float,
        max_capital: float,
        shares_owned: float = 0,
    ) -> List[Dict]:
        """Scan a single symbol for strategy opportunities."""
        candidates = []

        # Calculate analytics
        max_pain = self.analytics.max_pain(chain)
        exp_move = self.analytics.expected_move(
            stock_price,
            volatility_data.get("iv", 0.20),
            volatility_data.get("dte", 30),
        )
        nvrp = self.analytics.net_volatility_risk_premium(
            volatility_data.get("iv", 0.20),
            volatility_data.get("hv_20", 0.18),
            volatility_data.get("hv_30"),
            volatility_data.get("hv_60"),
        )
        # Carry the real, symbol-specific context into every strategy score.
        # Previously each scorer received a fixed VIX of 20 and IV expressed
        # as an IV-rank, which distorted the strategy comparison.
        nvrp["iv_rank"] = volatility_data.get("iv_rank", 50)
        nvrp["vix"] = market_data.get("vix", 20)
        nvrp["trend"] = technical_data.get("trend", "neutral")

        # Group options by expiry
        expiries = {}
        for opt in chain:
            exp = opt.get("expiry", "")
            if exp not in expiries:
                expiries[exp] = []
            expiries[exp].append(opt)

        # For each expiry, try all strategies
        for expiry, opts in expiries.items():
            dte = opts[0].get("dte", 30) if opts else 30
            # Avoid stale leaps and near-expiry contracts for the standard
            # defined-risk playbook. Their actual DTE comes from the provider,
            # never a placeholder.
            if dte < 14 or dte > 60:
                continue
            calls = [o for o in opts if o.get("option_type", "").upper() == "CALL"]
            puts = [o for o in opts if o.get("option_type", "").upper() == "PUT"]

            # Find ATM strike
            atm_call = min(calls, key=lambda x: abs(x.get("strike", 0) - stock_price), default=None)
            atm_put = min(puts, key=lambda x: abs(x.get("strike", 0) - stock_price), default=None)

            # Strategy 1: Cash-Secured Puts (credit selling)
            for put in puts:
                cand = self._score_csp(
                    symbol, stock_price, put, dte, regime, technical_data,
                    flow_data, nvrp, risk_per_trade, max_capital
                )
                if cand:
                    candidates.append(cand)

            # Strategy 2: Covered Calls are only valid against existing shares.
            if shares_owned >= 100:
                for call in calls:
                    cand = self._score_cc(
                        symbol, stock_price, call, dte, regime, technical_data,
                        flow_data, nvrp, risk_per_trade, max_capital
                    )
                    if cand:
                        candidates.append(cand)

            # Strategy 3: Bull Put Credit Spreads
            for i, put in enumerate(puts):
                for lower_put in puts[i+1:]:
                    cand = self._score_bull_put(
                        symbol, stock_price, put, lower_put, dte, regime,
                        technical_data, flow_data, nvrp, risk_per_trade, max_capital
                    )
                    if cand:
                        candidates.append(cand)

            # Strategy 4: Bear Call Credit Spreads
            for i, call in enumerate(calls):
                for higher_call in calls[i+1:]:
                    cand = self._score_bear_call(
                        symbol, stock_price, call, higher_call, dte, regime,
                        technical_data, flow_data, nvrp, risk_per_trade, max_capital
                    )
                    if cand:
                        candidates.append(cand)

            # Strategy 5: Iron Condors
            if len(puts) >= 2 and len(calls) >= 2:
                cand = self._score_iron_condor(
                    symbol, stock_price, puts, calls, dte, regime,
                    technical_data, flow_data, nvrp, risk_per_trade, max_capital
                )
                if cand:
                    candidates.append(cand)

            # Strategy 6: Call Debit Spreads
            for i, call in enumerate(calls):
                for higher_call in calls[i+1:]:
                    cand = self._score_call_debit(
                        symbol, stock_price, call, higher_call, dte, regime,
                        technical_data, flow_data, nvrp, risk_per_trade, max_capital
                    )
                    if cand:
                        candidates.append(cand)

            # Strategy 7: Put Debit Spreads
            for i, put in enumerate(puts):
                for lower_put in puts[i+1:]:
                    cand = self._score_put_debit(
                        symbol, stock_price, put, lower_put, dte, regime,
                        technical_data, flow_data, nvrp, risk_per_trade, max_capital
                    )
                    if cand:
                        candidates.append(cand)

        return candidates

    @staticmethod
    def _strategy_market_context(nvrp: Dict[str, Any], tech: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize the actual per-symbol volatility and technical context."""
        return {
            "iv_rank": nvrp.get("iv_rank", 50),
            "vix": nvrp.get("vix", 20),
            "trend": tech.get("trend", nvrp.get("trend", "neutral")),
            "hv_20": nvrp.get("hv_20", nvrp.get("iv", 0.20)),
        }

    @staticmethod
    def _executable_credit(short_leg: Dict[str, Any], long_leg: Dict[str, Any]) -> Optional[float]:
        """Return conservative spread credit: sell bid less buy ask.

        Last-trade prices are deliberately excluded because they may be stale
        and cannot be assumed to be obtainable when opening a spread.
        """
        try:
            short_bid = float(short_leg.get("bid", 0) or 0)
            long_ask = float(long_leg.get("ask", 0) or 0)
        except (TypeError, ValueError):
            return None
        if short_bid <= 0 or long_ask <= 0:
            return None
        return short_bid - long_ask

    @classmethod
    def _passes_quality_gate(
        cls,
        score: Dict[str, Any],
        roi: Dict[str, Any],
        strategy: Optional[str] = None,
        nvrp: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Reject mediocre candidates even when they rank highest in a scan."""
        if not (
            score.get("composite_score", 0) >= MIN_COMPOSITE_SCORE
            and score.get("edge_score", 0) >= MIN_EDGE_SCORE
            and roi.get("probability_of_profit", 0) >= MIN_PROBABILITY_OF_PROFIT
        ):
            return False
        return cls._passes_volatility_gate(strategy, nvrp or {})

    @staticmethod
    def _passes_volatility_gate(
        strategy: Optional[str], nvrp: Dict[str, Any]
    ) -> bool:
        """TastyTrade-style volatility regime gates for a scored strategy.

        Selling premium is only authorized when IV Rank is elevated, VIX is not
        spiking, and IV exceeds realized volatility (positive NVRP). Buying
        premium is only authorized when IV Rank is depressed. Missing
        volatility context defaults are permissive so the gate never invents a
        rejection, but the professional thresholds still apply to real data.
        """
        if strategy in ("csp", "cc", "bull_put", "bear_call", "iron_condor"):
            if float(nvrp.get("vix", 0) or 0) > MAX_VIX_SELL:
                return False
            if float(nvrp.get("iv_rank", 30) or 0) < MIN_IV_RANK_SELL:
                return False
            iv = nvrp.get("iv")
            hv_20 = nvrp.get("hv_20")
            if iv is not None and hv_20 is not None and float(iv) <= float(hv_20):
                return False
        elif strategy in ("call_debit", "put_debit"):
            if float(nvrp.get("iv_rank", 30) or 0) > MIN_IV_RANK_BUY:
                return False
        return True

    def _score_csp(
        self, symbol, stock_price, put, dte, regime, tech, flow, nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Cash-Secured Put opportunity."""
        strike = put.get("strike", 0)
        # A sell order can only be valued at the available bid. Last trade can
        # be stale and is not an executable quote.
        premium = put.get("bid", 0)
        if premium <= 0 or strike <= 0 or strike >= stock_price:
            return None

        # Liquidity check
        if put.get("volume", 0) < MIN_LIQUIDITY_VOLUME and put.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        roi = self.roi_calc.csp_roi(strike, premium, dte, stock_price, nvrp.get("iv", 0.20))
        capital_needed = strike * 100

        if capital_needed > max_cap:
            return None

        # Score
        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte, "volume": put.get("volume", 0),
            "open_interest": put.get("open_interest", 0), "bid": put.get("bid", 0),
            "ask": put.get("ask", 0), "credit": premium, "max_profit": premium * 100,
            "max_loss": capital_needed - premium * 100,
        }
        score = self.scorer.score_strategy(StrategyType.CASH_SECURED_PUT, market_ctx, opt_ctx, tech, flow)

        if not self._passes_quality_gate(score, roi, "csp", nvrp):
            return None

        return {
            "type": "csp",
            "symbol": symbol,
            "stock_price": stock_price,
            "strike": strike,
            "expiry": put.get("expiry", ""),
            "dte": dte,
            "premium": premium,
            "capital_required": capital_needed,
            "roi": roi,
            "score": score,
            "nvrp": nvrp,
            "option_data": put,
            "legs": [{**put, "action": "SELL"}],
        }

    def _score_cc(
        self, symbol, stock_price, call, dte, regime, tech, flow, nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Covered Call opportunity."""
        strike = call.get("strike", 0)
        premium = call.get("bid", 0)
        if premium <= 0 or strike <= stock_price:
            return None

        if call.get("volume", 0) < MIN_LIQUIDITY_VOLUME and call.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        roi = self.roi_calc.covered_call_roi(strike, premium, dte, stock_price, iv=nvrp.get("iv", 0.20))
        capital_needed = stock_price * 100

        if capital_needed > max_cap:
            return None

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte, "volume": call.get("volume", 0),
            "open_interest": call.get("open_interest", 0), "bid": call.get("bid", 0),
            "ask": call.get("ask", 0), "credit": premium, "max_profit": (strike - stock_price + premium) * 100,
            "max_loss": (stock_price - premium) * 100,
        }
        score = self.scorer.score_strategy(StrategyType.COVERED_CALL, market_ctx, opt_ctx, tech, flow)

        if not self._passes_quality_gate(score, roi, "cc", nvrp):
            return None

        return {
            "type": "cc",
            "symbol": symbol,
            "stock_price": stock_price,
            "strike": strike,
            "expiry": call.get("expiry", ""),
            "dte": dte,
            "premium": premium,
            "capital_required": capital_needed,
            "roi": roi,
            "score": score,
            "nvrp": nvrp,
            "option_data": call,
            "legs": [{**call, "action": "SELL"}],
        }

    def _score_bull_put(
        self, symbol, stock_price, short_put, long_put, dte, regime, tech, flow,
        nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Bull Put Credit Spread."""
        short_strike = short_put.get("strike", 0)
        long_strike = long_put.get("strike", 0)
        credit = self._executable_credit(short_put, long_put)

        if short_strike <= long_strike or short_strike >= stock_price:
            return None

        if credit is None or credit <= 0:
            return None

        # The short leg is the executed side — it must be liquid.
        if short_put.get("volume", 0) < MIN_LIQUIDITY_VOLUME and short_put.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        width = short_strike - long_strike
        capital_needed = (width - credit) * 100

        if capital_needed > max_cap or capital_needed <= 0:
            return None

        roi = self.roi_calc.credit_spread_roi(short_strike, long_strike, credit, dte, stock_price, "put", nvrp.get("iv", 0.20))

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte,
            "volume": short_put.get("volume", 0), "open_interest": short_put.get("open_interest", 0),
            "bid": short_put.get("bid", 0), "ask": short_put.get("ask", 0),
            "credit": credit, "max_profit": credit * 100, "max_loss": (width - credit) * 100,
        }
        score = self.scorer.score_strategy(StrategyType.BULL_PUT_CREDIT, market_ctx, opt_ctx, tech, flow)

        if not self._passes_quality_gate(score, roi, "bull_put", nvrp):
            return None

        return {
            "type": "bull_put",
            "symbol": symbol,
            "stock_price": stock_price,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "expiry": short_put.get("expiry", ""),
            "dte": dte,
            "credit": credit,
            "width": width,
            "capital_required": capital_needed,
            "roi": roi,
            "score": score,
            "nvrp": nvrp,
            "legs": [{**short_put, "action": "SELL"}, {**long_put, "action": "BUY"}],
        }

    def _score_bear_call(
        self, symbol, stock_price, short_call, long_call, dte, regime, tech, flow,
        nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Bear Call Credit Spread."""
        short_strike = short_call.get("strike", 0)
        long_strike = long_call.get("strike", 0)
        credit = self._executable_credit(short_call, long_call)

        if short_strike >= long_strike or short_strike <= stock_price:
            return None

        if credit is None or credit <= 0:
            return None

        # The short leg is the executed side — it must be liquid.
        if short_call.get("volume", 0) < MIN_LIQUIDITY_VOLUME and short_call.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        width = long_strike - short_strike
        capital_needed = (width - credit) * 100

        if capital_needed > max_cap or capital_needed <= 0:
            return None

        roi = self.roi_calc.credit_spread_roi(short_strike, long_strike, credit, dte, stock_price, "call", nvrp.get("iv", 0.20))

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte,
            "volume": short_call.get("volume", 0), "open_interest": short_call.get("open_interest", 0),
            "bid": short_call.get("bid", 0), "ask": short_call.get("ask", 0),
            "credit": credit, "max_profit": credit * 100, "max_loss": (width - credit) * 100,
        }
        score = self.scorer.score_strategy(StrategyType.BEAR_CALL_CREDIT, market_ctx, opt_ctx, tech, flow)

        if not self._passes_quality_gate(score, roi, "bear_call", nvrp):
            return None

        return {
            "type": "bear_call",
            "symbol": symbol,
            "stock_price": stock_price,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "expiry": short_call.get("expiry", ""),
            "dte": dte,
            "credit": credit,
            "width": width,
            "capital_required": capital_needed,
            "roi": roi,
            "score": score,
            "nvrp": nvrp,
            "legs": [{**short_call, "action": "SELL"}, {**long_call, "action": "BUY"}],
        }

    def _score_iron_condor(
        self, symbol, stock_price, puts, calls, dte, regime, tech, flow,
        nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score an Iron Condor."""
        if len(puts) < 2 or len(calls) < 2:
            return None

        # Find ATM for both sides
        atm_strike = min(puts, key=lambda x: abs(x.get("strike", 0) - stock_price), default=None)
        if not atm_strike:
            return None

        atm_s = atm_strike.get("strike", 0)

        # Select strikes: 1 std OTM each side
        em = self.analytics.expected_move(stock_price, nvrp.get("iv", 0.20), dte)
        expected_move = em.get("expected_move_1sd", stock_price * 0.05)

        # Put side: short ~1 SD below, long ~1.5 SD below
        put_short = max([p for p in puts if p.get("strike", 0) < atm_s],
                        key=lambda x: x.get("strike", 0), default=None)
        put_long = max([p for p in puts if p.get("strike", 0) < (put_short.get("strike", 0) if put_short else 0)],
                        key=lambda x: x.get("strike", 0), default=None)

        # Call side: short ~1 SD above, long ~1.5 SD above
        call_short = min([c for c in calls if c.get("strike", 0) > atm_s],
                         key=lambda x: x.get("strike", 0), default=None)
        call_long = min([c for c in calls if c.get("strike", 0) > (call_short.get("strike", 0) if call_short else 999999)],
                         key=lambda x: x.get("strike", 0), default=None)

        if not all([put_short, put_long, call_short, call_long]):
            return None

        # Both executed short wings must be liquid.
        if put_short.get("volume", 0) < MIN_LIQUIDITY_VOLUME and put_short.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None
        if call_short.get("volume", 0) < MIN_LIQUIDITY_VOLUME and call_short.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        # Calculate credit
        put_credit = self._executable_credit(put_short, put_long)
        call_credit = self._executable_credit(call_short, call_long)
        if put_credit is None or call_credit is None:
            return None
        total_credit = put_credit + call_credit

        if total_credit <= 0:
            return None

        put_width = put_short.get("strike", 0) - put_long.get("strike", 0)
        call_width = call_long.get("strike", 0) - call_short.get("strike", 0)
        wing_width = max(put_width, call_width)

        # Thin-credit condors are low-probability lottery tickets: the credit
        # must represent a real share of the defined risk.
        if wing_width <= 0 or total_credit / wing_width < MIN_IRON_CONDOR_CREDIT_TO_WIDTH:
            return None

        max_loss = (wing_width - total_credit) * 100
        capital_needed = max_loss

        if capital_needed > max_cap or capital_needed <= 0:
            return None

        roi = self.roi_calc.iron_condor_roi(
            put_short.get("strike", 0), put_long.get("strike", 0),
            call_short.get("strike", 0), call_long.get("strike", 0),
            total_credit, dte, stock_price, nvrp.get("iv", 0.20)
        )

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte,
            "volume": put_short.get("volume", 0), "open_interest": put_short.get("open_interest", 0),
            "bid": put_short.get("bid", 0), "ask": put_short.get("ask", 0),
            "credit": total_credit, "max_profit": total_credit * 100, "max_loss": max_loss,
        }
        score = self.scorer.score_strategy(StrategyType.IRON_CONDOR, market_ctx, opt_ctx, tech, flow)

        if not self._passes_quality_gate(score, roi, "iron_condor", nvrp):
            return None

        return {
            "type": "iron_condor",
            "symbol": symbol,
            "stock_price": stock_price,
            "put_short": put_short.get("strike", 0),
            "put_long": put_long.get("strike", 0),
            "call_short": call_short.get("strike", 0),
            "call_long": call_long.get("strike", 0),
            "expiry": put_short.get("expiry", ""),
            "dte": dte,
            "credit": total_credit,
            "wing_width": wing_width,
            "capital_required": capital_needed,
            "roi": roi,
            "score": score,
            "nvrp": nvrp,
            "legs": [
                {**put_long, "action": "BUY"}, {**put_short, "action": "SELL"},
                {**call_short, "action": "SELL"}, {**call_long, "action": "BUY"},
            ],
        }

    def _score_call_debit(
        self, symbol, stock_price, long_call, short_call, dte, regime, tech, flow,
        nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Call Debit Spread (Bull Call Spread)."""
        long_strike = long_call.get("strike", 0)
        short_strike = short_call.get("strike", 0)

        if long_strike >= short_strike or long_strike < stock_price * 0.97:
            return None

        # The short leg is the executed side — it must be liquid.
        if short_call.get("volume", 0) < MIN_LIQUIDITY_VOLUME and short_call.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        long_prem = long_call.get("ask", 0)
        short_prem = short_call.get("bid", 0)
        debit = long_prem - short_prem

        if debit <= 0:
            return None

        width = short_strike - long_strike
        max_profit = (width - debit) * 100
        capital_needed = debit * 100

        if capital_needed > max_cap:
            return None

        rr = max_profit / (capital_needed) if capital_needed > 0 else 0

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte,
            "volume": long_call.get("volume", 0), "open_interest": long_call.get("open_interest", 0),
            "bid": long_call.get("bid", 0), "ask": long_call.get("ask", 0),
            "credit": 0, "max_profit": max_profit, "max_loss": capital_needed,
        }
        score = self.scorer.score_strategy(StrategyType.CALL_DEBIT_SPREAD, market_ctx, opt_ctx, tech, flow)

        roi = {
            "max_profit": max_profit, "max_loss": capital_needed,
            "probability_of_profit": self.roi_calc._approx_pop_otm(stock_price, long_strike, dte, "call", nvrp.get("iv", 0.20)),
        }
        if not self._passes_quality_gate(score, roi, "call_debit", nvrp):
            return None

        return {
            "type": "call_debit",
            "symbol": symbol,
            "stock_price": stock_price,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "expiry": long_call.get("expiry", ""),
            "dte": dte,
            "debit": debit,
            "width": width,
            "capital_required": capital_needed,
            "max_profit": max_profit,
            "risk_reward": rr,
            "score": score,
            "roi": roi,
            "nvrp": nvrp,
            "legs": [{**long_call, "action": "BUY"}, {**short_call, "action": "SELL"}],
        }

    def _score_put_debit(
        self, symbol, stock_price, long_put, short_put, dte, regime, tech, flow,
        nvrp, risk, max_cap
    ) -> Optional[Dict]:
        """Score a Put Debit Spread (Bear Put Spread)."""
        long_strike = long_put.get("strike", 0)
        short_strike = short_put.get("strike", 0)

        if long_strike <= short_strike or long_strike > stock_price * 1.03:
            return None

        # The short leg is the executed side — it must be liquid.
        if short_put.get("volume", 0) < MIN_LIQUIDITY_VOLUME and short_put.get("open_interest", 0) < MIN_LIQUIDITY_OI:
            return None

        long_prem = long_put.get("ask", 0)
        short_prem = short_put.get("bid", 0)
        debit = long_prem - short_prem

        if debit <= 0:
            return None

        width = long_strike - short_strike
        max_profit = (width - debit) * 100
        capital_needed = debit * 100

        if capital_needed > max_cap:
            return None

        rr = max_profit / (capital_needed) if capital_needed > 0 else 0

        market_ctx = self._strategy_market_context(nvrp, tech)
        opt_ctx = {
            "iv": nvrp.get("iv", 0.20), "dte": dte,
            "volume": long_put.get("volume", 0), "open_interest": long_put.get("open_interest", 0),
            "bid": long_put.get("bid", 0), "ask": long_put.get("ask", 0),
            "credit": 0, "max_profit": max_profit, "max_loss": capital_needed,
        }
        score = self.scorer.score_strategy(StrategyType.PUT_DEBIT_SPREAD, market_ctx, opt_ctx, tech, flow)

        roi = {
            "max_profit": max_profit, "max_loss": capital_needed,
            "probability_of_profit": self.roi_calc._approx_pop_otm(stock_price, long_strike, dte, "put", nvrp.get("iv", 0.20)),
        }
        if not self._passes_quality_gate(score, roi, "put_debit", nvrp):
            return None

        return {
            "type": "put_debit",
            "symbol": symbol,
            "stock_price": stock_price,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "expiry": long_put.get("expiry", ""),
            "dte": dte,
            "debit": debit,
            "width": width,
            "capital_required": capital_needed,
            "max_profit": max_profit,
            "risk_reward": rr,
            "score": score,
            "roi": roi,
            "nvrp": nvrp,
            "legs": [{**long_put, "action": "BUY"}, {**short_put, "action": "SELL"}],
        }

    def _detect_market_regime(self, market_data: Dict, volatility_data: Dict) -> MarketRegime:
        """Detect current market regime from data."""
        vix = market_data.get("vix", 20)
        trend = market_data.get("trend", "neutral")

        if vix > 30:
            return MarketRegime.HIGH_VOL
        elif vix < 15:
            return MarketRegime.LOW_VOL
        elif trend == "bullish":
            return MarketRegime.BULLISH
        elif trend == "bearish":
            return MarketRegime.BEARISH
        return MarketRegime.NEUTRAL

    def _calculate_risk_per_trade(self, account: AccountInfo) -> float:
        """Calculate max risk per trade based on tolerance."""
        max_risk_pct = MAX_RISK_PCT.get(account.risk_tolerance, 0.02)
        # RiskManager enforces its own hard ceiling (2% of equity by default)
        # regardless of the tolerance profile.
        return min(
            account.total_equity * max_risk_pct,
            account.total_equity * self.risk_manager.max_position_risk_pct / 100,
        )

    def _analyze_portfolio_greeks(self, positions: List[Dict]) -> Dict[str, float]:
        """Analyze current portfolio Greeks."""
        net_delta = sum(p.get("delta", 0) for p in positions)
        net_vega = sum(p.get("vega", 0) for p in positions)
        net_theta = sum(p.get("theta", 0) for p in positions)
        net_gamma = sum(p.get("gamma", 0) for p in positions)

        total_risk = sum(abs(p.get("max_loss", 0)) for p in positions)
        portfolio_heat = total_risk / max(sum(p.get("capital", 1) for p in positions), 1)

        return {
            "net_delta": net_delta,
            "net_vega": net_vega,
            "net_theta": net_theta,
            "net_gamma": net_gamma,
            "portfolio_heat": portfolio_heat,
        }

    def _select_recommendations(
        self,
        ranked: List[Dict],
        account: AccountInfo,
        portfolio_greeks: Dict,
        risk_per_trade: float,
        diversify_underlyings: bool = True,
    ) -> List[Dict]:
        """Select top recommendations respecting portfolio limits."""
        selected = []
        remaining_capital = account.buying_power
        remaining_risk = risk_per_trade * (account.max_positions - len(account.current_positions))
        current_delta = portfolio_greeks.get("net_delta", 0)
        current_vega = portfolio_greeks.get("net_vega", 0)
        selected_symbols = set()

        for cand in ranked:
            if len(selected) >= account.max_positions:
                break

            # The market-wide scanner is a stock-selection tool first. A
            # focused stock review can opt out, but each alternative still
            # passes the identical capital, score, liquidity, and Greeks
            # requirements below.
            if diversify_underlyings and cand.get("symbol", "").upper() in selected_symbols:
                continue

            capital_needed = cand.get("capital_required", 0)
            if capital_needed > remaining_capital:
                continue

            # The risk budget binds the maximum amount this position can LOSE,
            # not its capital outlay (which is reserved by buying power above).
            max_loss = (
                cand.get("max_loss")
                or cand.get("roi", {}).get("max_loss")
                or capital_needed
            )
            if max_loss > risk_per_trade:
                continue

            # Check Greeks limits. Position delta/vega come from the short
            # leg(s) when the provider supplied them; a candidate that carries
            # explicit impacts (e.g. from a caller) keeps those values.
            est_delta = cand.get("delta_impact")
            est_vega = cand.get("vega_impact")
            if est_delta is None:
                est_delta = sum(
                    -float(leg.get("delta", 0) or 0) * 100
                    for leg in cand.get("legs", [])
                    if leg.get("action") == "SELL"
                )
            if est_vega is None:
                est_vega = sum(
                    -float(leg.get("vega", 0) or 0) * 100
                    for leg in cand.get("legs", [])
                    if leg.get("action") == "SELL"
                )
            if abs(current_delta + est_delta) > MAX_PORTFOLIO_DELTA:
                continue
            if abs(current_vega + est_vega) > MAX_PORTFOLIO_VEGA:
                continue

            selected.append(cand)
            selected_symbols.add(cand.get("symbol", "").upper())
            remaining_capital -= capital_needed
            remaining_risk -= max_loss
            current_delta += est_delta
            current_vega += est_vega

        return selected

    def _build_recommendation(
        self,
        candidate: Dict,
        regime: MarketRegime,
        market_data: Dict,
        volatility_data: Dict,
    ) -> Optional[TradeRecommendation]:
        """Convert a scored candidate into a full TradeRecommendation."""
        rec_id = f"TF-{uuid.uuid4().hex[:8].upper()}"
        score_data = candidate.get("score", {})
        roi_data = candidate.get("roi", {})

        strategy_map = {
            "csp": StrategyType.CASH_SECURED_PUT,
            "cc": StrategyType.COVERED_CALL,
            "bull_put": StrategyType.BULL_PUT_CREDIT,
            "bear_call": StrategyType.BEAR_CALL_CREDIT,
            "iron_condor": StrategyType.IRON_CONDOR,
            "call_debit": StrategyType.CALL_DEBIT_SPREAD,
            "put_debit": StrategyType.PUT_DEBIT_SPREAD,
        }

        strategy_type = strategy_map.get(candidate.get("type"), StrategyType.CASH_SECURED_PUT)
        symbol = candidate.get("symbol", "")

        # Build legs
        legs = []
        raw_legs = candidate.get("legs", [])
        for raw in raw_legs:
            contract = OptionContract(
                symbol=raw.get("symbol", symbol),
                strike=raw.get("strike", 0),
                expiry=raw.get("expiry", ""),
                option_type=raw.get("option_type", "PUT"),
                bid=raw.get("bid", 0),
                ask=raw.get("ask", 0),
                mid=(raw.get("bid", 0) + raw.get("ask", 0)) / 2,
                last=raw.get("last", 0),
                volume=raw.get("volume", 0),
                open_interest=raw.get("open_interest", 0),
                iv=raw.get("iv", 0),
                delta=raw.get("delta", 0),
                dte=raw.get("dte", candidate.get("dte", 30)),
            )
            action = "SELL" if raw.get("action") == "SELL" else "BUY"
            role = raw.get("role", "")
            legs.append(StrategyLeg(contract=contract, action=action, quantity=1, role=role))

        # Build entry/exit rules
        entry_rules = self._generate_entry_rules(strategy_type, candidate)
        exit_rules = self._generate_exit_rules(strategy_type, candidate)

        # ROI calculations
        capital_required = candidate.get("capital_required", 0)
        max_profit = candidate.get("max_profit", roi_data.get("max_profit", 0))
        max_loss = candidate.get("max_loss", roi_data.get("max_loss", 0))
        annualized = roi_data.get("annualized_return_pct", 0)

        return TradeRecommendation(
            recommendation_id=rec_id,
            strategy_type=strategy_type,
            symbol=symbol,
            underlying_price=candidate.get("stock_price", 0),
            legs=legs,
            quantity=1,
            net_credit=candidate.get("credit", 0),
            net_debit=candidate.get("debit", 0),
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven=roi_data.get("breakeven", 0),
            risk_reward_ratio=roi_data.get("return_on_risk_pct", 0),
            probability_of_profit=roi_data.get("probability_of_profit", 0),
            expected_value=(max_profit * roi_data.get("probability_of_profit", 50) / 100) - (max_loss * (100 - roi_data.get("probability_of_profit", 50)) / 100),
            kelly_fraction=self._calculate_kelly(candidate, roi_data),
            capital_required=capital_required,
            capital_at_risk=max_loss,
            return_on_capital_pct=roi_data.get("premium_yield_pct", 0),
            annualized_return_pct=annualized,
            composite_score=score_data.get("composite_score", 0),
            confidence_score=score_data.get("edge_score", 0),
            iv_rank=volatility_data.get("iv_rank", 50),
            vix=market_data.get("vix", 0),
            market_regime=regime,
            reasoning=self._generate_reasoning(candidate, score_data, roi_data),
            risk_warning=self._generate_risk_warning(candidate),
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            data_sources=["IBKR", "yfinance", "CBOE"],
            layers_passed=candidate.get("layers_passed", ["scoring", "selection"]),
        )

    @staticmethod
    def _calculate_kelly(candidate: Dict, roi_data: Dict) -> float:
        """True half-Kelly fraction from POP and the win/loss payoff ratio.

        f = (p*b - q) / b, halved for conservatism, clamped to [0, 0.5].
        A structure whose max loss dwarfs its max profit legitimately sizes to
        zero — the win rate alone was never a valid position-size input.
        """
        prob_win = float(roi_data.get("probability_of_profit", 0) or 0) / 100.0
        max_profit = roi_data.get("max_profit")
        if max_profit is None:
            max_profit = (candidate.get("credit") or candidate.get("premium") or 0) * 100
        max_loss = float(roi_data.get("max_loss", 0) or 0)
        if max_loss <= 0:
            max_loss = max(float(candidate.get("capital_required", 0) or 0), 1e-6)
        win_loss_ratio = float(max_profit or 0) / max_loss
        try:
            return round(max(0.0, min(calculate_kelly(prob_win, win_loss_ratio), 0.5)), 4)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _generate_entry_rules(self, strategy_type: StrategyType, candidate: Dict) -> Dict:
        """Generate specific entry rules for the trade."""
        dte = candidate.get("dte", 30)
        return {
            "preferred_dte": f"{dte} DTE",
            "entry_time": "First 30 min after market open" if dte <= 7 else "Anytime",
            "limit_order": True,
            "fill_target": "Mid-price or better",
            "max_slippage": "5% of credit/debit",
        }

    def _generate_exit_rules(self, strategy_type: StrategyType, candidate: Dict) -> Dict:
        """Generate specific exit rules - stolen from TastyTrade mechanical rules."""
        dte = candidate.get("dte", 30)
        profit_target = candidate.get("credit", 0) * 0.50 if candidate.get("credit", 0) > 0 else 0

        rules = {
            "profit_target": f"Close at 50% of max profit (${profit_target:.2f} credit collected)" if profit_target > 0 else "N/A",
            "stop_loss": f"Close at 2x credit received" if candidate.get("credit", 0) > 0 else "Close at 2x debit paid",
            "time_exit": f"Close at {max(dte - 21, 1)} DTE remaining" if dte > 21 else f"Close at {max(dte - 7, 1)} DTE",
            "max_loss_exit": "Close immediately if max loss hit",
            "roll_rules": "Roll out in time if tested, never roll for a loss",
        }

        if strategy_type in [StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT, StrategyType.BEAR_CALL_CREDIT]:
            rules["adjustment"] = "If short strike tested, roll up/down the tested side"
        elif strategy_type in [StrategyType.CALL_DEBIT_SPREAD, StrategyType.PUT_DEBIT_SPREAD]:
            rules["trailing_stop"] = "Consider selling at 50-100% profit"

        return rules

    def _generate_reasoning(self, candidate: Dict, score: Dict, roi: Dict) -> str:
        """Generate human-readable reasoning for the recommendation."""
        parts = []
        nvrp = candidate.get("nvrp", {})
        regime = nvrp.get("regime", "neutral")

        if regime in ["strong_sell_vol", "sell_vol"]:
            parts.append("IV exceeds realized volatility (positive NVRP) → edge in selling premium")
        elif regime in ["strong_buy_vol", "buy_vol"]:
            parts.append("Realized volatility exceeds IV (negative NVRP) → edge in buying options")

        if score.get("technical_score", 0) > 70:
            parts.append("Technical indicators align with trade direction")

        if score.get("theta_score", 0) > 70:
            parts.append("Time decay working in our favor")

        if score.get("liquidity_score", 0) > 70:
            parts.append("Strong liquidity ensures clean fills")

        parts.append(f"Composite score: {score.get('composite_score', 0)}/100")
        parts.append(f"Win rate: {score.get('adjusted_win_rate', 0)*100:.1f}%")

        annual = roi.get("annualized_return_pct", 0)
        if annual > 50:
            parts.append(f"Excellent annualized return: {annual:.1f}%")

        return " | ".join(parts) if parts else "Standard trade setup"

    def _generate_risk_warning(self, candidate: Dict) -> str:
        """Generate risk warning."""
        warnings = []
        if candidate.get("type") == "iron_condor":
            warnings.append("Iron condors have undefined risk on both sides if market moves significantly")
        if candidate.get("dte", 30) < 7:
            warnings.append("Short DTE trade - higher gamma risk")
        if candidate.get("capital_required", 0) > 5000:
            warnings.append("Large capital requirement - ensure adequate margin")
        return " | ".join(warnings) if warnings else "Standard options risk applies"

    def _generate_warnings(
        self,
        account: AccountInfo,
        portfolio_greeks: Dict,
        recommendations: List[TradeRecommendation],
    ) -> List[str]:
        """Generate portfolio-level warnings."""
        warnings = []

        total_deployed = sum(r.capital_required for r in recommendations)
        if total_deployed > account.buying_power * 0.5:
            warnings.append(f"⚠ Would deploy {total_deployed:.0f} ({total_deployed/account.buying_power*100:.0f}%) of buying power")

        new_delta = sum(r.legs[0].contract.delta * (1 if r.legs[0].action == "BUY" else -1) for r in recommendations if r.legs)
        if abs(portfolio_greeks.get("net_delta", 0) + new_delta) > MAX_PORTFOLIO_DELTA:
            warnings.append(f"⚠ Net delta would approach limit ({MAX_PORTFOLIO_DELTA})")

        if len(account.current_positions) + len(recommendations) > account.max_positions:
            warnings.append(f"⚠ Would exceed max positions ({account.max_positions})")

        return warnings
