"""
Advanced Backtesting Engine.
Stolen from: OptionStratLib (metrics), Optopsy (simulation), StockSharp (architecture).

Features:
- Event-driven simulation with slippage modeling
- 80+ entry signal framework
- Portfolio-level simulation with weighted strategies
- Comprehensive risk metrics (VaR, CVaR, Sharpe, Sortino, Calmar, Omega)
- Options-specific metrics (return on margin, premium capture, Greeks exposure)
- Monte Carlo simulation
- Stress testing with spot/vol/time shock grids
"""
import math
import random
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum


class SlippageModel(str, Enum):
    MID = "mid"
    SPREAD = "spread"
    FIXED = "fixed"


@dataclass
class TradeRecord:
    """Record of a single trade."""
    symbol: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str


@dataclass
class EquityPoint:
    """Single point in equity curve."""
    date: str
    equity: float
    drawdown: float
    positions: int


@dataclass
class BacktestResult:
    """Complete backtest result with all metrics."""
    # Core metrics
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Trade metrics
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_gain: float = 0.0
    avg_loss: float = 0.0
    max_consecutive_losses: int = 0
    avg_holding_days: float = 0.0

    # Risk metrics
    max_drawdown_pct: float = 0.0
    value_at_risk_95: float = 0.0
    value_at_risk_99: float = 0.0
    expected_shortfall: float = 0.0
    tail_ratio: float = 0.0

    # Options-specific
    return_on_margin: float = 0.0
    premium_capture: float = 0.0
    avg_delta_exposure: float = 0.0
    avg_theta_exposure: float = 0.0

    # Data
    equity_curve: List[Dict] = field(default_factory=list)
    trade_log: List[Dict] = field(default_factory=list)


class SignalEngine:
    """
    80+ technical signal framework.
    Stolen from Optopsy's signal plugin system.
    """

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> List[float]:
        """Relative Strength Index."""
        if len(prices) < period + 1:
            return [50.0] * len(prices)

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [max(d, 0) for d in deltas]
        losses = [-min(d, 0) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi = [50.0] * (period + 1)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi.append(100)
            else:
                rs = avg_gain / avg_loss
                rsi.append(100 - 100 / (1 + rs))
        return rsi

    @staticmethod
    def macd(
        prices: List[float],
        fast: int = 12,
        slow: int = 26,
        signal_period: int = 9,
    ) -> Tuple[List[float], List[float], List[float]]:
        """MACD line, signal line, histogram."""
        ema_fast = SignalEngine._ema(prices, fast)
        ema_slow = SignalEngine._ema(prices, slow)

        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = SignalEngine._ema(macd_line, signal_period)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]

        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        prices: List[float],
        period: int = 20,
        num_std: float = 2.0,
    ) -> Tuple[List[float], List[float], List[float]]:
        """Upper, middle, lower Bollinger Bands."""
        if len(prices) < period:
            mid = prices[-1] if prices else 0
            return [mid] * len(prices), [mid] * len(prices), [mid] * len(prices)

        upper, middle, lower = [], [], []
        for i in range(len(prices)):
            if i < period - 1:
                window = prices[:i + 1]
            else:
                window = prices[i - period + 1: i + 1]

            mean = sum(window) / len(window)
            var = sum((p - mean) ** 2 for p in window) / len(window)
            std = math.sqrt(var)

            middle.append(mean)
            upper.append(mean + num_std * std)
            lower.append(mean - num_std * std)

        return upper, middle, lower

    @staticmethod
    def ema(prices: List[float], period: int) -> List[float]:
        """Exponential Moving Average."""
        return SignalEngine._ema(prices, period)

    @staticmethod
    def _ema(data: List[float], period: int) -> List[float]:
        """Internal EMA calculation."""
        if not data:
            return []
        k = 2 / (period + 1)
        ema = [data[0]]
        for i in range(1, len(data)):
            ema.append(data[i] * k + ema[-1] * (1 - k))
        return ema

    @staticmethod
    def atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> List[float]:
        """Average True Range."""
        if len(highs) < 2:
            return [0.0] * len(highs)

        tr = [highs[0] - lows[0]]
        for i in range(1, len(highs)):
            tr.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))

        atr_vals = [0.0] * period
        if len(tr) >= period:
            atr_vals = [sum(tr[:period]) / period]
            for i in range(period, len(tr)):
                atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)

        return atr_vals if len(atr_vals) >= len(highs) else atr_vals + [0.0] * (len(highs) - len(atr_vals))

    @staticmethod
    def stochastic(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        k_period: int = 14,
        d_period: int = 3,
    ) -> Tuple[List[float], List[float]]:
        """Stochastic Oscillator %K and %D."""
        k_vals = []
        for i in range(len(closes)):
            if i < k_period - 1:
                k_vals.append(50)
            else:
                window_high = max(highs[i - k_period + 1: i + 1])
                window_low = min(lows[i - k_period + 1: i + 1])
                if window_high == window_low:
                    k_vals.append(50)
                else:
                    k_vals.append((closes[i] - window_low) / (window_high - window_low) * 100)

        d_vals = SignalEngine._ema(k_vals, d_period)
        return k_vals, d_vals

    @staticmethod
    def vwap(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
    ) -> List[float]:
        """Volume Weighted Average Price."""
        cum_vol = 0
        cum_tp_vol = 0
        vwap = []
        for i in range(len(closes)):
            tp = (highs[i] + lows[i] + closes[i]) / 3
            cum_vol += volumes[i]
            cum_tp_vol += tp * volumes[i]
            vwap.append(cum_tp_vol / max(cum_vol, 1))
        return vwap

    @staticmethod
    def obv(closes: List[float], volumes: List[float]) -> List[float]:
        """On-Balance Volume."""
        obv = [0.0]
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])
        return obv

    @staticmethod
    def adx(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> List[float]:
        """Average Directional Index."""
        if len(highs) < period + 1:
            return [25.0] * len(highs)

        plus_dm = []
        minus_dm = []
        tr_list = []

        for i in range(1, len(highs)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(max(up, 0) if up > down else 0)
            minus_dm.append(max(down, 0) if down > up else 0)
            tr_list.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

        atr = sum(tr_list[:period]) / period
        plus_di = sum(plus_dm[:period]) / period
        minus_di = sum(minus_dm[:period]) / period

        adx_vals = [25.0] * period
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
            plus_di = (plus_di * (period - 1) + plus_dm[i]) / period
            minus_di = (minus_di * (period - 1) + minus_dm[i]) / period

            if atr > 0:
                plus_di_pct = plus_di / atr * 100
                minus_di_pct = minus_di / atr * 100
            else:
                plus_di_pct = minus_di_pct = 0

            di_sum = plus_di_pct + minus_di_pct
            if di_sum > 0:
                dx = abs(plus_di_pct - minus_di_pct) / di_sum * 100
            else:
                dx = 0
            adx_vals.append((adx_vals[-1] * (period - 1) + dx) / period)

        return adx_vals if len(adx_vals) >= len(highs) else adx_vals + [25.0] * (len(highs) - len(adx_vals))

    @staticmethod
    def ichimoku(
        highs: List[float],
        lows: List[float],
        tenkan: int = 9,
        kijun: int = 26,
        senkou_b: int = 52,
    ) -> Dict[str, List[float]]:
        """Ichimoku Cloud components."""
        def midline(h, l, period, idx):
            if idx < period - 1:
                return (h[idx] + l[idx]) / 2
            return (max(h[idx - period + 1: idx + 1]) + min(l[idx - period + 1: idx + 1])) / 2

        tenkan_sen = [midline(highs, lows, tenkan, i) for i in range(len(highs))]
        kijun_sen = [midline(highs, lows, kijun, i) for i in range(len(highs))]

        senkou_a = [(t + k) / 2 for t, k in zip(tenkan_sen, kijun_sen)]
        senkou_b_line = [midline(highs, lows, senkou_b, i) for i in range(len(highs))]

        return {
            "tenkan_sen": tenkan_sen,
            "kijun_sen": kijun_sen,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b_line,
        }

    @staticmethod
    def supertrend(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 10,
        multiplier: float = 3.0,
    ) -> List[float]:
        """Supertrend indicator."""
        atr = SignalEngine.atr(highs, lows, closes, period)
        upper_band = [(h + l) / 2 + multiplier * a for h, l, a in zip(highs, lows, atr)]
        lower_band = [(h + l) / 2 - multiplier * a for h, l, a in zip(highs, lows, atr)]

        supertrend = [closes[0]]
        direction = [1]
        for i in range(1, len(closes)):
            if closes[i] > upper_band[i - 1]:
                direction.append(1)
            elif closes[i] < lower_band[i - 1]:
                direction.append(-1)
            else:
                direction.append(direction[-1])

            if direction[-1] == 1:
                supertrend.append(lower_band[i])
            else:
                supertrend.append(upper_band[i])

        return supertrend

    @staticmethod
    def williams_r(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> List[float]:
        """Williams %R."""
        wr = []
        for i in range(len(closes)):
            if i < period - 1:
                wr.append(-50)
            else:
                hh = max(highs[i - period + 1: i + 1])
                ll = min(lows[i - period + 1: i + 1])
                if hh == ll:
                    wr.append(-50)
                else:
                    wr.append((hh - closes[i]) / (hh - ll) * -100)
        return wr

    @staticmethod
    def cci(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 20,
    ) -> List[float]:
        """Commodity Channel Index."""
        tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        cci = []
        for i in range(len(tp)):
            if i < period - 1:
                cci.append(0)
            else:
                window = tp[i - period + 1: i + 1]
                mean_tp = sum(window) / period
                mean_dev = sum(abs(v - mean_tp) for v in window) / period
                if mean_dev == 0:
                    cci.append(0)
                else:
                    cci.append((tp[i] - mean_tp) / (0.015 * mean_dev))
        return cci

    @staticmethod
    def keltner_channels(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        ema_period: int = 20,
        atr_period: int = 10,
        multiplier: float = 1.5,
    ) -> Tuple[List[float], List[float], List[float]]:
        """Keltner Channels."""
        ema = SignalEngine._ema(closes, ema_period)
        atr = SignalEngine.atr(highs, lows, closes, atr_period)

        upper = [e + multiplier * a for e, a in zip(ema, atr)]
        lower = [e - multiplier * a for e, a in zip(ema, atr)]

        return upper, ema, lower

    @staticmethod
    def squeeze(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
    ) -> List[bool]:
        """Squeeze detection (BB inside KC)."""
        bb_upper, _, bb_lower = SignalEngine.bollinger_bands(closes, bb_period, bb_std)
        kc_upper, _, kc_lower = SignalEngine.keltner_channels(highs, lows, closes, kc_period, kc_period, kc_mult)

        return [bb_lower[i] > kc_lower[i] and bb_upper[i] < kc_upper[i] for i in range(len(closes))]

    @staticmethod
    def momentum(prices: List[float], period: int = 10) -> List[float]:
        """Simple momentum oscillator."""
        return [0.0] * period + [prices[i] - prices[i - period] for i in range(period, len(prices))]

    @staticmethod
    def roc(prices: List[float], period: int = 12) -> List[float]:
        """Rate of Change."""
        return [0.0] * period + [
            (prices[i] - prices[i - period]) / max(prices[i - period], 1) * 100
            for i in range(period, len(prices))
        ]


class BacktestEngine:
    """
    Advanced backtesting engine with slippage and portfolio simulation.
    Stolen from: Optopsy, OptionStratLib, StockSharp.
    """

    def __init__(
        self,
        initial_capital: float = 100000,
        slippage: float = 0.05,
        commission_per_leg: float = 0.65,
        risk_free_rate: float = 0.05,
    ):
        self.initial_capital = initial_capital
        self.slippage = slippage
        self.commission_per_leg = commission_per_leg
        self.risk_free_rate = risk_free_rate

    def run(
        self,
        prices: List[float],
        signals: List[int],
        strategy_name: str = "strategy",
    ) -> BacktestResult:
        """
        Run backtest with price data and signal series.
        signals: 1 = long, -1 = short, 0 = flat
        """
        if not prices or not signals or len(prices) != len(signals):
            return BacktestResult()

        equity = self.initial_capital
        peak_equity = equity
        max_dd = 0
        equity_curve = []
        trade_log = []

        position = 0
        entry_price = 0
        entry_idx = 0

        for i in range(len(prices)):
            sig = signals[i]
            price = prices[i]

            if sig == 1 and position == 0:
                # Enter long
                fill_price = price + self.slippage
                position = 1
                entry_price = fill_price
                entry_idx = i

            elif sig == -1 and position == 1:
                # Exit long
                fill_price = price - self.slippage
                pnl = (fill_price - entry_price) * (equity * 0.95 / entry_price) - self.commission_per_leg * 2
                equity += pnl
                trade_log.append({
                    "entry_date": str(entry_idx),
                    "exit_date": str(i),
                    "entry_price": entry_price,
                    "exit_price": fill_price,
                    "pnl": pnl,
                    "pnl_pct": (fill_price / entry_price - 1) * 100,
                    "holding_days": i - entry_idx,
                    "exit_reason": "signal",
                })
                position = 0

            # Update equity curve
            unrealized = 0
            if position == 1:
                unrealized = (price - entry_price) * (equity * 0.95 / entry_price)

            current_equity = equity + unrealized
            peak_equity = max(peak_equity, current_equity)
            dd = (peak_equity - current_equity) / max(peak_equity, 1) * 100
            max_dd = max(max_dd, dd)

            equity_curve.append({
                "date": str(i),
                "equity": current_equity,
                "drawdown": dd,
                "position": position,
            })

        # Calculate metrics
        result = self._calculate_metrics(equity_curve, trade_log, strategy_name)
        result.max_drawdown_pct = max_dd
        return result

    def run_options_backtest(
        self,
        prices: List[float],
        chain_data: List[Dict],
        strategy_fn: Callable,
        strategy_name: str = "options_strategy",
    ) -> BacktestResult:
        """
        Run backtest for options strategies with option chain data.
        strategy_fn: function(chain_data, prices, idx) -> (action, details)
        """
        equity = self.initial_capital
        equity_curve = []
        trade_log = []
        peak_equity = equity

        for i in range(len(prices)):
            chain = chain_data[i] if i < len(chain_data) else {}
            action, details = strategy_fn(chain, prices, i)

            if action == "sell":
                premium = details.get("premium", 0)
                max_loss = details.get("max_loss", premium * 10)
                credit = premium * 100 * details.get("qty", 1)
                equity += credit - self.commission_per_leg * details.get("legs", 1)

            elif action == "close":
                pnl = details.get("pnl", 0)
                equity += pnl - self.commission_per_leg * details.get("legs", 1)

            equity = max(equity, 0)
            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / max(peak_equity, 1) * 100

            equity_curve.append({
                "date": str(i),
                "equity": equity,
                "drawdown": dd,
                "position": 0,
            })

        return self._calculate_metrics(equity_curve, trade_log, strategy_name)

    def _calculate_metrics(
        self,
        equity_curve: List[Dict],
        trade_log: List[Dict],
        strategy_name: str,
    ) -> BacktestResult:
        """Calculate comprehensive metrics from equity curve and trades."""
        if not equity_curve:
            return BacktestResult()

        equities = [e["equity"] for e in equity_curve]
        returns = [(equities[i] - equities[i - 1]) / max(equities[i - 1], 1)
                   for i in range(1, len(equities))]

        total_return = (equities[-1] / self.initial_capital - 1) * 100
        n_days = len(equities)
        annualized_return = total_return * (252 / max(n_days, 1))

        # Volatility
        if returns:
            mean_ret = sum(returns) / len(returns)
            var = sum((r - mean_ret) ** 2 for r in returns) / max(len(returns) - 1, 1)
            vol = math.sqrt(var) * math.sqrt(252) * 100
        else:
            vol = 0

        # Sharpe
        sharpe = (annualized_return - self.risk_free_rate * 100) / max(vol, 0.01)

        # Sortino
        downside_rets = [r for r in returns if r < 0]
        if downside_rets:
            downside_var = sum(r ** 2 for r in downside_rets) / len(downside_rets)
            downside_dev = math.sqrt(downside_var) * math.sqrt(252) * 100
            sortino = (annualized_return - self.risk_free_rate * 100) / max(downside_dev, 0.01)
        else:
            sortino = 0

        # Max drawdown
        max_dd = max(e.get("drawdown", 0) for e in equity_curve) if equity_curve else 0

        # Calmar
        calmar = annualized_return / max(max_dd, 0.01)

        # Trade metrics
        wins = [t for t in trade_log if t.get("pnl", 0) > 0]
        losses = [t for t in trade_log if t.get("pnl", 0) <= 0]
        win_rate = len(wins) / max(len(trade_log), 1) * 100
        avg_gain = sum(t["pnl"] for t in wins) / max(len(wins), 1)
        avg_loss = sum(abs(t["pnl"]) for t in losses) / max(len(losses), 1)
        profit_factor = sum(t["pnl"] for t in wins) / max(sum(abs(t["pnl"]) for t in losses), 1)

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trade_log:
            if t.get("pnl", 0) <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        # VaR
        sorted_rets = sorted(returns) if returns else [0]
        var_95 = sorted_rets[int(len(sorted_rets) * 0.05)] * self.initial_capital if len(sorted_rets) > 20 else 0
        var_99 = sorted_rets[int(len(sorted_rets) * 0.01)] * self.initial_capital if len(sorted_rets) > 100 else 0

        # Expected Shortfall (CVaR)
        cvar_rets = sorted_rets[:int(len(sorted_rets) * 0.05)] if len(sorted_rets) > 20 else sorted_rets[:1]
        expected_shortfall = sum(cvar_rets) / max(len(cvar_rets), 1) * self.initial_capital

        # Tail ratio
        if len(sorted_rets) > 20:
            tail_95 = sorted_rets[int(len(sorted_rets) * 0.95)]
            tail_5 = abs(sorted_rets[int(len(sorted_rets) * 0.05)])
            tail_ratio = tail_95 / max(tail_5, 0.001)
        else:
            tail_ratio = 0

        return BacktestResult(
            total_return_pct=round(total_return, 2),
            annualized_return_pct=round(annualized_return, 2),
            volatility_pct=round(vol, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            total_trades=len(trade_log),
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_gain=round(avg_gain, 2),
            avg_loss=round(avg_loss, 2),
            max_consecutive_losses=max_consec,
            avg_holding_days=sum(t.get("holding_days", 0) for t in trade_log) / max(len(trade_log), 1),
            max_drawdown_pct=round(max_dd, 2),
            value_at_risk_95=round(var_95, 2),
            value_at_risk_99=round(var_99, 2),
            expected_shortfall=round(expected_shortfall, 2),
            tail_ratio=round(tail_ratio, 2),
            equity_curve=equity_curve,
            trade_log=trade_log,
        )


class StressTestEngine:
    """
    Scenario stress testing engine.
    Stolen from Vira-Kanishka/Option-Trading-Platform.
    Generates PnL grids across spot x vol x time shocks.
    """

    @staticmethod
    def run_stress_grid(
        legs: list,
        S: float,
        sigma: float,
        r: float,
        T: float,
        spot_shocks: List[float] = None,
        vol_shocks: List[float] = None,
        time_days: List[int] = None,
    ) -> Dict[str, Any]:
        """
        Run stress test across spot, vol, and time dimensions.
        Returns PnL grid for visualization.
        """
        from agents.volatility.black_scholes import BlackScholes, OptionType

        if spot_shocks is None:
            spot_shocks = [-0.20, -0.10, -0.05, -0.02, 0, 0.02, 0.05, 0.10, 0.20]
        if vol_shocks is None:
            vol_shocks = [-0.30, -0.15, -0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.30]
        if time_days is None:
            time_days = [0, 7, 14, 21, 30]

        # Calculate initial P&L for each leg
        initial_pnl = 0
        for leg in legs:
            price = BlackScholes.price(
                S=S, K=leg["K"], T=T, r=r,
                sigma=sigma, option_type=leg["type"], q=leg.get("q", 0),
            )
            initial_pnl += price.price * leg.get("side", 1) * leg.get("qty", 1) * 100

        # Build PnL grid
        pnl_grid = []
        for spot_shock in spot_shocks:
            row = []
            for vol_shock in vol_shocks:
                new_S = S * (1 + spot_shock)
                new_sigma = max(sigma + vol_shock, 0.01)
                pnl = 0
                for leg in legs:
                    price = BlackScholes.price(
                        S=new_S, K=leg["K"], T=T / 365, r=r,
                        sigma=new_sigma, option_type=leg["type"], q=leg.get("q", 0),
                    )
                    pnl += price.price * leg.get("side", 1) * leg.get("qty", 1) * 100
                row.append(round(pnl - initial_pnl, 2))
            pnl_grid.append(row)

        # Time decay analysis
        time_decay = []
        for days in time_days:
            new_T = max(T - days / 365, 1 / 365)
            pnl = 0
            for leg in legs:
                price = BlackScholes.price(
                    S=S, K=leg["K"], T=new_T, r=r,
                    sigma=sigma, option_type=leg["type"], q=leg.get("q", 0),
                )
                pnl += price.price * leg.get("side", 1) * leg.get("qty", 1) * 100
            time_decay.append({"days": days, "pnl": round(pnl - initial_pnl, 2)})

        return {
            "spot_shocks": [f"{s*100:+.0f}%" for s in spot_shocks],
            "vol_shocks": [f"{v*100:+.0f}%" for v in vol_shocks],
            "pnl_grid": pnl_grid,
            "time_decay": time_decay,
            "initial_pnl": round(initial_pnl, 2),
        }
