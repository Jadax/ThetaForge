"""
Trade Recommendation Engine - Strategy Selector.

Selects the optimal strategy for each symbol and ranks the top
candidates for final recommendation.

PIPELINE STAGE 4 of 6: SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND

SELECTION LOGIC:
  1. Group strategy scores by symbol
  2. Pick the best strategy per symbol (highest composite score)
  3. Rank all symbol-strategy pairs by expected value * Kelly fraction
  4. Apply strategy diversity filter (don't recommend 10 iron condors)
  5. Return top-N candidates for validation

METHODOLOGY:
  - Option Alpha: Select strategy based on IV regime + trend
  - TastyTrade: Prefer strategies that benefit from time decay
  - Bierman: Prioritize premium yield
  - Institutional: Diversify across uncorrelated strategies
"""
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from .models import (
    StrategyScore, StrategyType, Direction,
    MarketRegime, GEXRegime,
)
from .edge_calculator import EdgeCalculator


class StrategySelector:
    """
    Selects and ranks the best strategy-symbol combinations.

    Takes scored strategies from the scorer and produces a ranked
    list of candidates for the validation stage.
    """

    def __init__(self, market_regime: MarketRegime = MarketRegime.NORMAL):
        self.market_regime = market_regime

        # Maximum number of same strategy type in recommendations
        self.max_same_strategy = 3
        # Maximum number of recommendations
        self.top_n = 10
        # Minimum composite score to consider
        self.min_composite_score = 30.0

    def select_best_per_symbol(
        self, all_scores: List[StrategyScore]
    ) -> List[StrategyScore]:
        """
        For each symbol, select the single best strategy.

        Groups scores by symbol and picks the highest composite score.
        """
        symbol_groups = defaultdict(list)
        for score in all_scores:
            symbol_groups[score.symbol].append(score)

        best_per_symbol = []
        for symbol, scores in symbol_groups.items():
            # Sort by composite score
            scores.sort(key=lambda s: s.composite_score, reverse=True)
            best_per_symbol.append(scores[0])

        return best_per_symbol

    def rank_by_expected_value(
        self, scores: List[StrategyScore]
    ) -> List[StrategyScore]:
        """
        Rank strategies by Expected Value * Kelly Fraction.

        This metric captures both the quality and size of opportunity.
        High EV with high Kelly = strong signal.

        Secondary sort by composite_score for tie-breaking.
        """
        for score in scores:
            # Combined ranking metric
            score._ev_x_kelly = (
                score.expected_value * score.kelly_fraction * 100
                + score.composite_score * 0.01
            )

        scores.sort(
            key=lambda s: (s._ev_x_kelly, s.composite_score), reverse=True
        )
        return scores

    def apply_strategy_diversity(
        self, ranked_scores: List[StrategyScore]
    ) -> List[StrategyScore]:
        """
        Ensure strategy diversity in recommendations.

        Limits the number of recommendations using the same strategy type
        to prevent concentration risk.
        """
        strategy_counts = defaultdict(int)
        diversified = []

        for score in ranked_scores:
            strat = score.strategy
            if strategy_counts[strat] < self.max_same_strategy:
                diversified.append(score)
                strategy_counts[strat] += 1

        return diversified

    def select_top_candidates(
        self,
        all_scores: List[StrategyScore],
        top_n: int = None,
    ) -> List[StrategyScore]:
        """
        Main selection pipeline.

        Takes all scored strategies and returns the top-N candidates.
        """
        if top_n is None:
            top_n = self.top_n

        # Step 1: Filter by minimum score
        viable = [
            s for s in all_scores
            if s.composite_score >= self.min_composite_score
        ]

        # Step 2: Select best per symbol
        best_per_symbol = self.select_best_per_symbol(viable)

        # Step 3: Rank by expected value * Kelly
        ranked = self.rank_by_expected_value(best_per_symbol)

        # Step 4: Apply strategy diversity
        diverse = self.apply_strategy_diversity(ranked)

        # Step 5: Top N
        return diverse[:top_n]

    def select_earnings_candidates(
        self, all_scores: List[StrategyScore]
    ) -> List[StrategyScore]:
        """
        Specialized selection for earnings-based strategies.

        Filters for earnings-specific strategies and ranks by EVR and
        expected move analysis.
        """
        earnings_strategies = [
            s for s in all_scores
            if "EARNINGS" in s.strategy.value
            and s.composite_score >= self.min_composite_score
        ]

        # Rank by risk/reward score (captures EVR and premium analysis)
        earnings_strategies.sort(
            key=lambda s: s.risk_reward_score, reverse=True
        )

        return earnings_strategies[:5]  # Top 5 earnings plays

    def select_by_market_regime(
        self, all_scores: List[StrategyScore]
    ) -> List[StrategyScore]:
        """
        Filter and rank strategies based on the current market regime.

        Low VIX / Normal:    Favor premium selling, iron condors
        High VIX:            Favor directional, wide spreads, or buying premium
        Extreme VIX:         Favor defensive, reduce position count
        """
        regime_preferences = {
            MarketRegime.VERY_LOW: [
                StrategyType.BULL_CALL_DEBIT, StrategyType.BEAR_PUT_DEBIT,
                StrategyType.LONG_CALL, StrategyType.LONG_PUT,
                StrategyType.LEAPS, StrategyType.PMCC,
            ],
            MarketRegime.LOW: [
                StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
                StrategyType.BEAR_CALL_CREDIT, StrategyType.WHEEL,
            ],
            MarketRegime.NORMAL: [
                StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
                StrategyType.BEAR_CALL_CREDIT, StrategyType.WHEEL,
                StrategyType.COVERED_CALL, StrategyType.CASH_SECURED_PUT,
            ],
            MarketRegime.ELEVATED: [
                StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_CREDIT,
                StrategyType.BEAR_CALL_CREDIT,
                StrategyType.CALENDAR_SPREAD,
            ],
            MarketRegime.HIGH: [
                StrategyType.IRON_CONDOR, StrategyType.BUTTERFLY,
                StrategyType.CALENDAR_SPREAD,
                StrategyType.BULL_PUT_CREDIT,
            ],
            MarketRegime.EXTREME: [
                StrategyType.IRON_BUTTERFLY, StrategyType.BUTTERFLY,
                StrategyType.CALENDAR_SPREAD,
            ],
        }

        preferred = regime_preferences.get(self.market_regime, [])

        # Separate preferred and non-preferred
        preferred_scores = [
            s for s in all_scores if s.strategy in preferred
        ]
        other_scores = [
            s for s in all_scores if s.strategy not in preferred
        ]

        # Rank preferred first, then others
        preferred_scores.sort(
            key=lambda s: s.composite_score, reverse=True
        )
        other_scores.sort(
            key=lambda s: s.composite_score, reverse=True
        )

        return preferred_scores + other_scores
