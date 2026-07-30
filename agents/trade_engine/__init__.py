# Trade Recommendation Engine
# Capital In → Specific Trades Out
#
# TradeRecommender scans each symbol's option chain, scores candidate
# structures with StrategyScorer, prices them with ROICalculator and
# OptionsAnalytics, and emits only those that clear the composite, edge, and
# modelled-probability gates.

from agents.trade_engine.models import (
    AccountInfo, TradeRecommendation, AdvisoryOutput,
    StrategyType, RiskTolerance, MarketRegime, Direction, GEXRegime,
    OptionContract, StrategyLeg,
)

from agents.trade_engine.roi_calculator import ROICalculator
from agents.trade_engine.analytics import OptionsAnalytics
from agents.trade_engine.strategy_scorer import StrategyScorer
from agents.trade_engine.recommender import TradeRecommender
