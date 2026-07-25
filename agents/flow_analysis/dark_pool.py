"""
Dark Pool Detection Engine.
Identifies institutional activity from free data sources.
Adapted from FINRA ATS data analysis and dark pool detection patterns.

Free dark pool data sources:
1. FINRA ATS data (weekly, free download)
2. IBKR time & sales (free with account)
3. Volume anomalies as dark pool proxies
4. Large block prints detection
"""
import logging
from typing import Dict, Any, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class DarkPoolDetector:
    """
    Detects dark pool and institutional activity using free data.
    Uses volume anomalies, block print detection, and time & sales patterns.
    """

    def __init__(self):
        self.block_size_threshold = 500  # Minimum contracts for block
        self.volume_spike_threshold = 2.0  # 2x average volume
        self.dark_pool_volume_pct_threshold = 0.4  # 40% of volume in dark pools

    def analyze_volume_anomaly(
        self,
        current_volume: int,
        avg_volume_20d: int,
        current_oi: int,
        prev_oi: int,
    ) -> Dict[str, Any]:
        """
        Detect unusual volume patterns that suggest dark pool activity.

        Key signals:
        - Volume >> 20-day average
        - OI changes that don't match visible volume
        - Volume/Price divergence
        """
        vol_ratio = current_volume / max(avg_volume_20d, 1)
        oi_change = current_oi - prev_oi

        # If volume is high but OI didn't change much, likely dark pool prints
        # (dark pool trades don't always update OI in real-time)
        dark_pool_signal = False
        confidence = 0.0

        if vol_ratio > self.volume_spike_threshold:
            # High volume with low OI change = likely dark pool
            if abs(oi_change) < current_volume * 0.3:
                dark_pool_signal = True
                confidence = min(vol_ratio / 5.0, 1.0)
            # High volume with OI increase = new institutional positions
            elif oi_change > 0:
                dark_pool_signal = True
                confidence = min(oi_change / max(current_volume, 1), 1.0)

        return {
            "dark_pool_signal": dark_pool_signal,
            "volume_ratio": round(vol_ratio, 2),
            "oi_change": oi_change,
            "confidence": round(confidence, 3),
            "signal_type": (
                "DARK_POOL_PRINT" if dark_pool_signal and abs(oi_change) < current_volume * 0.3
                else "INSTITUTIONAL_BUILDUP" if dark_pool_signal
                else "NORMAL"
            ),
        }

    def detect_block_prints(
        self, trades: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Detect large block trades that indicate institutional activity.
        Block trades: >= 500 contracts or >= $100K premium.
        """
        blocks = []
        for trade in trades:
            quantity = trade.get("quantity", 0)
            price = trade.get("price", 0)
            premium = quantity * price * 100

            is_block = (
                quantity >= self.block_size_threshold
                or premium >= 100_000
            )

            if is_block:
                blocks.append({
                    "type": "BLOCK_PRINT",
                    "quantity": quantity,
                    "price": price,
                    "premium": premium,
                    "option_type": trade.get("option_type", "UNKNOWN"),
                    "strike": trade.get("strike", 0),
                    "expiry": trade.get("expiry", ""),
                    "institutional": True,
                })

        return blocks

    def analyze_dark_pool_prints(self, prints: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze a batch of dark pool prints for directional bias.

        Dark pool prints are off-exchange trades that indicate institutional activity.
        Without direct OPRA exchange data, we use volume and OI as proxies.
        """
        if not prints:
            return {"bias": "NEUTRAL", "total_prints": 0, "total_premium": 0}

        total_call_premium = 0
        total_put_premium = 0
        total_prints = len(prints)

        for p in prints:
            premium = p.get("premium", 0)
            if p.get("option_type") == "CALL":
                total_call_premium += premium
            else:
                total_put_premium += premium

        total_premium = total_call_premium + total_put_premium
        if total_premium == 0:
            return {"bias": "NEUTRAL", "total_prints": total_prints, "total_premium": 0}

        # Call/Put premium ratio
        cp_ratio = total_call_premium / max(total_put_premium, 1)

        # Determine bias
        if cp_ratio > 1.5:
            bias = "BULLISH"
        elif cp_ratio < 0.67:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            "bias": bias,
            "call_premium": round(total_call_premium, 2),
            "put_premium": round(total_put_premium, 2),
            "cp_ratio": round(cp_ratio, 3),
            "total_prints": total_prints,
            "total_premium": round(total_premium, 2),
        }

    def estimate_dark_pool_volume(
        self, total_volume: int, exchange_volume: int
    ) -> Dict[str, Any]:
        """
        Estimate dark pool volume from total vs exchange-reported volume.
        FINRA reports off-exchange volume weekly.
        """
        dark_volume = max(total_volume - exchange_volume, 0)
        dark_pct = dark_volume / max(total_volume, 1) * 100

        return {
            "total_volume": total_volume,
            "exchange_volume": exchange_volume,
            "estimated_dark_volume": dark_volume,
            "dark_pool_pct": round(dark_pct, 1),
            "elevated_dark": dark_pct > 40,  # Above 40% is unusual
        }
