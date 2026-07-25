"""
Greeks calculation engine.
Uses py_vollib for Black-Scholes pricing and Greeks derivation.
Adapted from general options pricing theory.
"""
import logging
from typing import Dict, Any
from py_vollib.black_scholes import black_scholes
from py_vollib.black_scholes.greeks import analytical

logger = logging.getLogger(__name__)

def calculate_greeks(
    flag: str,  # 'c' or 'p'
    S: float,   # Underlying price
    K: float,   # Strike price
    t: float,   # Time to expiration (in years)
    r: float,   # Risk-free rate
    sigma: float # Implied volatility
) -> Dict[str, float]:
    """Calculate option Greeks using Black-Scholes."""
    try:
        delta = analytical.delta(flag, S, K, t, r, sigma)
        gamma = analytical.gamma(flag, S, K, t, r, sigma)
        theta = analytical.theta(flag, S, K, t, r, sigma)
        vega = analytical.vega(flag, S, K, t, r, sigma)
        rho = analytical.rho(flag, S, K, t, r, sigma)
        
        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
            "rho": rho
        }
    except Exception as e:
        logger.error(f"Error calculating Greeks: {e}")
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}
