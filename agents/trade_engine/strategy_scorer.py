"""
Strategy Scorer.

Scores and ranks strategy candidates based on:
1. Edge (NVRP, IV vs HV, flow alignment)
2. Risk/Reward (Kelly, Sharpe, win rate * avg win)
3. Technical alignment (trend + support/resistance)
4. Time decay advantage (theta capture efficiency)
5. Liquidity (volume, OI, bid-ask spread)
"""
import math
from typing import Dict, Any, List, Optional, Tuple

from agents.trade_engine.models import StrategyType


class StrategyScorer:
    """
    Multi-dimensional scoring engine for options strategies.
    Each strategy is scored 0-100 across 5 dimensions, then weighted.
    """

    # Weight profiles by market regime
    WEIGHT_PROFILES = {
        "bullish": {"edge": 0.30, "risk_reward": 0.25, "technical": 0.20, "theta": 0.10, "liquidity": 0.15},
        "bearish": {"edge": 0.30, "risk_reward": 0.25, "technical": 0.20, "theta": 0.10, "liquidity": 0.15},
        "neutral": {"edge": 0.25, "risk_reward": 0.20, "technical": 0.10, "theta": 0.30, "liquidity": 0.15},
        "high_vol": {"edge": 0.35, "risk_reward": 0.20, "technical": 0.10, "theta": 0.25, "liquidity": 0.10},
        "low_vol": {"edge": 0.20, "risk_reward": 0.30, "technical": 0.25, "theta": 0.10, "liquidity": 0.15},
    }

    # Win rate baselines by strategy (historical)
    BASELINE_WIN_RATES = {
        StrategyType.CASH_SECURED_PUT: 0.75,
        StrategyType.BULL_PUT_CREDIT: 0.72,
        StrategyType.BEAR_CALL_CREDIT: 0.70,
        StrategyType.IRON_CONDOR: 0.68,
        StrategyType.COVERED_CALL: 0.80,
        StrategyType.VERTICAL_SPREAD: 0.70,
        StrategyType.WHEEL_CSP: 0.75,
        StrategyType.WHEEL_CC: 0.80,
        StrategyType.CALENDAR_SPREAD: 0.60,
        StrategyType.BUTTERFLY: 0.65,
        StrategyType.CALL_DEBIT_SPREAD: 0.48,
        StrategyType.PUT_DEBIT_SPREAD: 0.48,
        StrategyType.LEAPS: 0.50,
        StrategyType.LONG_CALL: 0.40,
        StrategyType.LONG_PUT: 0.40,
        StrategyType.STRADDLE: 0.55,
        StrategyType.STRANGLE: 0.55,
    }

    def score_strategy(
        self,
        strategy_type: StrategyType,
        market_data: Dict[str, Any],
        option_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        flow_data: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Score a strategy across all 5 dimensions.
        Returns individual dimension scores + composite score.
        """
        flow_data = flow_data or {}

        weights = self.WEIGHT_PROFILES.get(
            self._detect_regime(market_data),
            self.WEIGHT_PROFILES["neutral"]
        )

        # Score each dimension
        edge_score = self._score_edge(strategy_type, market_data, option_data, flow_data)
        rr_score = self._score_risk_reward(strategy_type, option_data)
        tech_score = self._score_technical(strategy_type, technical_data)
        theta_score = self._score_theta_efficiency(strategy_type, option_data)
        liq_score = self._score_liquidity(option_data)

        composite = (
            edge_score * weights["edge"]
            + rr_score * weights["risk_reward"]
            + tech_score * weights["technical"]
            + theta_score * weights["theta"]
            + liq_score * weights["liquidity"]
        )

        baseline_wr = self.BASELINE_WIN_RATES.get(strategy_type, 0.50)
        adjusted_wr = baseline_wr * (composite / 70)  # Normalize around 70 = baseline

        return {
            "strategy": strategy_type.value,
            "composite_score": round(composite, 1),
            "edge_score": round(edge_score, 1),
            "risk_reward_score": round(rr_score, 1),
            "technical_score": round(tech_score, 1),
            "theta_score": round(theta_score, 1),
            "liquidity_score": round(liq_score, 1),
            "weights": weights,
            "baseline_win_rate": baseline_wr,
            "adjusted_win_rate": round(min(adjusted_wr, 0.95), 3),
        }

    def rank_strategies(
        self,
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Rank strategy candidates by composite score."""
        return sorted(
            candidates,
            key=lambda candidate: candidate.get("score", {}).get(
                "composite_score", candidate.get("composite_score", 0)
            ),
            reverse=True,
        )

    def _score_edge(
        self,
        strategy_type: StrategyType,
        market_data: Dict,
        option_data: Dict,
        flow_data: Dict,
    ) -> float:
        """Score 0-100: How much edge do we have?"""
        score = 50.0  # Baseline

        iv_rank = market_data.get("iv_rank", 50)
        iv = option_data.get("iv", 0.20)
        hv = market_data.get("hv_20", iv)
        nvrp = iv - hv if hv > 0 else 0

        # NVRP edge
        if strategy_type in [StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
                             StrategyType.BEAR_CALL_CREDIT, StrategyType.CASH_SECURED_PUT,
                             StrategyType.COVERED_CALL]:
            # Premium selling: positive NVRP = edge
            score += min(nvrp * 3, 25)
            # High IV rank = more premium = more edge
            score += min((iv_rank - 50) * 0.3, 15)
        else:
            # Premium buying: negative NVRP = edge
            score -= min(nvrp * 3, 25)
            score -= min((iv_rank - 50) * 0.3, 15)

        # Flow confirmation
        net_sentiment = flow_data.get("net_sentiment", 0)
        if strategy_type in [StrategyType.BULL_PUT_CREDIT, StrategyType.CALL_DEBIT_SPREAD,
                             StrategyType.LONG_CALL, StrategyType.WHEEL_CSP]:
            if net_sentiment > 0.3:
                score += 10
            elif net_sentiment < -0.3:
                score -= 10
        elif strategy_type in [StrategyType.BEAR_CALL_CREDIT, StrategyType.PUT_DEBIT_SPREAD,
                               StrategyType.LONG_PUT]:
            if net_sentiment < -0.3:
                score += 10
            elif net_sentiment > 0.3:
                score -= 10

        return max(0, min(100, score))

    def _score_risk_reward(self, strategy_type: StrategyType, option_data: Dict) -> float:
        """Score 0-100: Risk/reward profile."""
        score = 50.0

        max_profit = option_data.get("max_profit", 0)
        max_loss = option_data.get("max_loss", 1)
        credit = option_data.get("credit", 0) or option_data.get("net_credit", 0)

        if max_loss > 0:
            rr_ratio = max_profit / max_loss
            # Better R:R = higher score
            score += min(rr_ratio * 20, 30)

        # Kelly fraction
        win_rate = self.BASELINE_WIN_RATES.get(strategy_type, 0.50)
        avg_win = credit if credit > 0 else max_profit
        avg_loss = max_loss if max_loss > 0 else 1
        if avg_loss > 0:
            kelly = win_rate - ((1 - win_rate) * avg_win / avg_loss)
            score += min(kelly * 50, 20)

        return max(0, min(100, score))

    def _score_technical(self, strategy_type: StrategyType, technical_data: Dict) -> float:
        """Score 0-100: Alignment with technical indicators."""
        score = 50.0

        trend = technical_data.get("trend", "neutral")
        rsi = technical_data.get("rsi", 50)
        macd_signal = technical_data.get("macd_signal", "neutral")

        bullish_tech = (trend == "bullish" or macd_signal == "bullish")
        bearish_tech = (trend == "bearish" or macd_signal == "bearish")

        bullish_strats = {StrategyType.BULL_PUT_CREDIT, StrategyType.CALL_DEBIT_SPREAD,
                          StrategyType.LONG_CALL, StrategyType.WHEEL_CSP}
        bearish_strats = {StrategyType.BEAR_CALL_CREDIT, StrategyType.PUT_DEBIT_SPREAD,
                          StrategyType.LONG_PUT}

        if strategy_type in bullish_strats:
            if bullish_tech:
                score += 20
            if bearish_tech:
                score -= 15
            if rsi < 40:
                score += 10  # Oversold = good for bullish entries
        elif strategy_type in bearish_strats:
            if bearish_tech:
                score += 20
            if bullish_tech:
                score -= 15
            if rsi > 70:
                score += 10  # Overbought = good for bearish entries
        else:
            # Neutral strategies (iron condor, butterfly)
            if trend == "neutral":
                score += 15

        return max(0, min(100, score))

    def _score_theta_efficiency(self, strategy_type: StrategyType, option_data: Dict) -> float:
        """Score 0-100: Theta decay advantage."""
        score = 50.0

        dte = option_data.get("dte", 30)
        theta = abs(option_data.get("theta", 0))
        credit = option_data.get("credit", 0) or option_data.get("net_credit", 0)

        # Premium sellers benefit from theta
        premium_selling = strategy_type in {
            StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
            StrategyType.BEAR_CALL_CREDIT, StrategyType.CASH_SECURED_PUT,
            StrategyType.COVERED_CALL, StrategyType.WHEEL_CSP, StrategyType.WHEEL_CC
        }

        if premium_selling:
            # Higher theta relative to credit = better decay capture
            if credit > 0:
                theta_yield = (theta * dte) / (credit * 100) if credit > 0 else 0
                score += min(theta_yield * 30, 30)
            # Sweet spot: 30-45 DTE for maximum theta decay
            if 30 <= dte <= 45:
                score += 15
            elif 15 <= dte <= 30:
                score += 10
        else:
            # Premium buyers: lower theta is better (less decay)
            score -= min((45 - dte) * 0.5, 20) if dte < 45 else 0

        return max(0, min(100, score))

    def _score_liquidity(self, option_data: Dict) -> float:
        """Score 0-100: Liquidity quality."""
        score = 50.0

        volume = option_data.get("volume", 0)
        oi = option_data.get("open_interest", 0)
        bid = option_data.get("bid", 0)
        ask = option_data.get("ask", 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

        # Volume scoring
        if volume >= 1000:
            score += 20
        elif volume >= 500:
            score += 15
        elif volume >= 100:
            score += 10
        elif volume >= 10:
            score += 5

        # OI scoring
        if oi >= 5000:
            score += 15
        elif oi >= 1000:
            score += 10
        elif oi >= 100:
            score += 5

        # Bid-ask spread scoring (tighter = better)
        if mid > 0:
            spread_pct = (ask - bid) / mid * 100
            if spread_pct < 5:
                score += 15
            elif spread_pct < 10:
                score += 10
            elif spread_pct < 20:
                score += 5
            else:
                score -= 10

        return max(0, min(100, score))

    def _detect_regime(self, market_data: Dict) -> str:
        """Detect current market regime for weight selection."""
        vix = market_data.get("vix", 20)
        trend = market_data.get("trend", "neutral")
        iv_rank = market_data.get("iv_rank", 50)

        if vix > 30:
            return "high_vol"
        elif vix < 15:
            return "low_vol"
        elif trend == "bullish":
            return "bullish"
        elif trend == "bearish":
            return "bearish"
        return "neutral"
