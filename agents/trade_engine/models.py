"""
Unified Models for the Trade Recommendation Engine.
Supports both the new Capital-In/Trade-Out engine AND the sub-agent's
6-stage pipeline (SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


# =================================================================
# ENUMS
# =================================================================

class StrategyType(str, Enum):
    BULL_PUT_CREDIT = "bull_put_credit"
    BEAR_CALL_CREDIT = "bear_call_credit"
    CASH_SECURED_PUT = "cash_secured_put"
    COVERED_CALL = "covered_call"
    IRON_CONDOR = "iron_condor"
    VERTICAL_SPREAD = "vertical_spread"
    CALL_DEBIT_SPREAD = "call_debit_spread"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    CALENDAR_SPREAD = "calendar_spread"
    BUTTERFLY = "butterfly"
    STRADDLE = "straddle"
    STRANGLE = "strangle"
    LEAPS = "leaps"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    WHEEL_CSP = "wheel_csp"
    WHEEL_CC = "wheel_cc"
    COVERED_PUT = "covered_put"
    # Pipeline-compatible aliases
    WHEEL = "wheel"
    BULL_CALL_DEBIT = "bull_call_debit"
    BEAR_PUT_DEBIT = "bear_put_debit"
    IRON_BUTTERFLY = "iron_butterfly"
    PMCC = "pmcc"
    EARNINGS_SHORT_STRADDLE = "earnings_short_straddle"
    EARNINGS_SHORT_STRANGLE = "earnings_short_strangle"
    EARNINGS_IRON_CONDOR = "earnings_iron_condor"


class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class MarketRegime(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"
    EARNINGS = "earnings"
    # IV-based regimes used by pipeline scanner
    NORMAL = "normal"
    VERY_LOW = "very_low"
    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class GEXRegime(str, Enum):
    HIGH_POSITIVE = "high_positive"
    HIGH_NEGATIVE = "high_negative"
    FLIP_ZONE = "flip_zone"
    NEUTRAL = "neutral"


# =================================================================
# PIPELINE DATA CLASSES (used by scanner, scorer, sizer, etc.)
# =================================================================

@dataclass
class SymbolData:
    """Complete data for a single symbol through the scanning pipeline."""
    symbol: str = ""
    price: float = 0.0
    volume_avg_20: float = 0.0
    volume_ratio: float = 1.0
    option_chain: List[Dict[str, Any]] = field(default_factory=list)
    # Technical
    trend: str = "NEUTRAL"
    rsi_14: float = 50.0
    bb_position: float = 0.5
    atr_14: float = 0.0
    above_150_sma: bool = False
    above_200_sma: bool = False
    sma150_above_sma200: bool = False
    relative_strength_rank: float = 50.0
    macd_signal: str = "neutral"
    # Volatility
    iv_rank: float = 50.0
    iv: float = 0.20
    hv_20: float = 0.18
    nvrp: float = 0.0
    implied_move_pct: float = 0.0
    gex_regime: GEXRegime = GEXRegime.NEUTRAL
    # Flow
    flow_score: float = 0.0
    dark_pool_confirmed: bool = False
    sentiment_score: float = 0.0
    # Catalyst
    days_to_earnings: Optional[int] = None
    historical_earnings_move_pct: float = 0.0
    sector: str = ""


@dataclass
class MarketConditions:
    """Market-wide conditions used by the scorer."""
    vix: float = 20.0
    vix_rank: float = 50.0
    trend: str = "neutral"
    put_call_ratio: float = 1.0
    advance_decline: float = 1.0
    breadth: float = 0.5
    gex_regime: GEXRegime = GEXRegime.NEUTRAL


@dataclass
class CurrentPosition:
    """An existing portfolio position."""
    symbol: str = ""
    strategy: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    sector: str = ""
    delta: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    max_loss: float = 0.0
    capital: float = 0.0
    entry_date: str = ""
    dte: int = 0


@dataclass
class StrategyScore:
    """Score output from the scoring engine."""
    strategy: StrategyType = StrategyType.IRON_CONDOR
    symbol: str = ""
    direction: Direction = Direction.NEUTRAL
    # Component scores (0-100)
    iv_score: float = 0.0
    trend_score: float = 0.0
    flow_score: float = 0.0
    dark_pool_score: float = 0.0
    gex_score: float = 0.0
    catalyst_score: float = 0.0
    risk_reward_score: float = 0.0
    sentiment_score: float = 0.0
    minervini_score: float = 0.0
    # Composite
    composite_score: float = 0.0
    confidence_score: float = 0.0
    # Edge metrics
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expected_value: float = 0.0
    kelly_fraction: float = 0.0
    probability_of_profit: float = 0.0
    # Reasoning
    reasoning: str = ""


# =================================================================
# TRADE ENGINE DATA CLASSES (used by Capital-In/Trade-Out engine)
# =================================================================

@dataclass
class AccountInfo:
    """User's IBKR account information."""
    total_equity: float
    buying_power: float
    cash_available: float
    current_positions: List[Dict[str, Any]] = field(default_factory=list)
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    max_positions: int = 10
    max_sector_exposure_pct: float = 25.0


@dataclass
class OptionContract:
    """A specific option contract with full details."""
    symbol: str
    strike: float
    expiry: str
    option_type: str  # CALL or PUT
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    dte: int = 0
    itm: bool = False


@dataclass
class StrategyLeg:
    """A single leg in a multi-leg strategy."""
    contract: OptionContract
    action: str  # BUY or SELL
    quantity: int
    role: str = ""


@dataclass
class TradeRecommendation:
    """A complete trade recommendation with all details."""
    recommendation_id: str
    strategy_type: StrategyType
    symbol: str
    underlying_price: float
    legs: List[StrategyLeg]
    quantity: int
    net_debit: float = 0.0
    net_credit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    breakeven: float = 0.0
    risk_reward_ratio: float = 0.0
    probability_of_profit: float = 0.0
    expected_value: float = 0.0
    kelly_fraction: float = 0.0
    capital_required: float = 0.0
    capital_at_risk: float = 0.0
    return_on_capital_pct: float = 0.0
    annualized_return_pct: float = 0.0
    composite_score: float = 0.0
    confidence_score: float = 0.0
    iv_rank: float = 0.0
    iv_percentile: float = 0.0
    vix: float = 0.0
    market_regime: MarketRegime = MarketRegime.NEUTRAL
    days_to_earnings: Optional[int] = None
    reasoning: str = ""
    risk_warning: str = ""
    entry_rules: Dict[str, Any] = field(default_factory=dict)
    exit_rules: Dict[str, Any] = field(default_factory=dict)
    data_sources: List[str] = field(default_factory=list)
    layers_passed: List[str] = field(default_factory=list)


@dataclass
class AdvisoryOutput:
    """Complete advisory output for the user."""
    account_summary: AccountInfo
    recommendations: List[TradeRecommendation]
    market_context: Dict[str, Any]
    portfolio_analysis: Dict[str, Any]
    total_capital_deployed: float = 0.0
    remaining_buying_power: float = 0.0
    warnings: List[str] = field(default_factory=list)
