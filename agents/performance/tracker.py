"""
Performance Tracker Agent.
Adapted from Kinfo for automatic spread detection and grouped PnL reporting.
Tracks Sharpe Ratio, Win Rate, and Max Drawdown.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class PerformanceTracker:
    def __init__(self):
        self.trades: List[Dict[str, Any]] = []
        self.daily_pnl: List[float] = []

    def record_trade(self, trade: Dict[str, Any]):
        """Record a completed trade or spread."""
        self.trades.append(trade)
        logger.info(f"Trade recorded: {trade}")

    def calculate_sharpe_ratio(self, risk_free_rate: float = 0.05) -> float:
        """Calculate annualized Sharpe Ratio."""
        if not self.daily_pnl or len(self.daily_pnl) < 2:
            return 0.0
        
        import numpy as np
        returns = np.array(self.daily_pnl)
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
            
        sharpe = (mean_return - risk_free_rate) / std_return
        return float(sharpe * np.sqrt(252))  # Annualize

    def calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown percentage."""
        if not self.daily_pnl:
            return 0.0
        
        cumulative = [0.0]
        for pnl in self.daily_pnl:
            cumulative.append(cumulative[-1] + pnl)
        
        peak = cumulative[0]
        max_dd = 0.0
        for value in cumulative:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak != 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        return max_dd * 100

    def get_win_rate(self) -> float:
        """Calculate win rate based on recorded trades."""
        if not self.trades:
            return 0.0
        
        wins = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
        return (wins / len(self.trades)) * 100
