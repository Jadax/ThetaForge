"""
Advanced Volatility Models.
Stolen from: OptionStratLib, Riskfolio-Lib, je-suis-tm/quant-trading.

Implements:
- EWMA (Exponentially Weighted Moving Average) volatility
- GARCH(1,1) volatility forecasting
- Heston stochastic volatility simulation
- Ornstein-Uhlenbeck mean reversion
- Historical volatility from log returns
- Parkinson, Garman-Klass, Yang-Zhang range-based estimators
"""
import math
import random
from typing import List, Dict, Any, Optional, Tuple


class VolatilityModels:
    """
    Advanced volatility modeling suite.
    Production-quality implementations stolen from OptionStratLib.
    """

    @staticmethod
    def historical_volatility(
        prices: List[float],
        period: int = 20,
        annualize: bool = True,
    ) -> List[float]:
        """
        Calculate historical volatility from close prices.
        Uses log returns and rolling standard deviation.
        """
        if len(prices) < period + 1:
            return [0.0] * len(prices)

        # Calculate log returns
        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]

        vol = [0.0] * (period)  # First 'period' values have no vol
        for i in range(period, len(returns)):
            window = returns[i - period + 1: i + 1]
            mean = sum(window) / len(window)
            var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
            std = math.sqrt(var)
            if annualize:
                std *= math.sqrt(252)
            vol.append(std)

        return vol

    @staticmethod
    def ewma_volatility(
        prices: List[float],
        lambda_param: float = 0.94,
        annualize: bool = True,
    ) -> List[float]:
        """
        EWMA (Exponentially Weighted Moving Average) volatility.
        Lambda = 0.94 is standard for daily data (RiskMetrics).
        Gives more weight to recent observations.

        Formula: var[t] = lambda * var[t-1] + (1 - lambda) * r[t]^2
        """
        if len(prices) < 2:
            return [0.0] * len(prices)

        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]

        vol = [0.0]  # First point has no return
        variance = returns[0] ** 2

        for r in returns[1:]:
            variance = lambda_param * variance + (1 - lambda_param) * r ** 2
            std = math.sqrt(variance)
            if annualize:
                std *= math.sqrt(252)
            vol.append(std)

        return vol

    @staticmethod
    def garch_1_1(
        prices: List[float],
        omega: float = 0.000001,
        alpha: float = 0.06,
        beta: float = 0.90,
        annualize: bool = True,
        forecast_periods: int = 0,
    ) -> Tuple[List[float], Dict[str, float]]:
        """
        GARCH(1,1) volatility forecasting.
        Stolen from OptionStratLib.

        Formula: var[t] = omega + alpha * r[t-1]^2 + beta * var[t-1]
        Constraints: omega > 0, alpha >= 0, beta >= 0, alpha + beta < 1

        Returns:
            (vol_series, forecast) where forecast contains h-step ahead forecasts
        """
        if len(prices) < 2:
            return [0.0] * len(prices), {}

        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]

        # Initialize with sample variance
        mean_r = sum(returns) / len(returns)
        sample_var = sum((r - mean_r) ** 2 for r in returns) / len(returns)

        vol = [0.0]
        variance = sample_var

        for r in returns:
            variance = omega + alpha * r ** 2 + beta * variance
            std = math.sqrt(max(variance, 1e-12))
            if annualize:
                std *= math.sqrt(252)
            vol.append(std)

        # Long-run variance
        long_run_var = omega / max(1 - alpha - beta, 1e-12)
        persistence = alpha + beta

        # Forecast
        forecast = {}
        if forecast_periods > 0:
            h_var = variance
            forecasts = []
            for h in range(1, forecast_periods + 1):
                h_var = long_run_var + (persistence ** h) * (variance - long_run_var)
                h_vol = math.sqrt(max(h_var, 1e-12))
                if annualize:
                    h_vol *= math.sqrt(252)
                forecasts.append(h_vol)
            forecast = {
                "h_step_vol": forecasts,
                "long_run_vol": math.sqrt(long_run_var) * (math.sqrt(252) if annualize else 1),
                "persistence": persistence,
            }

        return vol, forecast

    @staticmethod
    def heston_simulation(
        S0: float,
        V0: float,
        r: float,
        kappa: float = 2.0,
        theta: float = 0.04,
        sigma_v: float = 0.3,
        rho: float = -0.7,
        T: float = 1.0,
        steps: int = 252,
        paths: int = 1000,
    ) -> Dict[str, Any]:
        """
        Heston stochastic volatility Monte Carlo simulation.
        Stolen from OptionStratLib.

        dS = r * S * dt + sqrt(V) * S * dW1
        dV = kappa * (theta - V) * dt + sigma_v * sqrt(V) * dW2
        Corr(dW1, dW2) = rho

        Returns terminal distributions for pricing/exotic analysis.
        """
        dt = T / steps
        sqrt_dt = math.sqrt(dt)

        terminal_S = []
        terminal_V = []
        path_S = []
        path_V = []

        for _ in range(paths):
            S = S0
            V = V0
            for _ in range(steps):
                # Correlated Brownian motions
                Z1 = random.gauss(0, 1)
                Z2 = rho * Z1 + math.sqrt(1 - rho ** 2) * random.gauss(0, 1)

                # Euler discretization
                V_new = V + kappa * (theta - V) * dt + sigma_v * math.sqrt(max(V, 0)) * sqrt_dt * Z2
                V_new = max(V_new, 0)  # Feller condition floor

                S_new = S * math.exp((r - 0.5 * V) * dt + math.sqrt(max(V, 0)) * sqrt_dt * Z1)

                S = S_new
                V = V_new

            terminal_S.append(S)
            terminal_V.append(V)

        # Calculate statistics
        terminal_S.sort()
        n = len(terminal_S)
        mean_S = sum(terminal_S) / n
        var_S = sum((s - mean_S) ** 2 for s in terminal_S) / (n - 1)
        std_S = math.sqrt(var_S)

        return {
            "terminal_prices": terminal_S,
            "terminal_volatilities": terminal_V,
            "mean_terminal_price": mean_S,
            "std_terminal_price": std_S,
            "percentile_5": terminal_S[int(n * 0.05)],
            "percentile_50": terminal_S[int(n * 0.50)],
            "percentile_95": terminal_S[int(n * 0.95)],
            "prob_above_0": sum(1 for s in terminal_S if s > 0) / n * 100,
        }

    @staticmethod
    def ornstein_uhlenbeck(
        X0: float,
        mu: float,
        theta: float,
        sigma: float,
        T: float = 1.0,
        steps: int = 252,
        paths: int = 1000,
    ) -> Dict[str, Any]:
        """
        Ornstein-Uhlenbeck mean-reverting process.
        Stolen from OptionStratLib.

        dX = theta * (mu - X) * dt + sigma * dW

        Used for: interest rate modeling, commodity futures, volatility modeling.
        """
        dt = T / steps
        sqrt_dt = math.sqrt(dt)

        terminal_X = []
        for _ in range(paths):
            X = X0
            for _ in range(steps):
                X += theta * (mu - X) * dt + sigma * random.gauss(0, 1) * sqrt_dt
            terminal_X.append(X)

        terminal_X.sort()
        n = len(terminal_X)
        mean_X = sum(terminal_X) / n

        return {
            "terminal_values": terminal_X,
            "mean_terminal": mean_X,
            "percentile_5": terminal_X[int(n * 0.05)],
            "percentile_50": terminal_X[int(n * 0.50)],
            "percentile_95": terminal_X[int(n * 0.95)],
            "half_life": math.log(2) / max(theta, 1e-12),
        }

    @staticmethod
    def parkinson_volatility(
        highs: List[float],
        lows: List[float],
        annualize: bool = True,
    ) -> float:
        """
        Parkinson range-based volatility estimator.
        More efficient than close-to-close (uses high/low).

        Formula: sigma = sqrt(1/(4*n*ln2) * sum(ln(H/L)^2))
        """
        if len(highs) < 2 or len(lows) < 2:
            return 0.0

        n = min(len(highs), len(lows))
        sq_sum = 0
        for i in range(n):
            if lows[i] > 0:
                sq_sum += math.log(highs[i] / lows[i]) ** 2

        vol = math.sqrt(sq_sum / (4 * n * math.log(2)))
        if annualize:
            vol *= math.sqrt(252)
        return vol

    @staticmethod
    def garman_klass_volatility(
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        annualize: bool = True,
    ) -> float:
        """
        Garman-Klass volatility estimator.
        Uses OHLV data for most efficient estimation.

        Formula: sigma = sqrt(0.5*ln(H/L)^2 - (2ln2-1)*ln(C/O)^2)
        """
        if len(opens) < 2:
            return 0.0

        n = len(opens)
        sq_sum = 0
        for i in range(n):
            if lows[i] > 0 and opens[i] > 0:
                hl = math.log(highs[i] / lows[i])
                co = math.log(closes[i] / opens[i])
                sq_sum += 0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2

        vol = math.sqrt(max(sq_sum / n, 0))
        if annualize:
            vol *= math.sqrt(252)
        return vol

    @staticmethod
    def yang_zhang_volatility(
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        annualize: bool = True,
    ) -> float:
        """
        Yang-Zhang volatility estimator.
        Unbiased estimator that handles overnight jumps.
        Best range-based estimator for OHLC data.
        """
        if len(opens) < 3:
            return 0.0

        n = len(opens)
        # Overnight returns
        overnight = [math.log(opens[i] / closes[i - 1]) for i in range(1, n)]
        # Open-to-close returns
        oc = [math.log(closes[i] / opens[i]) for i in range(n)]

        # Rogers-Satchell
        rs = 0
        for i in range(n):
            if highs[i] > 0 and lows[i] > 0 and opens[i] > 0:
                rs += math.log(highs[i] / closes[i]) * math.log(highs[i] / opens[i]) + \
                      math.log(lows[i] / closes[i]) * math.log(lows[i] / opens[i])

        # Yang-Zhang components
        overnight_var = sum((o - sum(overnight) / len(overnight)) ** 2 for o in overnight) / (len(overnight) - 1) if len(overnight) > 1 else 0
        oc_var = sum((o - sum(oc) / len(oc)) ** 2 for o in oc) / (len(oc) - 1) if len(oc) > 1 else 0
        rs_var = rs / n

        k = 0.34 / (1.34 + (n + 1) / (n - 1))
        yz_var = overnight_var + k * oc_var + (1 - k) * rs_var

        vol = math.sqrt(max(yz_var, 0))
        if annualize:
            vol *= math.sqrt(252)
        return vol

    @staticmethod
    def iv_term_structure(
        strikes: List[float],
        ivs: List[float],
        target_strike: float,
    ) -> float:
        """
        Interpolate IV at a target strike from observed IVs.
        Linear interpolation between strikes.
        """
        if not strikes or not ivs or len(strikes) != len(ivs):
            return 0.20

        # Sort by strike
        data = sorted(zip(strikes, ivs))

        if target_strike <= data[0][0]:
            return data[0][1]
        if target_strike >= data[-1][0]:
            return data[-1][1]

        for i in range(len(data) - 1):
            k1, v1 = data[i]
            k2, v2 = data[i + 1]
            if k1 <= target_strike <= k2:
                t = (target_strike - k1) / (k2 - k1)
                return v1 + t * (v2 - v1)

        return 0.20
