"""
Trade Recommendation Engine - Scorer.

Composite scoring engine that evaluates strategy-symbol pairs and produces
StrategyScore objects with full quantitative metrics.

PIPELINE STAGE 2 of 6: SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND

SCORING COMPONENTS:
  1. IV Score       - Volatility regime edge (NVRP, IV Rank zones)
  2. Trend Score    - Minervini SEPA, technical indicators
  3. Flow Score     - Unusual activity, dark pool, put/call
  4. GEX Score      - Gamma exposure regime alignment
  5. Catalyst Score - Event positioning, IV ramp
  6. Risk/Reward    - Expected value, Kelly fraction
  7. Sentiment      - Social/news sentiment alignment

METHODOLOGY:
  Each component produces a 0-100 sub-score. The composite score is a
  weighted average, with weights varying by market regime and strategy type.

  Strategy-specific weights encode the research from:
    - TastyTrade: IV Rank is king for premium selling
    - Option Alpha: Trend + HV alignment for directional
    - Bierman: Premium yield weighting
"""
import math
from typing import List, Dict, Optional

from .models import (
    SymbolData, MarketConditions, StrategyScore,
    StrategyType, Direction, GEXRegime,
)
from .edge_calculator import EdgeCalculator


class SymbolScorer:
    """
    Composite scoring engine for strategy-symbol evaluation.

    Takes scanned symbols and produces detailed StrategyScore objects
    quantifying the edge for each viable strategy on each symbol.
    """

    def __init__(self, market: MarketConditions):
        self.market = market
        self.edge_calc = EdgeCalculator()

        # Base weights for composite score
        self.base_weights = {
            "iv": 0.20,
            "trend": 0.20,
            "flow": 0.10,
            "dark_pool": 0.05,
            "gex": 0.10,
            "catalyst": 0.05,
            "risk_reward": 0.15,
            "sentiment": 0.05,
            "minervini": 0.10,
        }

    # =================================================================
    # INDIVIDUAL SCORING COMPONENTS
    # =================================================================

    def _score_iv(self, data: SymbolData, strategy: StrategyType) -> float:
        """
        Score based on IV environment and strategy fit.

        High IV Rank favors: credit spreads, iron condors, strangles
        Low IV Rank favors: debit spreads, long straddles, LEAPS
        """
        iv_rank = data.iv_rank
        is_credit = strategy in [
            StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
            StrategyType.BEAR_CALL_CREDIT, StrategyType.COVERED_CALL,
            StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL,
            StrategyType.EARNINGS_SHORT_STRADDLE,
            StrategyType.EARNINGS_SHORT_STRANGLE,
            StrategyType.EARNINGS_IRON_CONDOR,
        ]

        if is_credit:
            # Credit strategies benefit from high IV
            if iv_rank >= 75:
                return 90.0
            elif iv_rank >= 60:
                return 75.0
            elif iv_rank >= 40:
                return 55.0
            elif iv_rank >= 20:
                return 35.0
            else:
                return 15.0
        else:
            # Debit strategies benefit from low IV (cheap options)
            if iv_rank <= 25:
                return 90.0
            elif iv_rank <= 40:
                return 75.0
            elif iv_rank <= 60:
                return 55.0
            elif iv_rank <= 80:
                return 35.0
            else:
                return 15.0

    def _score_trend(
        self, data: SymbolData, strategy: StrategyType
    ) -> float:
        """
        Score based on trend alignment with strategy direction.

        Trend-following: debit strategies, directional credit
        Range-bound: iron condors, strangles, neutral credit
        """
        score = 0.0

        # Determine if strategy is directional or neutral
        neutral_strategies = [
            StrategyType.IRON_CONDOR, StrategyType.IRON_BUTTERFLY,
            StrategyType.STRADDLE, StrategyType.STRANGLE,
            StrategyType.CALENDAR_SPREAD, StrategyType.BUTTERFLY,
        ]
        bullish_strategies = [
            StrategyType.BULL_PUT_CREDIT, StrategyType.BULL_CALL_DEBIT,
            StrategyType.LONG_CALL, StrategyType.COVERED_CALL,
            StrategyType.CASH_SECURED_PUT, StrategyType.PMCC,
            StrategyType.LEAPS,
        ]
        bearish_strategies = [
            StrategyType.BEAR_CALL_CREDIT, StrategyType.BEAR_PUT_DEBIT,
            StrategyType.LONG_PUT,
        ]

        is_neutral = strategy in neutral_strategies
        is_bullish = strategy in bullish_strategies
        is_bearish = strategy in bearish_strategies

        # Trend strength
        if data.trend == "STRONG_UPTREND":
            trend_score = 90.0 if is_bullish else (30.0 if is_neutral else 10.0)
        elif data.trend == "UPTREND":
            trend_score = 75.0 if is_bullish else (50.0 if is_neutral else 25.0)
        elif data.trend == "NEUTRAL":
            trend_score = 50.0 if is_neutral else 55.0
        elif data.trend == "DOWNTREND":
            trend_score = 75.0 if is_bearish else (50.0 if is_neutral else 25.0)
        elif data.trend == "STRONG_DOWNTREND":
            trend_score = 90.0 if is_bearish else (30.0 if is_neutral else 10.0)
        else:
            trend_score = 50.0

        # RSI adjustment
        if data.rsi_14 > 70:
            if is_bearish:
                trend_score += 10.0  # Overbought, bearish aligns
            elif is_bullish:
                trend_score -= 10.0
        elif data.rsi_14 < 30:
            if is_bullish:
                trend_score += 10.0  # Oversold, bullish aligns
            elif is_bearish:
                trend_score -= 10.0

        # MACD signal
        if data.macd_signal == "BULLISH" and is_bullish:
            trend_score += 5.0
        elif data.macd_signal == "BEARISH" and is_bearish:
            trend_score += 5.0

        return min(max(trend_score, 0.0), 100.0)

    def _score_flow(self, data: SymbolData) -> float:
        """Score based on options flow analysis."""
        score = 0.0

        # Volume ratio
        if data.volume_ratio >= 3.0:
            score += 35.0
        elif data.volume_ratio >= 2.0:
            score += 25.0
        elif data.volume_ratio >= 1.5:
            score += 15.0
        elif data.volume_ratio >= 1.0:
            score += 10.0

        # Flow score (composite from flow analysis agent)
        score += data.flow_score * 35.0

        # Dark pool
        if data.dark_pool_confirmed:
            score += 15.0

        return min(score, 100.0)

    def _score_dark_pool(self, data: SymbolData) -> float:
        """Score based on dark pool activity."""
        if data.dark_pool_confirmed:
            return 80.0 + min(data.flow_score * 20.0, 20.0)
        return 30.0 + min(data.flow_score * 20.0, 20.0)

    def _score_gex(
        self, data: SymbolData, strategy: StrategyType
    ) -> float:
        """
        Score based on GEX (Gamma Exposure) regime.

        HIGH_POSITIVE GEX: Price magnets to zero-GEX strikes.
                          Favor iron condors near zero-GEX strike.
        HIGH_NEGATIVE GEX: Price moves away from strikes.
                          Favor directional or wider strikes.
        """
        gex = data.gex_regime

        neutral_strategies = [
            StrategyType.IRON_CONDOR, StrategyType.IRON_BUTTERFLY,
            StrategyType.STRANGLE, StrategyType.CALENDAR_SPREAD,
        ]
        is_neutral = strategy in neutral_strategies

        if gex == GEXRegime.HIGH_POSITIVE:
            if is_neutral:
                return 85.0  # Positive GEX helps neutral strategies
            else:
                return 45.0  # Price pinned, less directional opportunity
        elif gex == GEXRegime.HIGH_NEGATIVE:
            if is_neutral:
                return 30.0  # Negative GEX makes neutral harder
            else:
                return 70.0  # Amplified moves help directional
        elif gex == GEXRegime.FLIP_ZONE:
            return 55.0  # Unpredictable, moderate score
        else:
            return 50.0  # Neutral GEX

    def _score_catalyst(
        self, data: SymbolData, strategy: StrategyType
    ) -> float:
        """
        Score based on catalyst positioning.

        For credit/premium selling: avoid being near earnings
        For debit/buying: IV ramp zone (14-30 days to earnings) is ideal
        """
        dte_to_earnings = data.days_to_earnings

        is_premium_seller = strategy in [
            StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
            StrategyType.BEAR_CALL_CREDIT, StrategyType.WHEEL,
            StrategyType.COVERED_CALL, StrategyType.CASH_SECURED_PUT,
        ]
        is_premium_buyer = strategy in [
            StrategyType.BULL_CALL_DEBIT, StrategyType.BEAR_PUT_DEBIT,
            StrategyType.LONG_CALL, StrategyType.LONG_PUT,
            StrategyType.LEAPS, StrategyType.PMCC,
        ]
        is_earnings_strategy = "EARNINGS" in strategy.value

        if is_earnings_strategy:
            if dte_to_earnings <= 7:
                return 80.0  # Right in the zone
            else:
                return 30.0

        if is_premium_seller:
            if dte_to_earnings <= 7:
                return 10.0  # Dangerous, binary event
            elif dte_to_earnings <= 14:
                return 30.0
            elif dte_to_earnings <= 30:
                return 70.0  # Sweet spot: IV elevated but not imminent
            else:
                return 60.0
        elif is_premium_buyer:
            if 14 <= dte_to_earnings <= 45:
                return 80.0  # IV ramp zone, cheap before event
            elif dte_to_earnings <= 7:
                return 50.0  # IV already high
            else:
                return 55.0

        return 50.0

    def _score_risk_reward(
        self, data: SymbolData, strategy: StrategyType
    ) -> float:
        """
        Score based on quantitative risk/reward metrics.

        Uses EdgeCalculator to compute:
          - Expected Value
          - Kelly Fraction
          - Risk/Reward Ratio
          - Probability of Profit
        """
        # Get strategy-specific metrics
        strategy_name = strategy.value
        trend_aligned = self._is_trend_aligned(data, strategy)

        metrics = EdgeCalculator.calculate_strategy_ev(
            strategy_name, data.iv_rank, self.market.vix, trend_aligned
        )

        score = 0.0

        # Expected Value
        ev = metrics["expected_value"]
        if ev > 0.15:
            score += 30.0
        elif ev > 0.08:
            score += 20.0
        elif ev > 0.03:
            score += 10.0
        elif ev > 0:
            score += 5.0

        # Kelly Fraction
        kelly = metrics["kelly_fraction"]
        if kelly > 0.15:
            score += 20.0
        elif kelly > 0.08:
            score += 15.0
        elif kelly > 0.03:
            score += 10.0
        elif kelly > 0:
            score += 5.0

        # Win Rate
        wr = metrics["win_rate"]
        if wr > 0.75:
            score += 25.0
        elif wr > 0.65:
            score += 15.0
        elif wr > 0.55:
            score += 10.0
        elif wr > 0.45:
            score += 5.0

        # Risk/Reward ratio
        rr = metrics["risk_reward_ratio"]
        if rr > 2.0:
            score += 15.0
        elif rr > 1.5:
            score += 10.0
        elif rr > 1.0:
            score += 5.0

        # Has positive EV bonus
        if metrics["has_positive_ev"]:
            score += 10.0

        return min(score, 100.0)

    def _score_sentiment(self, data: SymbolData) -> float:
        """Score based on sentiment analysis."""
        sentiment = data.sentiment_score
        # Abs value of sentiment (both bullish and bearish are useful signals)
        return 50.0 + abs(sentiment) * 50.0

    def _score_minervini(
        self, data: SymbolData, strategy: StrategyType
    ) -> float:
        """
        Score based on Minervini SEPA template compliance.

        For bullish strategies: strong SEPA template is highly bullish
        For neutral strategies: moderate SEPA is fine (trending is ok)
        For bearish strategies: weak SEPA is actually bearish
        """
        sepa_points = 0
        if data.above_150_sma:
            sepa_points += 1
        if data.above_200_sma:
            sepa_points += 1
        if data.sma150_above_sma200:
            sepa_points += 1
        if data.relative_strength_rank >= 70:
            sepa_points += 1

        bullish_strategies = [
            StrategyType.BULL_CALL_DEBIT, StrategyType.LONG_CALL,
            StrategyType.BULL_PUT_CREDIT, StrategyType.COVERED_CALL,
            StrategyType.CASH_SECURED_PUT, StrategyType.PMCC,
            StrategyType.LEAPS,
        ]
        is_bullish = strategy in bullish_strategies

        if is_bullish:
            # Full template: 4/4 = 95, 3/4 = 80, etc.
            return min(sepa_points * 24.0, 95.0)
        else:
            # Neutral or bearish: partial credit
            return min(sepa_points * 15.0 + 20.0, 80.0)

    # =================================================================
    # HELPER
    # =================================================================

    def _is_trend_aligned(
        self, data: SymbolData, strategy: StrategyType
    ) -> bool:
        """Check if the current trend aligns with the strategy's direction."""
        bullish = [
            StrategyType.BULL_PUT_CREDIT, StrategyType.BULL_CALL_DEBIT,
            StrategyType.LONG_CALL, StrategyType.COVERED_CALL,
            StrategyType.CASH_SECURED_PUT, StrategyType.PMCC,
            StrategyType.LEAPS,
        ]
        bearish = [
            StrategyType.BEAR_CALL_CREDIT, StrategyType.BEAR_PUT_DEBIT,
            StrategyType.LONG_PUT,
        ]
        if strategy in bullish:
            return data.trend in ("UPTREND", "STRONG_UPTREND")
        elif strategy in bearish:
            return data.trend in ("DOWNTREND", "STRONG_DOWNTREND")
        return True  # Neutral strategies are always "aligned"

    # =================================================================
    # MAIN SCORING METHOD
    # =================================================================

    def score_symbol(
        self,
        data: SymbolData,
        strategies: List[StrategyType] = None,
    ) -> List[StrategyScore]:
        """
        Score a symbol against all (or specified) strategies.

        Returns a list of StrategyScore objects, one per strategy,
        sorted by composite_score descending.
        """
        if strategies is None:
            strategies = list(StrategyType)

        scores = []
        for strat in strategies:
            score = StrategyScore(
                strategy=strat,
                symbol=data.symbol,
            )

            # Calculate each component
            score.iv_score = self._score_iv(data, strat)
            score.trend_score = self._score_trend(data, strat)
            score.flow_score = self._score_flow(data)
            score.dark_pool_score = self._score_dark_pool(data)
            score.gex_score = self._score_gex(data, strat)
            score.catalyst_score = self._score_catalyst(data, strat)
            score.risk_reward_score = self._score_risk_reward(data, strat)
            score.sentiment_score = self._score_sentiment(data)
            score.minervini_score = self._score_minervini(data, strat)

            # Determine direction
            if self._is_trend_aligned(data, strat):
                if data.trend in ("UPTREND", "STRONG_UPTREND"):
                    score.direction = Direction.BULLISH
                elif data.trend in ("DOWNTREND", "STRONG_DOWNTREND"):
                    score.direction = Direction.BEARISH
                else:
                    score.direction = Direction.NEUTRAL
            else:
                score.direction = Direction.NEUTRAL

            # Weighted composite score
            w = self._get_weights_for_strategy(strat)
            score.composite_score = (
                score.iv_score * w["iv"]
                + score.trend_score * w["trend"]
                + score.flow_score * w["flow"]
                + score.dark_pool_score * w["dark_pool"]
                + score.gex_score * w["gex"]
                + score.catalyst_score * w["catalyst"]
                + score.risk_reward_score * w["risk_reward"]
                + score.sentiment_score * w["sentiment"]
                + score.minervini_score * w["minervini"]
            )

            # Get quantitative metrics from EdgeCalculator
            strategy_name = strat.value
            trend_aligned = self._is_trend_aligned(data, strat)
            metrics = EdgeCalculator.calculate_strategy_ev(
                strategy_name, data.iv_rank, self.market.vix, trend_aligned
            )
            score.win_rate = metrics["win_rate"]
            score.avg_win = metrics["avg_win"]
            score.avg_loss = metrics["avg_loss"]
            score.expected_value = metrics["expected_value"]
            score.kelly_fraction = metrics["kelly_fraction"]

            # POP
            score.probability_of_profit = EdgeCalculator.probability_of_profit_short(
                data.price, data.price * 0.9, data.iv, 30
            )

            # Confidence: based on how many components agree
            high_components = sum(1 for s in [
                score.iv_score, score.trend_score, score.flow_score,
                score.gex_score, score.risk_reward_score
            ] if s >= 60)
            score.confidence_score = min(high_components * 20.0, 100.0)

            # Reasoning
            score.reasoning = self._build_reasoning(data, strat, score)

            scores.append(score)

        # Sort by composite score descending
        scores.sort(key=lambda s: s.composite_score, reverse=True)
        return scores

    def _get_weights_for_strategy(
        self, strategy: StrategyType
    ) -> Dict[str, float]:
        """
        Adjust weights based on strategy type.

        Credit strategies: IV weight is king
        Debit strategies: Trend weight is king
        Neutral strategies: GEX and IV are most important
        """
        w = dict(self.base_weights)

        credit = [
            StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
            StrategyType.BEAR_CALL_CREDIT, StrategyType.WHEEL,
            StrategyType.COVERED_CALL, StrategyType.CASH_SECURED_PUT,
        ]
        debit = [
            StrategyType.BULL_CALL_DEBIT, StrategyType.BEAR_PUT_DEBIT,
            StrategyType.LONG_CALL, StrategyType.LONG_PUT,
            StrategyType.LEAPS, StrategyType.PMCC,
        ]

        if strategy in credit:
            w["iv"] = 0.30
            w["trend"] = 0.10
            w["risk_reward"] = 0.20
            w["gex"] = 0.10
        elif strategy in debit:
            w["iv"] = 0.10
            w["trend"] = 0.30
            w["risk_reward"] = 0.20
            w["minervini"] = 0.15
        else:
            # Neutral: balance IV and GEX
            w["iv"] = 0.25
            w["gex"] = 0.15
            w["trend"] = 0.10

        # Normalize weights to sum to 1.0
        total = sum(w.values())
        if total > 0:
            w = {k: v / total for k, v in w.items()}
        return w

    def _build_reasoning(
        self, data: SymbolData, strategy: StrategyType,
        score: StrategyScore
    ) -> List[str]:
        """Build human-readable reasoning for the score."""
        reasons = []

        if score.iv_score >= 70:
            reasons.append(f"Strong IV environment for {strategy.value}: IV Rank {data.iv_rank:.0f}")
        elif score.iv_score <= 30:
            reasons.append(f"Weak IV for {strategy.value}: IV Rank {data.iv_rank:.0f}")

        if score.trend_score >= 70:
            reasons.append(f"Strong trend alignment: {data.trend}, RSI {data.rsi_14:.0f}")

        if score.flow_score >= 70:
            reasons.append(f"Strong options flow: volume ratio {data.volume_ratio:.1f}x")

        if score.gex_score >= 70:
            reasons.append(f"Favorable GEX regime: {data.gex_regime.value}")

        if score.risk_reward_score >= 70:
            reasons.append(
                f"Excellent risk/reward: EV {score.expected_value:.3f}, "
                f"Kelly {score.kelly_fraction:.3f}"
            )

        if score.catalyst_score >= 70:
            reasons.append(f"Favorable catalyst positioning: {data.days_to_earnings}d to earnings")

        if score.minervini_score >= 70:
            reasons.append("Strong Minervini SEPA template compliance")

        if score.composite_score < 40:
            reasons.append("Low overall score, may not meet entry criteria")

        return reasons
