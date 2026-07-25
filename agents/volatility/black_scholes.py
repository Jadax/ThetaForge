"""
Black-Scholes Option Pricing Engine.
Stolen from: OptionStratLib, PyOptionTrader, Vira-Kanishka/Option-Trading-Platform.

Features:
- Black-Scholes with continuous dividend yield (q)
- Full Greeks suite: Delta, Gamma, Theta, Vega, Rho, Vanna, Vomma, Charm, Color, Veta
- Barone-Adesi-Whaley American option approximation
- Implied Volatility via Newton-Raphson
- Probability of profit from break-even analysis
"""
import math
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

SQRT_2PI = math.sqrt(2 * math.pi)


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class OptionPrice:
    """Complete option pricing result."""
    price: float
    delta: float
    gamma: float
    theta: float  # per day
    vega: float   # per 1% vol change
    rho: float    # per 1% rate change
    vanna: float  # dDelta/dVol
    vomma: float  # d2Vega/dVol2 (vomma = vanna * d1 * d2 / sigma)
    charm: float  # dDelta/dTime
    color: float  # dGamma/dTime
    veta: float   # dVega/dTime
    alpha: float  # Gamma/Theta ratio (risk/reward efficiency)


class BlackScholes:
    """
    Black-Scholes pricing with dividend yield, full Greeks, and American approximation.
    Stolen from OptionStratLib (Rust) and Vira-Kanishka (Python).
    """

    @staticmethod
    def _n(x: float) -> float:
        """Standard normal PDF."""
        return math.exp(-0.5 * x * x) / SQRT_2PI

    @staticmethod
    def _N(x: float) -> float:
        """Standard normal CDF (Abramowitz & Stegun approximation)."""
        a1 = 0.254829592
        a2 = -0.284496736
        a3 = 1.421413741
        a4 = -1.453152027
        a5 = 1.061405429
        p = 0.3275911
        sign = 1 if x >= 0 else -1
        x = abs(x) / math.sqrt(2)
        t = 1.0 / (1.0 + p * x)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
        return 0.5 * (1.0 + sign * y)

    @classmethod
    def _d1(cls, S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return 0.0
        return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))

    @classmethod
    def _d2(cls, S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
        return cls._d1(S, K, T, r, q, sigma) - sigma * math.sqrt(T)

    @classmethod
    def price(
        cls,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: OptionType,
        q: float = 0.0,
    ) -> OptionPrice:
        """
        Black-Scholes price and all Greeks.

        Args:
            S: Stock price
            K: Strike price
            T: Time to expiration in years
            r: Risk-free rate (annual)
            sigma: Implied volatility (annual)
            option_type: CALL or PUT
            q: Continuous dividend yield (annual)

        Returns:
            OptionPrice with all Greeks
        """
        if T <= 0:
            if option_type == OptionType.CALL:
                intrinsic = max(S - K, 0)
            else:
                intrinsic = max(K - S, 0)
            return OptionPrice(
                price=intrinsic, delta=1.0 if option_type == OptionType.CALL else -1.0,
                gamma=0, theta=0, vega=0, rho=0,
                vanna=0, vomma=0, charm=0, color=0, veta=0, alpha=0,
            )

        sqrtT = math.sqrt(T)
        disc_r = math.exp(-r * T)
        disc_q = math.exp(-q * T)

        d1 = cls._d1(S, K, T, r, q, sigma)
        d2 = cls._d2(S, K, T, r, q, sigma)
        n_d1 = cls._n(d1)
        N_d1 = cls._N(d1)
        N_d2 = cls._N(d2)

        if option_type == OptionType.CALL:
            price = S * disc_q * N_d1 - K * disc_r * N_d2
            delta = disc_q * N_d1
            theta = (-S * disc_q * n_d1 * sigma / (2 * sqrtT)
                     - r * K * disc_r * N_d2
                     + q * S * disc_q * N_d1) / 365
            rho = K * T * disc_r * N_d2 / 100
        else:
            N_neg_d1 = cls._N(-d1)
            N_neg_d2 = cls._N(-d2)
            price = K * disc_r * N_neg_d2 - S * disc_q * N_neg_d1
            delta = disc_q * (N_d1 - 1)
            theta = (-S * disc_q * n_d1 * sigma / (2 * sqrtT)
                     + r * K * disc_r * N_neg_d2
                     - q * S * disc_q * N_neg_d1) / 365
            rho = -K * T * disc_r * N_neg_d2 / 100

        # Common Greeks
        gamma = disc_q * n_d1 / (S * sigma * sqrtT) if sigma > 0 and S > 0 else 0
        vega = S * disc_q * n_d1 * sqrtT / 100  # per 1% vol

        # Second-order Greeks
        vanna = -disc_q * n_d1 * d2 / sigma if sigma > 0 else 0
        vomma = vanna * d1 * d2 / sigma if sigma > 0 else 0

        # Charm (dDelta/dTime)
        if option_type == OptionType.CALL:
            charm = -disc_q * (
                n_d1 * (r * d2 / (sigma * sqrtT) - q)
                - n_d1 * (1 + d2 * d1) / (2 * T)
            ) / 365 if T > 0 else 0
        else:
            charm = disc_q * (
                n_d1 * (r * d2 / (sigma * sqrtT) - q)
                + n_d1 * (1 + d2 * d1) / (2 * T)
            ) / 365 if T > 0 else 0

        # Color (dGamma/dTime)
        color = (-disc_q * n_d1 / (S * sigma * sqrtT) * (
            q + (r - q) * d1 / sigma - (1 + d2) / (2 * T)
        )) / 365 if T > 0 and sigma > 0 else 0

        # Veta (dVega/dTime)
        veta = (-S * disc_q * n_d1 * sqrtT * (
            q + (r - q) * d1 / sigma - (1 + d2) / (2 * T)
        )) / 100 / 365 if T > 0 and sigma > 0 else 0

        # Alpha = Gamma/Theta ratio (risk/reward efficiency)
        alpha = gamma / abs(theta) if abs(theta) > 1e-10 else 0

        return OptionPrice(
            price=max(price, 0),
            delta=max(-1, min(1, delta)),
            gamma=gamma,
            theta=theta,
            vega=vega,
            rho=rho,
            vanna=vanna,
            vomma=vomma,
            charm=charm,
            color=color,
            veta=veta,
            alpha=alpha,
        )

    @classmethod
    def implied_volatility(
        cls,
        market_price: float,
        S: float,
        K: float,
        T: float,
        r: float,
        option_type: OptionType,
        q: float = 0.0,
        max_iterations: int = 100,
        tolerance: float = 1e-8,
    ) -> float:
        """
        Newton-Raphson implied volatility extraction.
        Stolen from PyOptionTrader.
        """
        if T <= 0 or market_price <= 0:
            return 0.0

        sigma = 0.30  # Initial guess
        for _ in range(max_iterations):
            result = cls.price(S, K, T, r, sigma, option_type, q)
            diff = result.price - market_price
            if abs(diff) < tolerance:
                return sigma
            vega = result.vega * 100  # Convert from per-1% to per-unit
            if abs(vega) < 1e-12:
                break
            sigma -= diff / vega
            sigma = max(0.001, min(5.0, sigma))
        return sigma

    @classmethod
    def probability_of_profit(
        cls,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: OptionType,
        q: float = 0.0,
        breakeven: float = None,
    ) -> float:
        """
        Probability that the option finishes ITM (or at breakeven).
        Uses Black-Scholes terminal distribution.
        """
        if T <= 0:
            if option_type == OptionType.CALL:
                return 100.0 if S > K else 0.0
            else:
                return 100.0 if S < K else 0.0

        target = breakeven if breakeven else K
        sigma_sqrt_T = sigma * math.sqrt(T)
        if sigma_sqrt_T <= 0:
            return 50.0

        d = (math.log(S / target) + (r - q - 0.5 * sigma ** 2) * T) / sigma_sqrt_T

        if option_type == OptionType.CALL:
            return cls._N(d) * 100
        else:
            return cls._N(-d) * 100

    @classmethod
    def american_price(
        cls,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: OptionType,
        q: float = 0.0,
    ) -> float:
        """
        Barone-Adesi-Whaley American option approximation.
        Stolen from OptionStratLib.
        """
        # American call on non-dividend stock = European call
        if option_type == OptionType.CALL and q == 0:
            return cls.price(S, K, T, r, sigma, option_type, q).price

        if T <= 0:
            return max(S - K, 0) if option_type == OptionType.CALL else max(K - S, 0)

        sigma2 = sigma * sigma
        M = 2 * r / sigma2
        N_val = 2 * (r - q) / sigma2
        K_coeff = 1 - math.exp(-r * T)

        if option_type == OptionType.CALL:
            q2 = (-(N_val - 1) + math.sqrt((N_val - 1) ** 2 + 4 * M / K_coeff)) / 2
            # Find critical price S* via Newton-Raphson
            S_star = cls._find_critical_price_call(K, T, r, q, sigma, q2)
            if S >= S_star:
                return S - K
            A2 = (S_star / q2) * (1 - math.exp(-q * T) * cls._N(cls._d1(S_star, K, T, r, q, sigma)))
            european = cls.price(S, K, T, r, sigma, option_type, q).price
            return european + A2 * (S / S_star) ** q2
        else:
            q1 = (-(N_val - 1) - math.sqrt((N_val - 1) ** 2 + 4 * M / K_coeff)) / 2
            S_star = cls._find_critical_price_put(K, T, r, q, sigma, q1)
            if S <= S_star:
                return K - S
            A1 = -(S_star / q1) * (1 - math.exp(-q * T) * cls._N(-cls._d1(S_star, K, T, r, q, sigma)))
            european = cls.price(S, K, T, r, sigma, option_type, q).price
            return european + A1 * (S / S_star) ** q1

    @classmethod
    def _find_critical_price_call(cls, K, T, r, q, sigma, q2):
        """Newton-Raphson for American call early exercise boundary."""
        S_star = K * 1.2
        for _ in range(100):
            d1 = cls._d1(S_star, K, T, r, q, sigma)
            euro = cls.price(S_star, K, T, r, sigma, OptionType.CALL, q).price
            f = S_star - K - euro - (S_star / q2) * (1 - math.exp(-q * T) * cls._N(d1))
            # Numerical derivative
            h = S_star * 0.001
            euro_h = cls.price(S_star + h, K, T, r, sigma, OptionType.CALL, q).price
            d1_h = cls._d1(S_star + h, K, T, r, q, sigma)
            f_h = (S_star + h) - K - euro_h - ((S_star + h) / q2) * (1 - math.exp(-q * T) * cls._N(d1_h))
            f_prime = (f_h - f) / h
            if abs(f_prime) < 1e-12:
                break
            S_star -= f / f_prime
            S_star = max(S_star, K * 0.5)
        return S_star

    @classmethod
    def _find_critical_price_put(cls, K, T, r, q, sigma, q1):
        """Newton-Raphson for American put early exercise boundary."""
        S_star = K * 0.8
        for _ in range(100):
            d1 = cls._d1(S_star, K, T, r, q, sigma)
            euro = cls.price(S_star, K, T, r, sigma, OptionType.PUT, q).price
            f = K - S_star - euro + (S_star / q1) * (1 - math.exp(-q * T) * cls._N(-d1))
            h = S_star * 0.001
            euro_h = cls.price(S_star + h, K, T, r, sigma, OptionType.PUT, q).price
            d1_h = cls._d1(S_star + h, K, T, r, q, sigma)
            f_h = K - (S_star + h) - euro_h + ((S_star + h) / q1) * (1 - math.exp(-q * T) * cls._N(-d1_h))
            f_prime = (f_h - f) / h
            if abs(f_prime) < 1e-12:
                break
            S_star -= f / f_prime
            S_star = max(S_star, K * 0.1)
        return S_star

    @classmethod
    def aggregate_greeks(cls, legs: list) -> Dict[str, float]:
        """
        Aggregate Greeks across multiple option legs.
        Stolen from Vira-Kanishka/Option-Trading-Platform.
        """
        totals = {"price": 0, "delta": 0, "gamma": 0, "vega": 0, "theta": 0, "rho": 0}
        for leg in legs:
            g = cls.price(
                S=leg["S"], K=leg["K"], T=leg["T"], r=leg["r"],
                sigma=leg["sigma"], option_type=leg["type"],
                q=leg.get("q", 0),
            )
            sign = leg.get("side", 1)  # 1 = long, -1 = short
            qty = leg.get("qty", 1)
            totals["price"] += g.price * sign * qty * 100
            totals["delta"] += g.delta * sign * qty * 100
            totals["gamma"] += g.gamma * sign * qty * 100
            totals["vega"] += g.vega * sign * qty * 100
            totals["theta"] += g.theta * sign * qty * 100
            totals["rho"] += g.rho * sign * qty * 100
        return totals

    @classmethod
    def payoff_at_expiry(cls, S_T: float, legs: list) -> float:
        """Calculate P&L at expiration for a set of option legs."""
        total = 0
        for leg in legs:
            K = leg["K"]
            sign = leg.get("side", 1)
            qty = leg.get("qty", 1)
            if leg["type"] == OptionType.CALL:
                intrinsic = max(S_T - K, 0)
            else:
                intrinsic = max(K - S_T, 0)
            total += intrinsic * sign * qty * 100
        return total
