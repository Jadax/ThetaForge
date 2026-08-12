"""
ThetaForge AI Brain - Unified Signal Orchestrator.
The central nervous system that combines ALL indicators, strategies,
and signals into a single coherent recommendation engine.

This is the synthesis of standard volatility, flow, and technical trading
research applied through a single weighted scoring model.

Architecture:
  Market Data → 15+ Signal Engines → Brain Scoring → Strategy Selection
  → Position Sizing → Risk Validation → Time-Horizon Filtering → Final Output

The Brain doesn't just add signals - it WEIGHTS them by:
1. Signal reliability (backtested win rates)
2. Current regime alignment
3. Time-horizon appropriateness
4. Portfolio context (existing positions)
5. Confidence decay (older signals get less weight)
"""
import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta

from agents.trade_engine.high_winrate import (
    relative_strength_ok as hw_relative_strength_ok,
    strategy_bias as hw_strategy_bias,
)
from agents.trade_engine import macro_calendar


class TimeHorizon(str, Enum):
    SWING_1W = "1w"      # 1 week - 0DTE to 7DTE, high gamma
    MONTHLY_1M = "1m"    # 1 month - 21-45 DTE, TastyTrade sweet spot
    QUARTERLY_3M = "3m"  # 3 months - 60-90 DTE, LEAPS entry
    LEAPS_6M = "6m"      # 6+ months - LEAPS, diagonal spreads


class SignalStrength(str, Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    WEAK_BUY = "weak_buy"
    NEUTRAL = "neutral"
    WEAK_SELL = "weak_sell"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


@dataclass
class SignalResult:
    """A single signal from any engine."""
    source: str
    signal: str
    strength: float  # -1 to +1
    confidence: float  # 0 to 100
    reasoning: str
    weight: float = 1.0  # Dynamic weight based on regime


@dataclass
class BrainOutput:
    """Complete output from the AI Brain."""
    symbol: str
    stock_price: float
    # Overall recommendation
    overall_signal: SignalStrength
    overall_score: float  # -100 to +100
    confidence: float  # 0-100
    # Time-horizon specific
    recommendations_1w: List[Dict] = field(default_factory=list)
    recommendations_1m: List[Dict] = field(default_factory=list)
    recommendations_3m: List[Dict] = field(default_factory=list)
    recommendations_6m: List[Dict] = field(default_factory=list)
    # Strategy suggestion
    best_strategy: str = ""
    best_strategy_reasoning: str = ""
    # Market context
    regime: str = "neutral"
    cpr_signal: Dict = field(default_factory=dict)
    iv_signal: Dict = field(default_factory=dict)
    sentiment_signal: Dict = field(default_factory=dict)
    sideways_signal: Dict = field(default_factory=dict)
    # All signals for transparency
    all_signals: List[Dict] = field(default_factory=list)
    # Portfolio warnings
    portfolio_warnings: List[str] = field(default_factory=list)
    # Self-learning feedback
    signal_accuracy: Dict = field(default_factory=dict)
    dynamic_weights: Dict = field(default_factory=dict)
    # Backtest context
    backtest_summary: Dict = field(default_factory=dict)
    # Relative strength vs SPY (6-month) — IBD "L" filter
    relative_strength: float = None


class AIBrain:
    """
    The unified AI Brain that orchestrates all trading intelligence.
    
    This is the CORE of ThetaForge - it takes in data from ALL engines
    and produces a single, coherent, actionable recommendation.
    
    Weight hierarchy (institutional-style composite scoring):
    1. Flow/Unusual Activity: 25% (smart money leads)
    2. IV/NVRP Edge: 20% (volatility is the #1 edge in options)
    3. Technical/Trend: 15% (direction matters)
    4. CPR/Pivots: 15% (institutional reference levels)
    5. Sentiment (PCR Z-Score): 10% (contrarian signals)
    6. GEX Regime: 10% (dealer positioning)
    7. Sideways Detection: 5% (regime filter)
    """
    
    # Minimum signal agreement (informative-signal confidence average) before a
    # strategy can be selected. Calibrated against the live scanner: the older
    # floor of 55 was tuned when PCR/flow/GEX feeds were always present; those
    # engines now report neutral (strength ~0) and are excluded from the
    # agreement average, so the floor reflects real directional agreement.
    MIN_STRATEGY_CONFIDENCE = 45
    # Neutral signals (no directional read) that would dilute a mean of all
    # confidences are excluded from the agreement average below this strength.
    INFORMATIVE_STRENGTH_EPS = 0.05

    # Signal weights by market regime
    REGIME_WEIGHTS = {
        "bullish": {
            "flow": 0.20, "iv": 0.15, "technical": 0.22, "cpr": 0.13,
            "sentiment": 0.08, "gex": 0.08, "sideways": 0.04,
            "skew": 0.05, "short_interest": 0.05, "vrp": 0.06,
        },
        "bearish": {
            "flow": 0.23, "iv": 0.20, "technical": 0.18, "cpr": 0.08,
            "sentiment": 0.08, "gex": 0.08, "sideways": 0.05,
            "skew": 0.05, "short_interest": 0.05, "vrp": 0.06,
        },
        "neutral": {
            "flow": 0.13, "iv": 0.22, "technical": 0.09, "cpr": 0.13,
            "sentiment": 0.13, "gex": 0.08, "sideways": 0.07,
            "skew": 0.08, "short_interest": 0.07, "vrp": 0.08,
        },
        "high_vol": {
            "flow": 0.22, "iv": 0.27, "technical": 0.09, "cpr": 0.04,
            "sentiment": 0.09, "gex": 0.13, "sideways": 0.05,
            "skew": 0.06, "short_interest": 0.05, "vrp": 0.06,
        },
        "low_vol": {
            "flow": 0.08, "iv": 0.27, "technical": 0.17, "cpr": 0.12,
            "sentiment": 0.08, "gex": 0.04, "sideways": 0.09,
            "skew": 0.08, "short_interest": 0.07, "vrp": 0.06,
        },
    }

    # Strategy-to-horizon mapping
    HORIZON_STRATEGIES = {
        # Only strategies with defined risk and an implemented execution path
        # are surfaced as live ideas. 0DTE and naked-short structures require
        # separate margin, event, and execution controls.
        TimeHorizon.SWING_1W: [],
        TimeHorizon.MONTHLY_1M: [
            "iron_condor", "bull_put_credit", "bear_call_credit",
            "cash_secured_put", "covered_call",
        ],
        TimeHorizon.QUARTERLY_3M: [
            "call_debit_spread", "put_debit_spread",
        ],
        TimeHorizon.LEAPS_6M: [],
    }

    def __init__(self):
        self.signal_engines = {}
        self._load_engines()

    def _load_engines(self):
        """Lazy-load all signal engines."""
        try:
            from agents.technical.tv_indicators import TradingViewIndicators
            self.tv = TradingViewIndicators()
        except ImportError:
            self.tv = None

        try:
            from agents.trade_engine.analytics import OptionsAnalytics
            self.analytics = OptionsAnalytics()
        except ImportError:
            self.analytics = None

        try:
            from agents.flow_analysis.unusual_activity import UnusualActivityDetector
            self.flow_detector = UnusualActivityDetector()
        except ImportError:
            self.flow_detector = None

        try:
            from agents.flow_analysis.gex_engine import GEXEngine
            self.gex_engine = GEXEngine()
        except ImportError:
            self.gex_engine = None

        try:
            from agents.volatility.black_scholes import BlackScholes
            self.bs = BlackScholes
        except ImportError:
            self.bs = None

    def analyze(
        self,
        symbol: str,
        stock_price: float,
        option_chain: List[Dict],
        historical_prices: List[float] = None,
        high_prices: List[float] = None,
        low_prices: List[float] = None,
        current_iv: float = 0.20,
        hv_20: float = 0.18,
        iv_52w_high: float = 0.40,
        iv_52w_low: float = 0.12,
        vix: float = 20.0,
        gex_data: Dict = None,
        flow_data: Dict = None,
        pcr_data: Dict = None,
        days_to_earnings: int = None,
        days_to_macro: int = None,
        portfolio_context: Dict = None,
        vix_term_structure: Dict = None,
        expected_move_pct: float = None,
        iv_percentile: float = None,
        iv_skew: Dict = None,
        short_interest: Dict = None,
        earnings_move: Dict = None,
        vol_risk_premium: Dict = None,
        relative_strength: float = None,
    ) -> BrainOutput:
        """
        MAIN ENTRY POINT: Analyze a symbol and produce comprehensive recommendation.
        
        portfolio_context: {"existing_positions": [...], "symbols_held": [...], "net_delta": float, "net_vega": float}
        vix_term_structure: {"VIX9D": float, "VIX3M": float, "VIX6M": float, "VIX1Y": float}
        expected_move_pct: 1-SD expected move over the trade horizon, as a percent of spot.
        iv_percentile: IV percentile (0-100) from the symbol's own IV history.
        iv_skew: desk_analytics.calculate_iv_skew() output (RR25/BF25 surface shape).
        short_interest: free_data.get_short_interest() output (% float, days to cover).
        earnings_move: desk_analytics.earnings_move_edge() output (implied vs realized).
        vol_risk_premium: {"vrp": float, "vrp_z": float, "iv_change_5d": float} — the
            symbol's own IV-minus-RV premium and its z-score over the IV-history
            store. Scored refinement of the vol edge, never a standalone gate.
        days_to_macro: calendar days until the next scheduled FOMC/CPI/NFP print
            (macro_calendar.macro_days_until(), market-wide, same for every
            symbol). Inside the blackout window the Brain vetoes new positions
            entirely — a macro print is the largest scheduled overnight-vol
            event of its week and is not a symbol-specific risk.
        """
        closes = historical_prices or [stock_price]
        highs = high_prices or [stock_price * 1.01]
        lows = low_prices or [stock_price * 0.99]

        # Quick price-trend check for regime detection
        if len(closes) >= 50:
            sma_50 = sum(closes[-50:]) / 50
            sma_20 = sum(closes[-20:]) / 20
            price = closes[-1]
            # 20-period MA above 50-period MA → uptrend; below → downtrend
            trend = "bullish" if sma_20 > sma_50 and price > sma_20 else "bearish" if sma_20 < sma_50 and price < sma_20 else "neutral"
        else:
            trend = "neutral"

        # Detect regime
        regime = self._detect_regime(vix, current_iv, hv_20, trend)

        # Run all signal engines
        signals = []

        # 1. CPR Signal
        if self.tv and len(closes) >= 2 and len(highs) >= 2 and len(lows) >= 2:
            # CPR levels for today's decision come from the prior completed bar.
            cpr = self.tv.calculate_cpr(highs[-2], lows[-2], closes[-2])
            cpr_signal = self.tv.cpr_option_signal(stock_price, cpr)
            signals.append(SignalResult(
                source="cpr",
                signal=cpr_signal["bias"],
                strength=1.0 if cpr_signal["bias"] == "bullish" else -1.0 if cpr_signal["bias"] == "bearish" else 0,
                confidence=70,
                reasoning=cpr_signal["reasoning"],
            ))
        else:
            cpr_signal = {}

        # 2. IV Rank / NVRP Signal
        if self.tv:
            ivr = self.tv.iv_rank(current_iv, iv_52w_high, iv_52w_low)
            iv_ratio = self.tv.iv_hv_ratio(current_iv, hv_20)
            iv_signal = {"iv_rank": ivr, **iv_ratio}

            # IV percentile is the stronger dual filter (56.8% premium-selling
            # win rate vs 48.2% for IVR alone) — MarketChameleon's methodology.
            if iv_percentile is not None:
                iv_signal["iv_percentile"] = iv_percentile
                eff_iv_rank = max(ivr, iv_percentile)
            else:
                iv_signal["iv_percentile"] = None
                eff_iv_rank = ivr
            # The dual-filter rank drives both the vol signal and strategy
            # selection so the same value is used everywhere (documented
            # relaxation: either confirm -- both must never be low).
            iv_signal["eff_iv_rank"] = eff_iv_rank

            # Expected move (1-SD) is the trader's map for strike selection.
            if expected_move_pct is not None:
                iv_signal["expected_move_pct"] = expected_move_pct

            # VIX term-structure contango/inversion regime (Option Alpha's
            # volatility-scenario framework; Tastytrade halts selling on
            # inversion). Missing data keeps the edge neutral, never bullish.
            if vix_term_structure:
                vix9d = vix_term_structure.get("VIX9D")
                vix3m = vix_term_structure.get("VIX3M")
                if vix9d is not None and vix3m is not None:
                    if vix9d < vix3m:
                        iv_signal["term_structure"] = "contango"
                    elif vix9d > vix3m:
                        iv_signal["term_structure"] = "inverted"
                    else:
                        iv_signal["term_structure"] = "flat"
                else:
                    iv_signal["term_structure"] = None
            else:
                iv_signal["term_structure"] = None

            if eff_iv_rank >= 50 and iv_ratio["signal"] == "sell_premium":
                signals.append(SignalResult(
                    source="iv", signal="sell_premium",
                    strength=0.8, confidence=min(eff_iv_rank, 90),
                    reasoning=f"IVR {ivr:.0f}" + (f"/Percentile {iv_percentile:.0f}" if iv_percentile is not None else "") + " + IV>HV → strong premium selling edge",
                ))
            elif 40 <= eff_iv_rank < 50 and iv_ratio["signal"] == "sell_premium":
                signals.append(SignalResult(
                    source="iv", signal="moderate_sell_premium",
                    strength=0.3, confidence=55,
                    reasoning=f"IVR {ivr:.0f} and IV>HV → moderate premium selling edge",
                ))
            elif eff_iv_rank <= 30 and iv_ratio["signal"] == "buy_premium":
                signals.append(SignalResult(
                    source="iv", signal="buy_premium",
                    strength=-0.5, confidence=min(100 - eff_iv_rank, 80),
                    reasoning=f"IVR {ivr:.0f} + IV<HV → premium buying edge",
                ))
            elif 30 < eff_iv_rank <= 40 and iv_ratio["signal"] == "buy_premium":
                signals.append(SignalResult(
                    source="iv", signal="moderate_buy_premium",
                    strength=-0.2, confidence=55,
                    reasoning=f"IVR {ivr:.0f} and IV<HV → moderate premium buying edge",
                ))
            else:
                signals.append(SignalResult(
                    source="iv", signal="neutral",
                    strength=0, confidence=50,
                    reasoning=f"IVR {ivr:.0f} → no clear vol edge",
                ))

            # Inverted VIX term structure overrides any premium-selling edge:
            # front-month fear makes the short side structurally toxic
            # regardless of symbol-level IVR.
            if iv_signal.get("term_structure") == "inverted" and iv_signal.get("signal") == "sell_premium":
                signals[-1] = SignalResult(
                    source="iv", signal="neutral",
                    strength=0, confidence=50,
                    reasoning=f"IVR {ivr:.0f} but VIX term structure inverted → pause premium selling",
                )

            # Earnings IV vs realized history: rich IV → sell the move, cheap
            # IV → buy it. Only nudges an already-existing vol edge; it never
            # fabricates one where the surface data is missing.
            if earnings_move and signals and signals[-1].source == "iv":
                iv_sig = signals[-1]
                read = earnings_move.get("read")
                if read == "sell_iv" and "sell_premium" in iv_sig.signal:
                    signals[-1] = SignalResult(
                        source="iv", signal=iv_sig.signal,
                        strength=min(1.0, iv_sig.strength + 0.1),
                        confidence=min(90, iv_sig.confidence + 5),
                        reasoning=(
                            f"{iv_sig.reasoning} | Earnings IV {earnings_move['implied_move_pct']:.1f}% "
                            f"vs realized median {earnings_move['median_historical_move_pct']:.1f}% → rich, sell the move"
                        ),
                    )
                elif read == "buy_iv" and "buy_premium" in iv_sig.signal:
                    signals[-1] = SignalResult(
                        source="iv", signal=iv_sig.signal,
                        strength=max(-1.0, iv_sig.strength - 0.1),
                        confidence=min(90, iv_sig.confidence + 5),
                        reasoning=(
                            f"{iv_sig.reasoning} | Earnings IV {earnings_move['implied_move_pct']:.1f}% "
                            f"vs realized median {earnings_move['median_historical_move_pct']:.1f}% → cheap, buy the move"
                        ),
                    )

            # Vol risk premium z-score (FlashAlpha / VolatilityBox institutional
            # timing metric): how rich today's IV-minus-RV premium is vs the
            # symbol's own history. A scored refinement only -- it nudges an
            # existing sell-premium read and never creates one, and a neutral
            # VRP entry carries zero strength so it cannot dilute confidence.
            vrp_z = (vol_risk_premium or {}).get("vrp_z") if vol_risk_premium else None
            vrp = (vol_risk_premium or {}).get("vrp") if vol_risk_premium else None
            iv_signal["vol_risk_premium"] = vol_risk_premium
            if vrp_z is not None and signals and signals[-1].source == "iv" and "sell_premium" in signals[-1].signal:
                if vrp_z >= 0.5:
                    signals.append(SignalResult(
                        source="vrp", signal="vrp_rich",
                        strength=0.15, confidence=70,
                        reasoning=f"VRP z {vrp_z:.1f} (premium {vrp:.1%} over RV) — premium richly priced vs its own history",
                    ))
                elif vrp_z <= -0.5:
                    signals.append(SignalResult(
                        source="vrp", signal="vrp_thin",
                        strength=-0.15, confidence=70,
                        reasoning=f"VRP z {vrp_z:.1f} (premium {vrp:.1%} over RV) — premium thin vs its own history, size down",
                    ))
                else:
                    signals.append(SignalResult(
                        source="vrp", signal="vrp_neutral",
                        strength=0.0, confidence=50,
                        reasoning=f"VRP z {vrp_z:.1f} — no premium timing edge",
                    ))
            elif vrp_z is not None:
                signals.append(SignalResult(
                    source="vrp", signal="vrp_neutral",
                    strength=0.0, confidence=50,
                    reasoning=f"VRP z {vrp_z:.1f} — no short-vol edge to refine",
                ))
        else:
            iv_signal = {"iv_rank": 50}

        # Desk surface data rides on the IV payload so the scanner and advisor
        # routes surface it without a separate transport.
        iv_signal["iv_skew"] = iv_skew
        iv_signal["short_interest"] = short_interest
        iv_signal["earnings_move"] = earnings_move

        # 2b. IV Skew — the surface shape every desk quotes (RR25/BF25).
        if iv_skew:
            skew_regime = iv_skew.get("regime")
            if skew_regime in ("fear", "elevated_fear"):
                signals.append(SignalResult(
                    source="skew", signal="put_skew_rich",
                    strength=-0.12,
                    confidence=65 if skew_regime == "fear" else 55,
                    reasoning=iv_skew.get("reasoning", ""),
                ))
            elif skew_regime == "complacent":
                signals.append(SignalResult(
                    source="skew", signal="flat_skew",
                    strength=0.08, confidence=40,
                    reasoning=iv_skew.get("reasoning", ""),
                ))
            else:
                signals.append(SignalResult(
                    source="skew", signal="neutral", strength=0.0, confidence=30,
                    reasoning=iv_skew.get("reasoning", ""),
                ))

        # 2c. Short interest — the squeeze-fuel input desks watch (% float).
        if short_interest:
            short_pct = short_interest.get("short_percent_of_float")
            days_to_cover = short_interest.get("days_to_cover")
            if (short_pct is not None and short_pct >= 30) or (days_to_cover is not None and days_to_cover >= 15):
                signals.append(SignalResult(
                    source="short_interest", signal="squeeze_fuel",
                    strength=0.3, confidence=65,
                    reasoning=(
                        f"Short interest {short_pct:.0f}% of float" if short_pct is not None else f"Days to cover {days_to_cover:.0f}"
                    ) + " — trapped shorts fuel upside",
                ))
            elif (short_pct is not None and short_pct >= 15) or (days_to_cover is not None and days_to_cover >= 8):
                signals.append(SignalResult(
                    source="short_interest", signal="moderate_squeeze_fuel",
                    strength=0.15, confidence=50,
                    reasoning="Elevated short interest — squeeze risk supports longs",
                ))
            else:
                signals.append(SignalResult(
                    source="short_interest", signal="neutral", strength=0.0, confidence=30,
                    reasoning="Short interest not elevated — no squeeze input",
                ))

        # 3. Technical / Trend Signal
        from agents.backtest.advanced_backtest import SignalEngine
        if len(closes) >= 26:
            macd_line, signal_line, histogram = SignalEngine.macd(closes)
            rsi = SignalEngine.rsi(closes)
            adx = SignalEngine.adx(highs, lows, closes) if len(highs) >= 20 else [25]

            current_rsi = rsi[-1] if rsi else 50
            current_adx = adx[-1] if adx else 25
            macd_hist = histogram[-1] if histogram else 0
            macd_cross_up = len(histogram) >= 2 and histogram[-1] > 0 and histogram[-2] <= 0
            macd_cross_down = len(histogram) >= 2 and histogram[-1] < 0 and histogram[-2] >= 0

            tech_strength = 0
            tech_reasons = []

            if macd_cross_up:
                tech_strength += 0.3
                tech_reasons.append("MACD bullish cross")
            elif macd_cross_down:
                tech_strength -= 0.3
                tech_reasons.append("MACD bearish cross")

            if current_rsi < 30:
                tech_strength += 0.4
                tech_reasons.append(f"RSI oversold ({current_rsi:.0f})")
            elif current_rsi > 70:
                tech_strength -= 0.4
                tech_reasons.append(f"RSI overbought ({current_rsi:.0f})")

            if current_adx > 25:
                tech_reasons.append(f"Strong trend (ADX {current_adx:.0f})")
            else:
                tech_reasons.append(f"Weak trend (ADX {current_adx:.0f})")

            signals.append(SignalResult(
                source="technical",
                signal="bullish" if tech_strength > 0 else "bearish" if tech_strength < 0 else "neutral",
                strength=max(-1, min(1, tech_strength)),
                confidence=min(abs(tech_strength) * 100, 80),
                reasoning=" | ".join(tech_reasons) if tech_reasons else "No strong technical signal",
            ))
        else:
            iv_signal = iv_signal

        # 4. Sideways Detection
        if self.tv and len(closes) >= 30:
            sideways = self.tv.detect_sideways(closes, highs, lows)
            signals.append(SignalResult(
                source="sideways",
                signal="sideways" if sideways["is_sideways"] else "trending",
                strength=0.3 if sideways["is_sideways"] else -0.1,
                confidence=sideways["confidence"],
                reasoning=sideways["reasoning"],
            ))
        else:
            sideways = {"is_sideways": False, "confidence": 0}

        # 5. Sentiment (PCR Z-Score)
        if self.tv and pcr_data:
            current_pcr = pcr_data.get("current", 1.0)
            historical_pcrs = pcr_data.get("historical", [])
            pcr_signal = self.tv.put_call_ratio_sentiment(current_pcr, historical_pcrs)
            signals.append(SignalResult(
                source="sentiment",
                signal=pcr_signal["signal"],
                strength=0.6 if "bullish" in pcr_signal["signal"] else -0.6 if "bearish" in pcr_signal["signal"] else 0,
                confidence=pcr_signal["confidence"],
                reasoning=pcr_signal["reasoning"],
            ))
        else:
            pcr_signal = {"signal": "neutral", "confidence": 30}

        # 6. Flow / Unusual Activity
        if flow_data and flow_data.get("total_signals", 0) > 0:
            bias = flow_data.get("bias", "neutral")
            total_prem = flow_data.get("total_premium_bull", 0) - flow_data.get("total_premium_bear", 0)
            # Normalize by stock price: $500K for a $500 stock is 1000 shares,
            # while $500K for a $50 stock is 10000 shares. Using stock_price
            # as the normalizer makes the metric symbol-agnostic.
            flow_norm = max(stock_price * 1000, 100_000)
            flow_strength = max(-1, min(1, total_prem / flow_norm))
            signals.append(SignalResult(
                source="flow",
                signal=bias,
                strength=flow_strength,
                confidence=min(flow_data.get("total_signals", 0) * 15, 90),
                reasoning=f"Flow: {flow_data.get('bullish_signals', 0)} bullish, {flow_data.get('bearish_signals', 0)} bearish signals",
            ))

        # 7. GEX Regime
        if gex_data:
            gex_regime = str(gex_data.get("regime", gex_data.get("gex_regime", "neutral"))).lower()
            if gex_regime in ("high_positive", "high_positive_gex"):
                signals.append(SignalResult(
                    source="gex", signal="stabilizing",
                    strength=0.2, confidence=60,
                    reasoning="High positive GEX → dealers stabilize markets → sell premium",
                ))
            elif gex_regime in ("high_negative", "high_negative_gex"):
                signals.append(SignalResult(
                    source="gex", signal="destabilizing",
                    strength=-0.3, confidence=70,
                    reasoning="High negative GEX → dealers amplify moves → be cautious selling",
                ))
            else:
                signals.append(SignalResult(
                    source="gex", signal="neutral",
                    strength=0.0, confidence=30,
                    reasoning="GEX is near neutral; no dealer-positioning edge",
                ))
        else:
            gex_data = {"regime": "neutral"}

        # === COMPOSITE SCORING ===
        weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS["neutral"])
        composite_score = 0
        total_weight = 0
        all_signal_dicts = []

        for sig in signals:
            w = weights.get(sig.source, 0.1) * sig.confidence / 100
            composite_score += sig.strength * w * 100
            total_weight += w
            all_signal_dicts.append({
                "source": sig.source,
                "signal": sig.signal,
                "strength": round(sig.strength, 3),
                "confidence": round(sig.confidence, 1),
                "weight": round(w, 3),
                "reasoning": sig.reasoning,
            })

        if total_weight > 0:
            composite_score /= total_weight

        # Determine overall signal
        overall_score = max(-100, min(100, composite_score))
        if overall_score > 50:
            overall_signal = SignalStrength.STRONG_BUY
        elif overall_score > 25:
            overall_signal = SignalStrength.BUY
        elif overall_score > 10:
            overall_signal = SignalStrength.WEAK_BUY
        elif overall_score > -10:
            overall_signal = SignalStrength.NEUTRAL
        elif overall_score > -25:
            overall_signal = SignalStrength.WEAK_SELL
        elif overall_score > -50:
            overall_signal = SignalStrength.SELL
        else:
            overall_signal = SignalStrength.STRONG_SELL

        # Signal agreement: average confidence of the *informative* signals
        # only. Neutral reads (strength ~0) are data-absence, not agreement, so
        # including them was dragging every symbol below the floor regardless
        # of a real directional edge. With no informative signal at all the
        # confidence is pinned below the strategy floor (fail-closed: no edge,
        # no strategy).
        informative = [s for s in signals if abs(s.strength) >= self.INFORMATIVE_STRENGTH_EPS]
        if informative:
            avg_confidence = sum(s.confidence for s in informative) / len(informative)
        else:
            avg_confidence = 35.0

        # === STRATEGY SELECTION (with portfolio context) ===
        existing_positions = (portfolio_context or {}).get("existing_positions", [])

        best_strategy = self._select_best_strategy(
            overall_signal, regime, iv_signal, sideways, vix, days_to_earnings,
            symbol=symbol, existing_positions=existing_positions, confidence=avg_confidence,
            trend=trend, relative_strength=relative_strength,
            days_to_macro=days_to_macro,
        )
        skew_note = self._skew_reasoning(iv_signal)
        if skew_note:
            best_strategy = dict(best_strategy)
            best_strategy["reasoning"] = f"{best_strategy['reasoning']} | {skew_note}"

        # === TIME-HORIZON RECOMMENDATIONS ===
        recommendations_1w = self._horizon_recommendations(
            symbol, stock_price, option_chain, TimeHorizon.SWING_1W,
            overall_signal, regime, iv_signal, vix
        )
        recommendations_1m = self._horizon_recommendations(
            symbol, stock_price, option_chain, TimeHorizon.MONTHLY_1M,
            overall_signal, regime, iv_signal, vix
        )
        recommendations_3m = self._horizon_recommendations(
            symbol, stock_price, option_chain, TimeHorizon.QUARTERLY_3M,
            overall_signal, regime, iv_signal, vix
        )
        recommendations_6m = self._horizon_recommendations(
            symbol, stock_price, option_chain, TimeHorizon.LEAPS_6M,
            overall_signal, regime, iv_signal, vix
        )

        # Build portfolio warnings
        portfolio_warnings = []
        for pos in existing_positions:
            if pos.get("symbol", "").upper() == symbol.upper():
                portfolio_warnings.append(
                    f"Already have {pos.get('strategy', 'position')} on {symbol} — "
                    f"consider closing or rolling before opening new"
                )
        if len(existing_positions) >= 8:
            portfolio_warnings.append(
                f"Portfolio has {len(existing_positions)} positions — near capacity limit"
            )

        return BrainOutput(
            symbol=symbol,
            stock_price=stock_price,
            overall_signal=overall_signal,
            overall_score=round(overall_score, 1),
            confidence=round(avg_confidence, 1),
            recommendations_1w=recommendations_1w,
            recommendations_1m=recommendations_1m,
            recommendations_3m=recommendations_3m,
            recommendations_6m=recommendations_6m,
            best_strategy=best_strategy["strategy"],
            best_strategy_reasoning=best_strategy["reasoning"],
            regime=regime,
            cpr_signal=cpr_signal,
            iv_signal=iv_signal,
            sentiment_signal=pcr_signal,
            sideways_signal=sideways,
            all_signals=all_signal_dicts,
            portfolio_warnings=portfolio_warnings,
            relative_strength=relative_strength,
        )

    def _detect_regime(self, vix: float, iv: float, hv: float, trend: str = "neutral") -> str:
        """Detect current market regime from vol inputs and trend direction.

        When vol is normal (not high/low), the regime reflects directional
        bias from technical trend, making "bullish"/"bearish" weight profiles
        reachable instead of dead code.
        """
        if vix >= 25 or (hv > 0 and iv / hv >= 1.5):
            return "high_vol"
        if vix <= 15:
            return "low_vol"
        if trend in ("bullish", "bearish"):
            return trend
        return "neutral"

    def _skew_reasoning(self, iv_signal: Dict) -> str:
        """Short desk read on the surface shape, appended to strategy reasoning."""
        skew = iv_signal.get("iv_skew") if isinstance(iv_signal, dict) else None
        if not skew:
            return ""
        regime = skew.get("regime")
        rr = skew.get("rr25_norm")
        if regime == "fear":
            return f"put skew extreme (RR25 {rr}) — rich downside hedges, enhanced put credit"
        if regime == "elevated_fear":
            return f"elevated put skew (RR25 {rr}) — put premium rich"
        if regime == "complacent":
            return f"flat skew (RR25 {rr}) — no fear priced, premium thin, size down"
        return ""

    def _select_best_strategy(
        self,
        signal: SignalStrength,
        regime: str,
        iv_signal: Dict,
        sideways: Dict,
        vix: float,
        days_to_earnings: Optional[int],
        symbol: str = "",
        existing_positions: List[Dict] = None,
        confidence: float = 0,
        trend: str = "neutral",
        relative_strength: float = None,
        days_to_macro: Optional[int] = None,
    ) -> Dict[str, str]:
        """Select the best strategy based on all signals + portfolio context.

        Gate order is deliberate: the market-wide vetoes (earnings proximity,
        macro-event proximity, inverted term structure, extreme VIX) fire
        before any strategy branch, so a single bad regime input can never mint
        a trade. The return dict carries a ``reason_code`` so the scanner can
        tally *why* symbols are paused without parsing prose.

        Trend alignment (IBD "trade with the market") and relative strength
        ("L" = leaders only) are hard vetoes on the *directional* branches:
        a bull structure is never minted against a confirmed downtrend, and
        directional short premium is never sold against a clear market laggard.
        The premium sell strategy is only chosen when it also passes the
        high-win-rate context gates.
        """
        # Dual-filter rank: whichever of IV Rank / IV percentile confirms an
        # elevated reading drives strategy selection (eff_iv_rank set in
        # analyze(); fall back to the raw rank for direct callers).
        ivr = iv_signal.get("eff_iv_rank", iv_signal.get("iv_rank", 50))

        # Check if already have position on this symbol
        has_position = any(
            p.get("symbol", "").upper() == symbol.upper()
            for p in (existing_positions or [])
        )
        if has_position:
            return {
                "strategy": "roll_or_close",
                "reason_code": "has_position",
                "reasoning": f"Already have a position on {symbol} — roll for credit or close for profit",
            }

        # Earnings filter — this must also pass the confidence gate so that
        # a single IVR data point on an unfamiliar symbol doesn't force a
        # blanket "no trade" without signal support.
        if days_to_earnings and days_to_earnings <= 7:
            return {
                "strategy": "avoid_new_positions",
                "reason_code": "earnings_proximity",
                "reasoning": f"Earnings in {days_to_earnings} days → too close, avoid new positions",
            }

        # Scheduled macro print (FOMC/CPI/NFP) inside the blackout window — the
        # largest scheduled overnight-vol event of its week. Unlike earnings,
        # this is market-wide: every symbol carries the same print risk, so the
        # veto fires before any strategy branch. None (unknown schedule) fails
        # open — a missing calendar never mints a veto.
        if days_to_macro is not None and 0 <= days_to_macro <= macro_calendar.MACRO_BLACKOUT_DAYS:
            macro_label = "scheduled macro event"
            try:
                event = macro_calendar.next_macro_event()
                if event:
                    macro_label = event["label"]
            except Exception:
                macro_label = macro_label
            return {
                "strategy": "no_trade",
                "reason_code": "macro_proximity",
                "reasoning": (
                    f"Macro event ({macro_label}) in {days_to_macro} day"
                    f"{'s' if days_to_macro != 1 else ''} → no new positions "
                    "through the print"
                ),
            }

        # Inverted VIX term structure = front-month fear. Selling premium into
        # an inverted curve is the classic premium-capture mistake; pause all
        # short-vega strategies until the curve re-steepens (Option Alpha).
        if iv_signal.get("term_structure") == "inverted":
            return {
                "strategy": "no_trade",
                "reason_code": "inverted_term_structure",
                "reasoning": "VIX term structure inverted → pause new premium selling until curve re-steepens",
            }

        if confidence < self.MIN_STRATEGY_CONFIDENCE:
            return {
                "strategy": "no_trade",
                "reason_code": "low_confidence",
                "reasoning": f"Signal agreement is only {confidence:.0f}% — insufficient confirmation",
            }

        # High IV + Sideways = sell premium. Iron condors are deliberately
        # limited to their documented moderate-volatility range (OptionsPilot
        # finds the 18-28 VIX zone where ICs win most).
        if ivr >= 50 and sideways.get("is_sideways", False) and 18 <= vix <= 28:
            return {
                "strategy": "iron_condor",
                "reason_code": "iron_condor",
                "reasoning": f"IVR {ivr:.0f} + sideways market → iron condor captures theta from both sides",
            }

        # Moderate-to-high IV + Trending = directional credit spread. The 40
        # floor mirrors the ROT open-source scanner, which gives bull put /
        # bear call spreads a boost in the 35-50 moderate zone instead of
        # waiting for rich (>50) IVR and leaving that band empty.
        if ivr >= 40 and not sideways.get("is_sideways", False):
            if signal in [SignalStrength.BUY, SignalStrength.STRONG_BUY, SignalStrength.WEAK_BUY]:
                if trend == "bearish":
                    return {
                        "strategy": "no_trade",
                        "reason_code": "trend_mismatch",
                        "reasoning": f"Bullish signal but {symbol} is in a confirmed downtrend — do not sell puts into the knife",
                    }
                rs_ok, rs_reason = hw_relative_strength_ok("bull_put", relative_strength)
                if not rs_ok:
                    return {
                        "strategy": "no_trade",
                        "reason_code": "laggard",
                        "reasoning": f"{symbol}: {rs_reason}",
                    }
                return {
                    "strategy": "bull_put_credit",
                    "reason_code": "bull_put_credit",
                    "reasoning": f"IVR {ivr:.0f} + bullish trend → bull put credit spread captures premium + direction",
                }
            elif signal in [SignalStrength.SELL, SignalStrength.STRONG_SELL, SignalStrength.WEAK_SELL]:
                if trend == "bullish":
                    return {
                        "strategy": "no_trade",
                        "reason_code": "trend_mismatch",
                        "reasoning": f"Bearish signal but {symbol} is in a confirmed uptrend — do not sell calls into strength",
                    }
                rs_ok, rs_reason = hw_relative_strength_ok("bear_call", relative_strength)
                if not rs_ok:
                    return {
                        "strategy": "no_trade",
                        "reason_code": "laggard",
                        "reasoning": f"{symbol}: {rs_reason}",
                    }
                return {
                    "strategy": "bear_call_credit",
                    "reason_code": "bear_call_credit",
                    "reasoning": f"IVR {ivr:.0f} + bearish trend → bear call credit spread captures premium + direction",
                }

        # Low IV + Bullish = buy options
        if ivr < 30:
            if signal in [SignalStrength.BUY, SignalStrength.STRONG_BUY]:
                if trend == "bearish":
                    return {
                        "strategy": "no_trade",
                        "reason_code": "trend_mismatch",
                        "reasoning": f"Bullish signal but {symbol} is in a confirmed downtrend — no call debit",
                    }
                return {
                    "strategy": "call_debit_spread",
                    "reason_code": "call_debit_spread",
                    "reasoning": f"IVR {ivr:.0f} + bullish → debit spread limits cost in low-IV environment",
                }
            elif signal in [SignalStrength.SELL, SignalStrength.STRONG_SELL]:
                if trend == "bullish":
                    return {
                        "strategy": "no_trade",
                        "reason_code": "trend_mismatch",
                        "reasoning": f"Bearish signal but {symbol} is in a confirmed uptrend — no put debit",
                    }
                return {
                    "strategy": "put_debit_spread",
                    "reason_code": "put_debit_spread",
                    "reasoning": f"IVR {ivr:.0f} + bearish → debit spread captures downside",
                }

        # A VIX spike does not itself authorize selling puts. Cash-secured
        # puts require an explicit assignment-approved underlying list, which
        # is outside this generic Brain input.
        if vix > 30:
            return {
                "strategy": "no_trade",
                "reason_code": "high_vix",
                "reasoning": f"VIX {vix:.0f} is extreme; wait for a directional and volatility confirmation",
            }

        # Absence of edge is a valid decision. A default wheel recommendation
        # can imply permission to sell puts on an underlying the user may not
        # want to own, so it must never be the automatic fallback.
        return {
            "strategy": "no_trade",
            "reason_code": "no_edge",
            "reasoning": "No strategy has a sufficiently differentiated edge in the current regime",
        }

    def _horizon_recommendations(
        self,
        symbol: str,
        stock_price: float,
        option_chain: List[Dict],
        horizon: TimeHorizon,
        signal: SignalStrength,
        regime: str,
        iv_signal: Dict,
        vix: float,
    ) -> List[Dict]:
        """Generate recommendations for a specific time horizon."""
        strategies = self.HORIZON_STRATEGIES.get(horizon, [])
        ivr = iv_signal.get("eff_iv_rank", iv_signal.get("iv_rank", 50))

        recs = []
        for strat in strategies:
            rec = {
                "strategy": strat,
                "horizon": horizon.value,
                "suitability": self._strategy_suitability(strat, signal, regime, ivr, vix),
                "typical_dte": self._typical_dte(horizon),
                "typical_delta": self._typical_delta(strat),
            }
            if rec["suitability"] >= 60:
                recs.append(rec)

        return sorted(recs, key=lambda x: x["suitability"], reverse=True)[:3]

    def _strategy_suitability(
        self, strategy: str, signal: SignalStrength, regime: str, ivr: float, vix: float,
    ) -> float:
        """Score strategy suitability 0-100."""
        score = 50

        # IV-based
        premium_selling = strategy in [
            "iron_condor", "bull_put_credit", "bear_call_credit",
            "cash_secured_put", "covered_call", "wheel",
            "short_straddle", "short_strangle", "iron_butterfly",
            "iron_condor_weekly", "credit_spread_weekly",
        ]
        if premium_selling and ivr >= 50:
            score += 20
        elif premium_selling and ivr < 30:
            score -= 20
        elif not premium_selling and ivr < 30:
            score += 15

        # Direction
        bullish_strats = ["bull_put_credit", "call_debit_spread", "long_call", "poor_mans_covered_call"]
        bearish_strats = ["bear_call_credit", "put_debit_spread", "long_put"]
        if strategy in bullish_strats and signal in [SignalStrength.BUY, SignalStrength.STRONG_BUY]:
            score += 15
        elif strategy in bearish_strats and signal in [SignalStrength.SELL, SignalStrength.STRONG_SELL]:
            score += 15

        # Regime
        if regime == "high_vol" and strategy in ["iron_condor", "short_strangle", "cash_secured_put"]:
            score += 10

        return max(0, min(100, score))

    def _typical_dte(self, horizon: TimeHorizon) -> str:
        """Typical DTE range for horizon."""
        return {
            TimeHorizon.SWING_1W: "0-7 DTE",
            TimeHorizon.MONTHLY_1M: "30-45 DTE",
            TimeHorizon.QUARTERLY_3M: "60-90 DTE",
            TimeHorizon.LEAPS_6M: "180+ DTE",
        }.get(horizon, "30-45 DTE")

    def _typical_delta(self, strategy: str) -> str:
        """Typical delta for strategy."""
        delta_map = {
            "iron_condor": "16 delta shorts",
            "bull_put_credit": "16-30 delta short put",
            "bear_call_credit": "16-30 delta short call",
            "cash_secured_put": "20-40 delta",
            "covered_call": "20-30 delta",
            "wheel": "30-40 delta",
            "short_straddle": "ATM (50 delta)",
            "short_strangle": "16 delta",
            "call_debit_spread": "40-60 delta long",
            "put_debit_spread": "40-60 delta long",
            "long_call": "40-60 delta",
            "long_put": "40-60 delta",
            "calendar_spread": "ATM short, ATM+ long",
            "leaps": "70-80 delta long",
            "poor_mans_covered_call": "70-80 delta long call",
        }
        return delta_map.get(strategy, "ATM")
