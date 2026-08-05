"""
Black-Scholes Option Pricing Engine.

Features:
- Black-Scholes with continuous dividend yield (q)
- Full Greeks suite: Delta, Gamma, Theta, Vega, Rho, Vanna, Vomma, Charm, Color, Veta
"""
import math
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
    Black-Scholes pricing with continuous dividend yield and a full Greeks suite.
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
