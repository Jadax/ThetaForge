"""
ThetaForge Strategy Module.
All 11 strategies registered here.
"""
from agents.strategies.wheel import WheelStrategy
from agents.strategies.vertical_spreads import VerticalSpreadStrategy
from agents.strategies.iron_condor import IronCondorStrategy
from agents.strategies.credit_spread import CreditSpreadStrategy
from agents.strategies.covered_call import CoveredCallStrategy
from agents.strategies.earnings_straddle import EarningsStraddleStrategy
from agents.strategies.gamma_blast import GammaBlastStrategy
from agents.strategies.leaps import LEAPSStrategy
from agents.strategies.calendar_spread import CalendarSpreadStrategy
from agents.strategies.butterfly_spread import ButterflySpreadStrategy
from agents.strategies.long_call_put import LongCallPutStrategy

STRATEGY_REGISTRY = {
    "wheel": WheelStrategy,
    "vertical_spreads": VerticalSpreadStrategy,
    "iron_condor": IronCondorStrategy,
    "credit_spread": CreditSpreadStrategy,
    "covered_call": CoveredCallStrategy,
    "earnings_straddle": EarningsStraddleStrategy,
    "gamma_blast": GammaBlastStrategy,
    "leaps": LEAPSStrategy,
    "calendar_spread": CalendarSpreadStrategy,
    "butterfly_spread": ButterflySpreadStrategy,
    "long_call_put": LongCallPutStrategy,
}

__all__ = ["STRATEGY_REGISTRY"] + [cls.__name__ for cls in STRATEGY_REGISTRY.values()]
