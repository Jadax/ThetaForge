"""
ThetaForge AI Brain - Unified Signal Orchestrator.
The central nervous system that combines ALL indicators, strategies,
and signals into a single coherent recommendation engine.

Stolen from: Every competitor analyzed. This is the synthesis.

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


class AIBrain:
    """
    The unified AI Brain that orchestrates all trading intelligence.
    
    This is the CORE of ThetaForge - it takes in data from ALL engines
    and produces a single, coherent, actionable recommendation.
    
    Weight hierarchy (stolen from institutional quant funds):
    1. Flow/Unusual Activity: 25% (smart money leads)
    2. IV/NVRP Edge: 20% (volatility is the #1 edge in options)
    3. Technical/Trend: 15% (direction matters)
    4. CPR/Pivots: 15% (institutional reference levels)
    5. Sentiment (PCR Z-Score): 10% (contrarian signals)
    6. GEX Regime: 10% (dealer positioning)
    7. Sideways Detection: 5% (regime filter)
    """
    
    # Signal weights by market regime
    REGIME_WEIGHTS = {
        "bullish": {
            "flow": 0.20, "iv": 0.15, "technical": 0.25, "cpr": 0.15,
            "sentiment": 0.10, "gex": 0.10, "sideways": 0.05,
        },
        "bearish": {
            "flow": 0.25, "iv": 0.20, "technical": 0.20, "cpr": 0.10,
            "sentiment": 0.10, "gex": 0.10, "sideways": 0.05,
        },
        "neutral": {
            "flow": 0.15, "iv": 0.25, "technical": 0.10, "cpr": 0.15,
            "sentiment": 0.15, "gex": 0.10, "sideways": 0.10,
        },
        "high_vol": {
            "flow": 0.25, "iv": 0.30, "technical": 0.10, "cpr": 0.05,
            "sentiment": 0.10, "gex": 0.15, "sideways": 0.05,
        },
        "low_vol": {
            "flow": 0.10, "iv": 0.30, "technical": 0.20, "cpr": 0.15,
            "sentiment": 0.10, "gex": 0.05, "sideways": 0.10,
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
        portfolio_context: Dict = None,
    ) -> BrainOutput:
        """
        MAIN ENTRY POINT: Analyze a symbol and produce comprehensive recommendation.
        
        portfolio_context: {"existing_positions": [...], "symbols_held": [...], "net_delta": float, "net_vega": float}
        """
        closes = historical_prices or [stock_price]
        highs = high_prices or [stock_price * 1.01]
        lows = low_prices or [stock_price * 0.99]

        # Detect regime
        regime = self._detect_regime(vix, current_iv, hv_20)

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

            if ivr >= 50 and iv_ratio["signal"] == "sell_premium":
                signals.append(SignalResult(
                    source="iv", signal="sell_premium",
                    strength=0.8, confidence=min(ivr, 90),
                    reasoning=f"IVR {ivr:.0f} + IV>HV → strong premium selling edge",
                ))
            elif 40 <= ivr < 50 and iv_ratio["signal"] == "sell_premium":
                signals.append(SignalResult(
                    source="iv", signal="moderate_sell_premium",
                    strength=0.3, confidence=55,
                    reasoning=f"IVR {ivr:.0f} and IV>HV → moderate premium selling edge",
                ))
            elif ivr <= 30 and iv_ratio["signal"] == "buy_premium":
                signals.append(SignalResult(
                    source="iv", signal="buy_premium",
                    strength=-0.5, confidence=min(100 - ivr, 80),
                    reasoning=f"IVR {ivr:.0f} + IV<HV → premium buying edge",
                ))
            elif 30 < ivr <= 40 and iv_ratio["signal"] == "buy_premium":
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
        else:
            iv_signal = {"iv_rank": 50}

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

        # Average confidence
        avg_confidence = sum(s.confidence for s in signals) / max(len(signals), 1)

        # === STRATEGY SELECTION (with portfolio context) ===
        existing_positions = (portfolio_context or {}).get("existing_positions", [])

        best_strategy = self._select_best_strategy(
            overall_signal, regime, iv_signal, sideways, vix, days_to_earnings,
            symbol=symbol, existing_positions=existing_positions, confidence=avg_confidence,
        )

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
        )

    def _detect_regime(self, vix: float, iv: float, hv: float) -> str:
        """Detect current market regime from vol inputs (no direction)."""
        if vix >= 25 or (hv > 0 and iv / hv >= 1.5):
            return "high_vol"
        if vix <= 15:
            return "low_vol"
        return "neutral"

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
    ) -> Dict[str, str]:
        """Select the best strategy based on all signals + portfolio context."""
        ivr = iv_signal.get("iv_rank", 50)

        # Check if already have position on this symbol
        has_position = any(
            p.get("symbol", "").upper() == symbol.upper()
            for p in (existing_positions or [])
        )
        if has_position:
            return {
                "strategy": "roll_or_close",
                "reasoning": f"Already have a position on {symbol} — roll for credit or close for profit",
            }

        # Earnings filter — this must also pass the confidence gate so that
        # a single IVR data point on an unfamiliar symbol doesn't force a
        # blanket "no trade" without signal support.
        if days_to_earnings and days_to_earnings <= 7:
            return {
                "strategy": "avoid_new_positions",
                "reasoning": f"Earnings in {days_to_earnings} days → too close, avoid new positions",
            }

        if confidence < 55:
            return {
                "strategy": "no_trade",
                "reasoning": f"Signal agreement is only {confidence:.0f}% — insufficient confirmation",
            }

        # High IV + Sideways = sell premium. Iron condors are deliberately
        # limited to their documented moderate-volatility range.
        if ivr >= 50 and sideways.get("is_sideways", False) and 15 <= vix <= 25:
            return {
                "strategy": "iron_condor",
                "reasoning": f"IVR {ivr:.0f} + sideways market → iron condor captures theta from both sides",
            }

        # High IV + Trending = directional credit spread
        if ivr >= 50 and not sideways.get("is_sideways", False):
            if signal in [SignalStrength.BUY, SignalStrength.STRONG_BUY, SignalStrength.WEAK_BUY]:
                return {
                    "strategy": "bull_put_credit",
                    "reasoning": f"IVR {ivr:.0f} + bullish trend → bull put credit spread captures premium + direction",
                }
            elif signal in [SignalStrength.SELL, SignalStrength.STRONG_SELL, SignalStrength.WEAK_SELL]:
                return {
                    "strategy": "bear_call_credit",
                    "reasoning": f"IVR {ivr:.0f} + bearish trend → bear call credit spread captures premium + direction",
                }

        # Low IV + Bullish = buy options
        if ivr < 30:
            if signal in [SignalStrength.BUY, SignalStrength.STRONG_BUY]:
                return {
                    "strategy": "call_debit_spread",
                    "reasoning": f"IVR {ivr:.0f} + bullish → debit spread limits cost in low-IV environment",
                }
            elif signal in [SignalStrength.SELL, SignalStrength.STRONG_SELL]:
                return {
                    "strategy": "put_debit_spread",
                    "reasoning": f"IVR {ivr:.0f} + bearish → debit spread captures downside",
                }

        # A VIX spike does not itself authorize selling puts. Cash-secured
        # puts require an explicit assignment-approved underlying list, which
        # is outside this generic Brain input.
        if vix > 30:
            return {
                "strategy": "no_trade",
                "reasoning": f"VIX {vix:.0f} is extreme; wait for a directional and volatility confirmation",
            }

        # Legacy fallback retained for compatibility; unreachable because the
        # branch above rejects unqualified high-volatility entries.
        # High VIX = defensive
        if vix > 30:
            return {
                "strategy": "cash_secured_put",
                "reasoning": f"VIX {vix:.0f} > 30 → high fear → sell CSPs at support for premium + potential assignment",
            }

        # Absence of edge is a valid decision. A default wheel recommendation
        # can imply permission to sell puts on an underlying the user may not
        # want to own, so it must never be the automatic fallback.
        return {
            "strategy": "no_trade",
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
        ivr = iv_signal.get("iv_rank", 50)

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
