"""
Portfolio risk limits and circuit breakers.
Adapted from ROT architecture and Option Alpha safeguards.
"""
import os
from datetime import datetime, date

class RiskManager:
    def __init__(self):
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", 15.0))
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PCT", 50.0))
        self.max_position_risk_pct = float(os.getenv("MAX_POSITION_RISK_PCT", 2.0))
        self.max_portfolio_delta = 0.20  # 20%
        self.max_portfolio_vega = 0.05   # 5%
        
        self.daily_start_equity = 0.0
        self.peak_equity = 0.0
        self.is_halted = False
        self.halt_reason = ""

    def set_start_equity(self, equity: float):
        self.daily_start_equity = equity
        if self.peak_equity == 0.0:
            self.peak_equity = equity

    def check_daily_loss(self, current_equity: float) -> bool:
        """Returns True if daily loss limit is breached."""
        if self.daily_start_equity == 0:
            return False
        
        daily_pnl_pct = ((current_equity - self.daily_start_equity) / self.daily_start_equity) * 100
        
        if daily_pnl_pct <= -self.max_daily_loss_pct:
            self.is_halted = True
            self.halt_reason = f"Daily loss limit breached: {daily_pnl_pct:.2f}%"
            return True
        return False

    def check_drawdown(self, current_equity: float) -> bool:
        """Returns True if max drawdown limit is breached."""
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        
        drawdown_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100
        
        if drawdown_pct >= self.max_drawdown_pct:
            self.is_halted = True
            self.halt_reason = f"Max drawdown breached: {drawdown_pct:.2f}%"
            return True
        return False

    def check_portfolio_greeks(self, net_delta: float, net_vega: float) -> bool:
        """Returns True if Greeks limits are breached."""
        if abs(net_delta) > self.max_portfolio_delta:
            self.is_halted = True
            self.halt_reason = f"Net Delta limit breached: {net_delta}"
            return True
        if abs(net_vega) > self.max_portfolio_vega:
            self.is_halted = True
            self.halt_reason = f"Net Vega limit breached: {net_vega}"
            return True
        return False
