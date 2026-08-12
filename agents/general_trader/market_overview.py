"""
General-trader market overview: indices, sectors, bonds, commodities, and
per-asset reads for stocks/ETFs.

Everything here runs on the existing free data stack (yfinance via
``FreeDataProvider``) — no paid feed, no second scoring path. It is a read-only
market map: "what is the tape doing across asset classes right now", plus a
per-symbol technical read for any stock/ETF/bond proxy the user names. Fails
closed per asset — a missing symbol or empty history is dropped from the
output, never filled with a placeholder.

Reads reuse the same ``SignalEngine`` indicator implementations as the Brain
(RSI / ADX / MACD) so there is exactly one source of truth for technical math.
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional

from agents.backtest.advanced_backtest import SignalEngine
from agents.data_ingestion.free_data import FreeDataProvider
from agents.volatility.iv_metrics import realized_volatility

logger = logging.getLogger(__name__)

# Asset catalogs. Kind drives how the read is presented:
#   "index"     -> equity index, changes in %
#   "yield"     -> treasury yield ticker (^IRX etc.), changes in basis points
#   "bond_etf"  -> bond/fixed-income ETF, changes in %
#   "commodity" -> commodity ETF or dollar index, changes in %
INDICES: Dict[str, Dict[str, Any]] = {
    "^GSPC": {"label": "S&P 500", "kind": "index"},
    "^IXIC": {"label": "Nasdaq", "kind": "index"},
    "^DJI": {"label": "Dow", "kind": "index"},
    "^RUT": {"label": "Russell 2000", "kind": "index"},
    "^VIX": {"label": "VIX", "kind": "index"},
}

BONDS: Dict[str, Dict[str, Any]] = {
    "^IRX": {"label": "13-wk T-bill", "kind": "yield"},
    "^FVX": {"label": "5-yr Treasury", "kind": "yield"},
    "^TNX": {"label": "10-yr Treasury", "kind": "yield"},
    "^TYX": {"label": "30-yr Treasury", "kind": "yield"},
    "TLT": {"label": "20-yr Bond ETF", "kind": "bond_etf"},
    "IEF": {"label": "7-10yr ETF", "kind": "bond_etf"},
    "SHY": {"label": "1-3yr ETF", "kind": "bond_etf"},
    "TIP": {"label": "TIPS ETF", "kind": "bond_etf"},
    "HYG": {"label": "High-yield", "kind": "bond_etf"},
}

COMMODITIES: Dict[str, Dict[str, Any]] = {
    "GLD": {"label": "Gold", "kind": "commodity"},
    "SLV": {"label": "Silver", "kind": "commodity"},
    "USO": {"label": "Oil (USO)", "kind": "commodity"},
    "DX-Y.NYB": {"label": "Dollar index", "kind": "commodity"},
}

SECTORS: Dict[str, str] = {
    "XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare",
    "XLE": "Energy", "XLI": "Industrials", "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary", "XLU": "Utilities",
    "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Communication",
}


def _clean_history(frame) -> Optional[Dict[str, List[float]]]:
    """Normalize a yfinance frame to parallel lists, or None when unusable."""
    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        clean = frame.dropna(subset=["Close"])
    except Exception:
        return None
    closes = [float(value) for value in clean["Close"].tolist() if float(value) > 0]
    if len(closes) < 3:
        return None
    out: Dict[str, List[float]] = {"Close": closes}
    for column, target in (("High", "High"), ("Low", "Low"), ("Volume", "Volume")):
        try:
            if column not in clean.columns:
                continue
            if target == "Volume":
                out[target] = [
                    float(value) if float(value) == float(value) else 0.0
                    for value in clean[column].tolist()
                ]
            else:
                out[target] = [float(value) for value in clean[column].tolist() if float(value) > 0]
        except Exception:
            continue
    return out


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


class MarketOverview:
    """Read-only market map across asset classes, free data only."""

    def __init__(self, provider: Optional[FreeDataProvider] = None):
        self.provider = provider or FreeDataProvider()

    async def overview(self) -> Dict[str, Any]:
        """Daily tape across indices, bonds, commodities, and sectors plus a
        derived risk tilt. Each asset fails closed independently."""
        symbols = (
            {sym: spec for sym, spec in INDICES.items()}
            | {sym: spec for sym, spec in BONDS.items()}
            | {sym: spec for sym, spec in COMMODITIES.items()}
        )
        frames = await asyncio.gather(
            *(self.provider.get_historical_prices(symbol, period="1y") for symbol in symbols),
            return_exceptions=True,
        )

        indices: Dict[str, Any] = {}
        bonds: Dict[str, Any] = {}
        commodities: Dict[str, Any] = {}
        for (symbol, spec), frame in zip(symbols.items(), frames):
            if isinstance(frame, Exception):
                continue
            read = self._asset_read(symbol, spec, frame)
            if read is None:
                continue
            target = {
                "index": indices,
                "yield": bonds,
                "bond_etf": bonds,
                "commodity": commodities,
            }[spec["kind"]]
            target[symbol] = read

        sectors = {}
        try:
            sectors = await self.provider.get_sector_performance()
        except Exception as error:
            logger.debug("Sector performance unavailable: %s", error)

        yield_curve = self._yield_curve(bonds)
        return {
            "as_of": self._as_of(frames),
            "indices": indices,
            "bonds": bonds,
            "commodities": commodities,
            "sectors": sectors,
            "yield_curve": yield_curve,
            "risk_tilt": self._risk_tilt(indices, bonds),
        }

    async def analyze_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Per-symbol general read (any stock, ETF, or bond proxy). Returns
        None fail-closed when the symbol has no usable history."""
        frame = await self.provider.get_historical_prices(symbol, period="1y")
        return self._symbol_read(symbol.upper(), frame)

    async def analyze_symbols(self, symbols: List[str]) -> Dict[str, Any]:
        """Concurrent per-symbol reads; failures drop individual symbols."""
        reads = await asyncio.gather(
            *(self.analyze_symbol(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        return {
            read["symbol"]: read
            for read in reads
            if not isinstance(read, Exception) and read is not None
        }

    # ── per-asset helpers ────────────────────────────────────────────────

    @staticmethod
    def _as_of(frames) -> str:
        for frame in frames:
            if isinstance(frame, Exception) or frame is None or getattr(frame, "empty", True):
                continue
            try:
                return str(frame.index[-1].date())
            except Exception:
                continue
        return ""

    def _asset_read(self, symbol: str, spec: Dict[str, Any], frame) -> Optional[Dict[str, Any]]:
        data = _clean_history(frame)
        if data is None:
            return None
        closes = data["Close"]
        last, prev = closes[-1], closes[-2]
        read: Dict[str, Any] = {"label": spec["label"], "level": round(last, 2)}
        if spec["kind"] == "yield":
            # Yield tickers quote in percent; report the move in basis points.
            read["level_pct"] = round(last, 2)
            read["change_bp"] = round((last - prev) * 100, 1)
        else:
            read["change_1d_pct"] = round((last / prev - 1) * 100, 2)
            read["change_5d_pct"] = round(
                (closes[-1] / closes[-min(6, len(closes))] - 1) * 100, 2
            ) if len(closes) >= 6 else None
        read["trend"] = self._sma_trend(closes)
        return read

    def _yield_curve(self, bonds: Dict[str, Any]) -> Dict[str, Any]:
        short = (bonds.get("^IRX") or {}).get("level_pct")
        five = (bonds.get("^FVX") or {}).get("level_pct")
        ten = (bonds.get("^TNX") or {}).get("level_pct")
        thirty = (bonds.get("^TYX") or {}).get("level_pct")
        shape = None
        if short is not None and ten is not None:
            shape = "inverted" if ten < short else "normal"
        return {
            "short": short,
            "mid": five,
            "long": ten,
            "very_long": thirty,
            "shape": shape,
        }

    def _risk_tilt(self, indices: Dict[str, Any], bonds: Dict[str, Any]) -> Dict[str, Any]:
        """Coarse risk-on/risk-off read from equity breadth + credit bid.

        Risk-on: most equities up and high-yield bid (or long-bond offered).
        Risk-off: most equities down and high-yield offered (or long-bond bid).
        Anything unclear resolves to "mixed", never a forced call.
        """
        equity = [read for sym, read in indices.items() if sym != "^VIX"]
        up = sum(1 for read in equity if (read.get("change_1d_pct") or 0) > 0.2)
        down = sum(1 for read in equity if (read.get("change_1d_pct") or 0) < -0.2)
        hyg = (bonds.get("HYG") or {}).get("change_1d_pct")
        tlt = (bonds.get("TLT") or {}).get("change_1d_pct")
        credit_risk_on = (hyg is not None and hyg > 0.3) or (tlt is not None and tlt < -0.3)
        credit_risk_off = (hyg is not None and hyg < -0.3) or (tlt is not None and tlt > 0.3)
        if up >= max(2, len(equity) // 2) and credit_risk_on:
            tilt = "risk_on"
        elif down >= max(2, len(equity) // 2) and credit_risk_off:
            tilt = "risk_off"
        else:
            tilt = "mixed"
        return {"tilt": tilt, "indices_up": up, "indices_down": down}

    # ── per-symbol read ──────────────────────────────────────────────────

    def _symbol_read(self, symbol: str, frame) -> Optional[Dict[str, Any]]:
        data = _clean_history(frame)
        if data is None or len(data["Close"]) < 30:
            return None
        closes = data["Close"]
        highs = data.get("High") or closes
        lows = data.get("Low") or closes
        volumes = data.get("Volume") or [0.0] * len(closes)

        last = closes[-1]
        sma_50 = _mean(closes[-50:]) if len(closes) >= 50 else None
        sma_200 = _mean(closes[-200:]) if len(closes) >= 200 else None
        rsi = SignalEngine.rsi(closes)[-1]
        adx = SignalEngine.adx(highs, lows, closes)[-1]
        _, _, histogram = SignalEngine.macd(closes)
        macd_hist = histogram[-1] if histogram else 0.0
        last_volume = volumes[-1] if volumes else 0.0
        prior_20 = volumes[-21:-1] if len(volumes) > 21 else volumes[:-1]
        avg_volume_20 = _mean(prior_20) if prior_20 else None

        year_high = max(closes)
        year_low = min(closes)
        month_ago = closes[-22] if len(closes) >= 23 else closes[0]

        read = {
            "symbol": symbol,
            "price": round(last, 2),
            "change_1d_pct": round((last / closes[-2] - 1) * 100, 2),
            "change_1m_pct": round((last / month_ago - 1) * 100, 2),
            "sma_50": round(sma_50, 2) if sma_50 else None,
            "sma_200": round(sma_200, 2) if sma_200 else None,
            "above_200d": bool(sma_200 and last > sma_200),
            "rsi_14": round(rsi, 1),
            "adx": round(adx, 1),
            "macd_histogram": round(macd_hist, 4),
            "macd_bullish": bool(macd_hist > 0),
            "volume_ratio": round(last_volume / avg_volume_20, 2) if avg_volume_20 else None,
            "percent_off_52w_high": round((last / year_high - 1) * 100, 1),
            "percent_above_52w_low": round((last / year_low - 1) * 100, 1),
            "volatility_20d_annualized": round(rv, 4) if (rv := realized_volatility(closes)) else None,
        }
        read["read"] = self._classify_read(read, sma_50, sma_200)
        return read

    @staticmethod
    def _classify_read(read: Dict[str, Any], sma_50: Optional[float], sma_200: Optional[float]) -> str:
        price = read["price"]
        rsi = read["rsi_14"]
        macd_up = read["macd_bullish"]
        above_200 = read["above_200d"]
        # A falling knife below the 200d with weak momentum is bearish; a
        # strong uptrend above both MAs with room in RSI is bullish. RSI at
        # extremes is a warning either way, not a signal to chase.
        if above_200 and macd_up and rsi < 70 and sma_50 is not None and price > sma_50:
            return "bullish"
        if not above_200 and not macd_up and rsi > 30:
            return "bearish"
        return "neutral"

    @staticmethod
    def _sma_trend(closes: List[float]) -> str:
        if len(closes) >= 50:
            sma_50 = sum(closes[-50:]) / 50
            price = closes[-1]
            if price > sma_50:
                return "uptrend"
            if price < sma_50:
                return "downtrend"
        return "flat"
