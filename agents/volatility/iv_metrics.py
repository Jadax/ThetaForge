"""
IV Rank and IV Percentile Calculation.
Adapted from Wheel Screener for dual-view volatility analysis.
IV Rank: (Current IV - 52-week Low) / (52-week High - 52-week Low)
IV Percentile: % of days with lower IV over the past year.
"""
import logging
import numpy as np
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def calculate_iv_rank(current_iv: float, iv_history: List[float]) -> float:
    """Calculate IV Rank (0-100)."""
    if not iv_history:
        return 0.0
    min_iv = min(iv_history)
    max_iv = max(iv_history)
    if max_iv == min_iv:
        return 50.0
    return ((current_iv - min_iv) / (max_iv - min_iv)) * 100

def calculate_iv_percentile(current_iv: float, iv_history: List[float]) -> float:
    """Calculate IV Percentile (0-100)."""
    if not iv_history:
        return 0.0
    count_below = sum(1 for iv in iv_history if iv < current_iv)
    return (count_below / len(iv_history)) * 100

class IVMetricsEngine:
    def __init__(self):
        pass

    def get_metrics(self, symbol: str, current_iv: float, iv_history: List[float]) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "current_iv": current_iv,
            "iv_rank": calculate_iv_rank(current_iv, iv_history),
            "iv_percentile": calculate_iv_percentile(current_iv, iv_history)
        }
