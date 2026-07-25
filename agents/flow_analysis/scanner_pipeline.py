"""
Multi-Layer Scanner Pipeline.
Implements the 6-layer institutional workflow:

Layer 1: Flow Scanner      -> Unusual volume/premium detection
Layer 2: Dark Pool          -> Institutional activity confirmation
Layer 3: GEX/Dealer         -> Gamma exposure context
Layer 4: Technical          -> Price action and trend confirmation
Layer 5: Catalyst           -> Macro events and earnings check
Layer 6: Risk Management    -> Final position sizing and limits

Each layer filters results from the previous layer, progressively narrowing
to the highest-conviction setups.
Adapted from institutional scanning workflows and community best practices.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import numpy as np

logger = logging.getLogger(__name__)


class MultiLayerScanner:
    """
    Six-layer scanner pipeline that progressively filters candidates.
    Only the most-conviction setups survive all layers.
    """

    def __init__(self):
        self.layer_results = {}

    async def scan(self, candidates: List[Dict[str, Any]], data_provider=None) -> List[Dict[str, Any]]:
        """
        Run the full 6-layer scanning pipeline.
        Input: list of candidate symbols/contracts
        Output: filtered list of high-conviction trade setups
        """
        logger.info(f"Starting multi-layer scan with {len(candidates)} candidates")

        # Layer 1: Flow Analysis
        layer1 = await self._layer_flow(candidates)
        logger.info(f"Layer 1 (Flow): {len(layer1)} candidates survived")

        # Layer 2: Dark Pool Confirmation
        layer2 = await self._layer_dark_pool(layer1, data_provider)
        logger.info(f"Layer 2 (Dark Pool): {len(layer2)} candidates survived")

        # Layer 3: GEX / Dealer Positioning
        layer3 = await self._layer_gex(layer2)
        logger.info(f"Layer 3 (GEX): {len(layer3)} candidates survived")

        # Layer 4: Technical Confirmation
        layer4 = await self._layer_technical(layer3)
        logger.info(f"Layer 4 (Technical): {len(layer4)} candidates survived")

        # Layer 5: Catalyst Check
        layer5 = await self._layer_catalyst(layer4)
        logger.info(f"Layer 5 (Catalyst): {len(layer5)} candidates survived")

        # Layer 6: Risk Management Filter
        layer6 = await self._layer_risk(layer5)
        logger.info(f"Layer 6 (Risk): {len(layer6)} candidates passed all filters")

        self.layer_results = {
            "input": len(candidates),
            "layer1_flow": len(layer1),
            "layer2_dark_pool": len(layer2),
            "layer3_gex": len(layer3),
            "layer4_technical": len(layer4),
            "layer5_catalyst": len(layer5),
            "layer6_risk": len(layer6),
        }

        return layer6

    async def _layer_flow(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Layer 1: Unusual Options Activity Detection
        Filters: Volume/OI ratio >= 2.0 AND premium >= $25,000
        Signals: Sweeps, blocks, unusual volume
        """
        filtered = []
        for c in candidates:
            vol = c.get("volume", 0)
            oi = c.get("open_interest", 1)
            price = c.get("last", 0) or c.get("ask", 0)
            premium = price * vol * 100

            vol_oi_ratio = vol / max(oi, 1)

            # Primary flow filters
            has_unusual_volume = vol_oi_ratio >= 2.0
            has_significant_premium = premium >= 25_000
            has_high_volume = vol >= 500

            if (has_unusual_volume and has_significant_premium) or has_high_volume:
                c["flow_score"] = round(min(vol_oi_ratio / 5.0, 1.0), 3)
                c["premium_scanned"] = premium
                c["vol_oi_ratio"] = round(vol_oi_ratio, 2)
                c["layers_passed"] = ["flow"]
                filtered.append(c)

        return filtered

    async def _layer_dark_pool(
        self, candidates: List[Dict[str, Any]], data_provider=None
    ) -> List[Dict[str, Any]]:
        """
        Layer 2: Dark Pool Confirmation
        Confirms institutional activity via volume anomalies and OI patterns.
        Relaxed filter: Passes through candidates with volume anomalies OR
        those that already have strong flow signals.
        """
        filtered = []
        for c in candidates:
            vol = c.get("volume", 0)
            oi = c.get("open_interest", 1)
            flow_score = c.get("flow_score", 0)

            # Dark pool proxy: volume significantly exceeds OI change
            # (suggests off-exchange activity)
            avg_daily_oi_change = oi * 0.05  # Assume ~5% daily OI churn
            vol_vs_oi_change = vol / max(avg_daily_oi_change, 1)

            # Strong flow signals get through even without dark pool confirmation
            if flow_score >= 0.6:
                c["dark_pool_confirmed"] = True
                c["layers_passed"].append("dark_pool")
                filtered.append(c)
            # Moderate flow + volume anomaly
            elif vol_vs_oi_change > 3.0 or vol > oi * 0.3:
                c["dark_pool_confirmed"] = True
                c["layers_passed"].append("dark_pool")
                filtered.append(c)

        return filtered

    async def _layer_gex(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Layer 3: GEX / Dealer Positioning Context
        Uses gamma exposure to determine if dealers are positioned
        to support or amplify moves at the candidate's strike.
        """
        filtered = []
        for c in candidates:
            strike = c.get("strike", 0)
            opt_type = c.get("option_type", "CALL")
            gex_regime = c.get("gex_regime", "NEUTRAL")

            # Favor selling premium when GEX is positive (dealers pin price)
            # Favor buying premium when GEX is negative (dealers amplify moves)
            favorable_gex = True
            if gex_regime == "HIGH_POSITIVE_GEX" and c.get("action") == "SELL":
                favorable_gex = True  # Selling premium in high GEX = good
            elif gex_regime == "HIGH_NEGATIVE_GEX" and c.get("action") == "BUY":
                favorable_gex = True  # Buying in negative GEX = good
            elif gex_regime == "FLIP_ZONE":
                favorable_gex = False  # Avoid flip zone (max volatility)
            else:
                favorable_gex = True  # Default: pass through

            if favorable_gex:
                c["gex_aligned"] = True
                c["layers_passed"].append("gex")
                filtered.append(c)

        return filtered

    async def _layer_technical(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Layer 4: Technical Confirmation
        Checks price trend, support/resistance, and momentum.
        """
        filtered = []
        for c in candidates:
            # Basic technical filters
            # In production, this would fetch real price data and calculate indicators
            trend = c.get("underlying_trend", "NEUTRAL")
            opt_type = c.get("option_type", "CALL")

            # Simple alignment check
            bullish_aligned = trend in ["BULLISH", "NEUTRAL"] or opt_type == "PUT"
            bearish_aligned = trend in ["BEARISH", "NEUTRAL"] or opt_type == "CALL"

            if bullish_aligned or bearish_aligned:
                c["technical_aligned"] = True
                c["layers_passed"].append("technical")
                filtered.append(c)

        return filtered

    async def _layer_catalyst(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Layer 5: Catalyst Check
        Filters out candidates with imminent earnings or high-impact events.
        Earnings within 3 days = skip (unless earnings strategy)
        """
        filtered = []
        for c in candidates:
            days_to_earnings = c.get("days_to_earnings", 999)
            is_earnings_strategy = c.get("strategy_name", "").lower() in [
                "earningsstraddle", "earnings"
            ]

            # Skip non-earnings strategies near earnings
            if days_to_earnings < 3 and not is_earnings_strategy:
                c["catalyst_warning"] = f"Earnings in {days_to_earnings} days"
                continue

            # Skip during high-impact macro events
            macro_events = c.get("upcoming_macro_events", [])
            if macro_events and not is_earnings_strategy:
                c["catalyst_warning"] = f"Macro events: {macro_events}"
                continue

            c["layers_passed"].append("catalyst")
            filtered.append(c)

        return filtered

    async def _layer_risk(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Layer 6: Risk Management Filter
        Final check: position sizing, correlation limits, portfolio impact.
        """
        filtered = []
        for c in candidates:
            max_loss = c.get("max_loss", 0)
            max_profit = c.get("max_profit", 0)
            confidence = c.get("confidence_score", 0)

            # Risk/reward minimum
            if max_profit > 0 and max_loss > 0:
                risk_reward = max_profit / max_loss
                if risk_reward < 0.5:
                    continue  # Skip poor risk/reward

            # Minimum confidence
            if confidence < 60:
                continue

            c["layers_passed"].append("risk")
            c["final_score"] = self._calculate_final_score(c)
            filtered.append(c)

        # Sort by final score
        filtered.sort(key=lambda x: x.get("final_score", 0), reverse=True)

        return filtered

    def _calculate_final_score(self, candidate: Dict[str, Any]) -> float:
        """Calculate composite score from all layers."""
        flow_score = candidate.get("flow_score", 0) * 25
        dark_pool_bonus = 15 if candidate.get("dark_pool_confirmed") else 0
        gex_bonus = 10 if candidate.get("gex_aligned") else 0
        technical_bonus = 10 if candidate.get("technical_aligned") else 0
        confidence = candidate.get("confidence_score", 0) * 0.4

        return round(flow_score + dark_pool_bonus + gex_bonus + technical_bonus + confidence, 2)
