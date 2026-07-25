"""
Kelly Criterion Calculator for position sizing.
Adapted from general quantitative finance principles and ROT architecture.
Uses Half-Kelly for conservative risk management.
"""
import math

def calculate_kelly(prob_win: float, win_loss_ratio: float, use_half_kelly: bool = True) -> float:
    """
    Calculates the Kelly Criterion for optimal position sizing.
    f = (p * b - q) / b
    where:
        p = probability of win
        q = probability of loss (1 - p)
        b = win/loss ratio (avg win / avg loss)
    """
    if prob_win < 0 or prob_win > 1:
        raise ValueError("Probability must be between 0 and 1.")
    if win_loss_ratio <= 0:
        raise ValueError("Win/loss ratio must be positive.")

    p = prob_win
    q = 1 - p
    b = win_loss_ratio

    kelly_fraction = (p * b - q) / b

    if use_half_kelly:
        return kelly_fraction / 2
    
    return kelly_fraction

def calculate_position_size(
    account_equity: float,
    risk_per_trade_pct: float,
    premium_per_contract: float,
    kelly_fraction: float
) -> int:
    """
    Determines number of contracts based on risk limits and Kelly sizing.
    """
    max_risk_amount = account_equity * (risk_per_trade_pct / 100)
    risk_adjusted_amount = max_risk_amount * kelly_fraction
    
    if premium_per_contract <= 0:
        return 0
        
    num_contracts = int(risk_adjusted_amount / (premium_per_contract * 100)) # standard 100 multiplier
    return max(0, num_contracts)
