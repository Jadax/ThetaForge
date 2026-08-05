"""Greeks calculation engine.

Thin adapter over the authoritative Black-Scholes engine so callers get a
stable, dependency-light API for option Greeks. Falls back to zeros on any
pricing error (fail-closed).
"""
import logging
from typing import Dict

from agents.volatility.black_scholes import BlackScholes, OptionType

logger = logging.getLogger(__name__)


def calculate_greeks(
    flag: str,  # 'c' or 'p'
    S: float,   # Underlying price
    K: float,   # Strike price
    t: float,   # Time to expiration (in years)
    r: float,   # Risk-free rate
    sigma: float,  # Implied volatility
) -> Dict[str, float]:
    """Calculate option Greeks using the Black-Scholes engine."""
    try:
        option_type = OptionType.CALL if str(flag).lower() == "c" else OptionType.PUT
        result = BlackScholes.price(S=S, K=K, T=t, r=r, sigma=sigma, option_type=option_type)
        return {
            "delta": result.delta,
            "gamma": result.gamma,
            "theta": result.theta,
            "vega": result.vega,
            "rho": result.rho,
        }
    except Exception as exc:  # noqa: BLE001 - fail closed to zero Greeks
        logger.error("Error calculating Greeks: %s", exc)
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}
