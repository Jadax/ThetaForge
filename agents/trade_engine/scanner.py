"""
Trade Recommendation Engine - Symbol Scanner.

Multi-layer SCAN pipeline that filters 7000+ symbols down to actionable
candidates. Each layer progressively narrows the universe.

PIPELINE STAGE 1 of 6: SCAN -> SCORE -> SIZE -> SELECT -> VALIDATE -> RECOMMEND

LAYERS:
  Layer 1: Liquidity Filter     - Volume, open interest, spread width
  Layer 2: Technical Filter     - Trend, momentum, Minervini SEPA template
  Layer 3: Volatility Filter    - IV Rank/Percentile, IV/HV relationship
  Layer 4: Flow and Sentiment   - Unusual options activity, dark pool prints
  Layer 5: Catalyst Filter      - Earnings proximity, FOMC, macro events
  Layer 6: Correlation Filter   - Sector overlap, beta exposure
"""
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

from .models import (
    SymbolData, MarketConditions, AccountInfo,
    Direction, MarketRegime, GEXRegime, RiskTolerance,
)


@dataclass
class ScanResult:
    """Result of scanning a single symbol through all layers."""
    symbol: str = ""
    passed_layers: List[str] = field(default_factory=list)
    failed_layers: List[str] = field(default_factory=list)
    layer_scores: Dict[str, float] = field(default_factory=dict)
    direction: Direction = Direction.NEUTRAL
    scan_score: float = 0.0
    rejection_reasons: List[str] = field(default_factory=list)


class SymbolScanner:
    """
    Multi-layer symbol scanner.

    Implements a progressive filtering pipeline where each layer
    eliminates symbols that do not meet minimum criteria. Symbols
    that pass all layers are scored and returned as ScanResults.

    METHODOLOGY SOURCES:
      - Option Alpha: HV-based probability + current option prices
      - TastyTrade: IV vs HV edge detection (NVRP)
      - Market Chameleon: IV/HV ratio, expected move analysis
      - Minervini: SEPA Trend Template for directional screening
      - Jeff Bierman: Premium yield screening
    """

    def __init__(self, market_conditions: MarketConditions, account: AccountInfo):
        self.market = market_conditions
        self.account = account

        # Thresholds (tunable)
        self.min_volume_avg = 500_000
        self.min_open_interest = 100
        self.max_bid_ask_spread_pct = 0.10  # 10% of mid
        self.min_price = 5.0
        self.max_price = 500.0

    # =====================================================================
    # LAYER 1: LIQUIDITY FILTER
    # =====================================================================

    def _layer_liquidity(self, data: SymbolData) -> Tuple[bool, float, str]:
        """
        Liquidity screening: Can we get in and out of this position?

        Checks:
          - Average daily volume > threshold
          - Options open interest > threshold
          - Bid-ask spread width < threshold
          - Price within tradeable range
        """
        reasons = []
        score = 0.0

        # Price filter
        if data.price < self.min_price:
            reasons.append(f"Price ${data.price:.2f} below minimum ${self.min_price}")
        elif data.price > self.max_price:
            reasons.append(f"Price ${data.price:.2f} above maximum ${self.max_price}")
        else:
            score += 25.0

        # Volume filter
        if data.volume_avg_20 < self.min_volume_avg:
            reasons.append(
                f"20d avg volume {data.volume_avg_20:,} below minimum {self.min_volume_avg:,}"
            )
        else:
            score += 25.0

        # Options chain liquidity
        has_liquid_options = len(data.option_chain) > 0
        if not has_liquid_options:
            reasons.append("No option chain data available")
        else:
            liquid_expirations = 0
            for opt in data.option_chain[:10]:
                bid = opt.get("bid", 0)
                ask = opt.get("ask", 0)
                mid = (bid + ask) / 2 if (bid + ask) > 0 else 0
                if mid > 0 and (ask - bid) / mid <= self.max_bid_ask_spread_pct:
                    liquid_expirations += 1
            if liquid_expirations >= 3:
                score += 50.0
            else:
                reasons.append("Insufficient liquid option expirations")

        passed = len(reasons) == 0
        return (passed, score, "; ".join(reasons) if reasons else "Liquidity OK")

    # =====================================================================
    # LAYER 2: TECHNICAL FILTER (Minervini SEPA)
    # =====================================================================

    def _layer_technical(self, data: SymbolData) -> Tuple[bool, float, str]:
        """
        Technical analysis screening.

        Incorporates Minervini SEPA Trend Template:
          - Price > 150-day SMA > 200-day SMA
          - 150-day SMA > 200-day SMA
          - 200-day SMA rising for at least 1 month
          - 50-day SMA > 150-day SMA and 200-day SMA
          - Price > 50-day SMA
          - Price at least 25% above 52-week low
          - Relative Strength rank > 70 (top 30% of market)

        Also includes RSI, MACD, Bollinger Band position.
        """
        reasons = []
        score = 0.0

        # --- Minervini SEPA Template ---
        sepa_score = 0
        if data.above_150_sma:
            sepa_score += 1
        if data.above_200_sma:
            sepa_score += 1
        if data.sma150_above_sma200:
            sepa_score += 1
        if data.relative_strength_rank >= 70:
            sepa_score += 1

        if sepa_score >= 3:
            score += 40.0
        elif sepa_score >= 1:
            score += 15.0
        else:
            reasons.append("Weak Minervini SEPA template")

        # --- Trend Assessment ---
        if data.trend == "STRONG_UPTREND":
            score += 25.0
        elif data.trend == "UPTREND":
            score += 15.0
        elif data.trend == "NEUTRAL":
            score += 5.0
        elif data.trend == "DOWNTREND":
            reasons.append("In downtrend")
        elif data.trend == "STRONG_DOWNTREND":
            score -= 10.0
            reasons.append("Strong downtrend")

        # --- RSI Assessment ---
        if 40 <= data.rsi_14 <= 60:
            score += 10.0
        elif 30 <= data.rsi_14 < 40:
            score += 15.0
        elif 20 <= data.rsi_14 < 30:
            score += 20.0
        elif 60 < data.rsi_14 <= 70:
            score += 5.0
        elif data.rsi_14 > 70:
            reasons.append(f"RSI overbought at {data.rsi_14:.1f}")
        elif data.rsi_14 < 20:
            reasons.append(f"RSI deeply oversold at {data.rsi_14:.1f}, high risk")

        # --- Bollinger Band Position ---
        if 0.2 <= data.bb_position <= 0.8:
            score += 10.0
        elif data.bb_position < 0.1:
            score += 5.0
        elif data.bb_position > 0.9:
            reasons.append("Near upper Bollinger Band")

        # --- ATR-Based Volatility Check ---
        if data.atr_14 > 0 and data.price > 0:
            atr_pct = (data.atr_14 / data.price) * 100
            if atr_pct > 10:
                reasons.append(f"Extremely volatile: ATR {atr_pct:.1f}%")
            elif atr_pct > 5:
                score += 5.0

        passed = len(reasons) == 0
        return (passed, score, "; ".join(reasons) if reasons else "Technical OK")

    # =====================================================================
    # LAYER 3: VOLATILITY FILTER
    # =====================================================================

    def _layer_volatility(self, data: SymbolData) -> Tuple[bool, float, str]:
        """
        Volatility analysis screening.

        Checks IV environment for edge opportunities:
          - IV Rank zones: Low (<25), Normal (25-75), High (>75)
          - IV vs HV relationship (NVRP)
          - IV/HV ratio for premium pricing assessment
        """
        reasons = []
        score = 0.0

        # IV Rank assessment
        iv_rank = data.iv_rank
        if iv_rank >= 75:
            score += 30.0
        elif iv_rank >= 50:
            score += 20.0
        elif iv_rank >= 25:
            score += 10.0
        elif iv_rank >= 10:
            score += 5.0
        else:
            reasons.append(f"IV Rank {iv_rank:.0f} extremely low, limited edge")
            score -= 10.0

        # IV vs HV (NVRP - TastyTrade/Option Alpha)
        if data.hv_20 > 0:
            nvrp = data.nvrp
            if nvrp > 0.15:
                score += 20.0  # Options rich, SELL signal
            elif nvrp > 0.05:
                score += 10.0
            elif nvrp < -0.15:
                score += 15.0  # Options cheap, BUY signal
            elif nvrp < -0.05:
                score += 5.0

        # IV/HV Ratio (Market Chameleon)
        if data.hv_20 > 0:
            ratio = data.iv / data.hv_20
            if ratio > 1.5:
                score += 15.0
            elif ratio > 1.1:
                score += 5.0
            elif ratio < 0.7:
                score += 10.0
            elif ratio < 0.85:
                score += 5.0

        # EVR (OptionSlam)
        if data.historical_earnings_move_pct > 0 and data.implied_move_pct > 0:
            evr = data.implied_move_pct / data.historical_earnings_move_pct
            if evr > 1.2:
                score += 10.0
            elif evr < 0.8:
                score += 5.0

        passed = len(reasons) == 0
        return (passed, score, "; ".join(reasons) if reasons else "Volatility OK")

    # =====================================================================
    # LAYER 4: FLOW AND SENTIMENT FILTER
    # =====================================================================

    def _layer_flow_sentiment(self, data: SymbolData) -> Tuple[bool, float, str]:
        """
        Options flow and sentiment screening.

        Checks:
          - Unusual options activity (volume ratio vs OI)
          - Dark pool prints confirmation
          - Sentiment score
        """
        reasons = []
        score = 0.0

        if data.volume_ratio >= 2.0:
            score += 20.0
        elif data.volume_ratio >= 1.5:
            score += 15.0
        elif data.volume_ratio >= 1.0:
            score += 10.0
        elif data.volume_ratio < 0.5:
            reasons.append(f"Very low volume ratio: {data.volume_ratio:.2f}")

        if data.dark_pool_confirmed:
            score += 15.0

        if data.flow_score > 0.7:
            score += 15.0
        elif data.flow_score > 0.5:
            score += 10.0
        elif data.flow_score > 0.3:
            score += 5.0

        if data.sentiment_score > 0.6:
            score += 10.0
        elif data.sentiment_score < -0.6:
            score += 10.0
        else:
            score += 5.0

        passed = len(reasons) == 0
        return (passed, score, "; ".join(reasons) if reasons else "Flow/Sentiment OK")

    # =====================================================================
    # LAYER 5: CATALYST FILTER
    # =====================================================================

    def _layer_catalyst(self, data: SymbolData) -> Tuple[bool, float, str]:
        """
        Catalyst screening. Manages event risk.

          - Earnings proximity
          - FOMC, CPI, NFP dates
        """
        reasons = []
        score = 10.0  # Base score (no catalyst)

        # Earnings
        if data.days_to_earnings <= 7:
            score -= 20.0
            reasons.append(
                f"Earnings in {data.days_to_earnings} days - high IV, binary event risk"
            )
        elif data.days_to_earnings <= 14:
            score -= 5.0
            reasons.append(f"Earnings in {data.days_to_earnings} days")
        elif 14 < data.days_to_earnings <= 30:
            score += 5.0
        elif 30 < data.days_to_earnings <= 45:
            score += 10.0  # IV ramp zone

        # Market-level Catalysts
        if self.market.fomc_days_away <= 5:
            score -= 10.0
            reasons.append(f"FOMC in {self.market.fomc_days_away} days")
        elif self.market.fomc_days_away <= 14:
            score -= 5.0

        if self.market.cpi_days_away <= 3:
            score -= 10.0
            reasons.append(f"CPI in {self.market.cpi_days_away} days")

        if self.market.nfp_days_away <= 3:
            score -= 5.0
            reasons.append(f"NFP in {self.market.nfp_days_away} days")

        passed = len(reasons) == 0 or score >= 0
        return (passed, score, "; ".join(reasons) if reasons else "Catalyst OK")

    # =====================================================================
    # LAYER 6: CORRELATION FILTER
    # =====================================================================

    def _layer_correlation(
        self, data: SymbolData, current_symbols: List[str]
    ) -> Tuple[bool, float, str]:
        """
        Correlation screening. Prevents portfolio concentration.

          - Sector overlap check
          - Maximum positions per sector
        """
        reasons = []
        score = 10.0

        # Crude sector proxy (first letter of ticker)
        sector_count = sum(
            1 for s in current_symbols
            if s.startswith(data.symbol[:1])
        )
        if sector_count >= 3:
            score -= 10.0
            reasons.append("Already have significant sector exposure")

        if data.above_150_sma and data.above_200_sma:
            score += 5.0

        passed = score >= 0
        return (passed, score, "; ".join(reasons) if reasons else "Correlation OK")

    # =====================================================================
    # MAIN SCAN METHOD
    # =====================================================================

    def scan_symbol(
        self,
        data: SymbolData,
        current_symbols: List[str] = None,
    ) -> ScanResult:
        """
        Run a single symbol through all scan layers.

        Returns ScanResult with pass/fail and scores for each layer.
        """
        if current_symbols is None:
            current_symbols = []

        result = ScanResult(symbol=data.symbol)

        # Layer 1: Liquidity
        passed, score, reason = self._layer_liquidity(data)
        result.layer_scores["liquidity"] = score
        if passed:
            result.passed_layers.append("liquidity")
        else:
            result.failed_layers.append("liquidity")
            result.rejection_reasons.append(reason)

        # Layer 2: Technical
        passed, score, reason = self._layer_technical(data)
        result.layer_scores["technical"] = score
        if passed:
            result.passed_layers.append("technical")
        else:
            result.failed_layers.append("technical")
            result.rejection_reasons.append(reason)

        # Layer 3: Volatility
        passed, score, reason = self._layer_volatility(data)
        result.layer_scores["volatility"] = score
        if passed:
            result.passed_layers.append("volatility")
        else:
            result.failed_layers.append("volatility")
            result.rejection_reasons.append(reason)

        # Layer 4: Flow and Sentiment
        passed, score, reason = self._layer_flow_sentiment(data)
        result.layer_scores["flow_sentiment"] = score
        if passed:
            result.passed_layers.append("flow_sentiment")
        else:
            result.failed_layers.append("flow_sentiment")
            result.rejection_reasons.append(reason)

        # Layer 5: Catalyst
        passed, score, reason = self._layer_catalyst(data)
        result.layer_scores["catalyst"] = score
        if passed:
            result.passed_layers.append("catalyst")
        else:
            result.failed_layers.append("catalyst")
            result.rejection_reasons.append(reason)

        # Layer 6: Correlation
        passed, score, reason = self._layer_correlation(data, current_symbols)
        result.layer_scores["correlation"] = score
        if passed:
            result.passed_layers.append("correlation")
        else:
            result.failed_layers.append("correlation")
            result.rejection_reasons.append(reason)

        # Determine direction from technical signals
        if data.trend in ("STRONG_UPTREND", "UPTREND") and data.rsi_14 > 50:
            result.direction = Direction.BULLISH
        elif data.trend in ("STRONG_DOWNTREND", "DOWNTREND") and data.rsi_14 < 50:
            result.direction = Direction.BEARISH
        else:
            result.direction = Direction.NEUTRAL

        # Calculate composite scan score
        result.scan_score = sum(result.layer_scores.values())

        return result

    def scan_batch(
        self,
        symbols_data: List[SymbolData],
        current_symbols: List[str] = None,
        min_score: float = 0.0,
    ) -> List[ScanResult]:
        """
        Scan a batch of symbols and return sorted by scan_score descending.
        Only returns symbols that passed at least the liquidity layer.
        """
        if current_symbols is None:
            current_symbols = []

        results = []
        for data in symbols_data:
            result = self.scan_symbol(data, current_symbols)
            if "liquidity" in result.passed_layers and result.scan_score >= min_score:
                results.append(result)

        results.sort(key=lambda r: r.scan_score, reverse=True)
        return results
