"""
Free Multi-Source Data Provider.
Aggregates data from IBKR (primary), CBOE delayed quotes (no key), Alpaca
(secondary), and yfinance (fallback). All sources are FREE with a brokerage
account or no account at all.
Adapted from IBKRTools and general market data aggregation patterns.
"""
import asyncio
import logging
import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date

import yfinance as yf
import pandas as pd

from agents.data_ingestion.cboe_data import CBOEDataProvider

logger = logging.getLogger(__name__)


class FreeDataProvider:
    """
    Multi-source data provider using only free APIs.
    Priority: IBKR > CBOE (options) > Alpaca > yfinance
    """

    def __init__(self, ibkr_client=None, alpaca_client=None):
        self.ibkr = ibkr_client
        self.alpaca = alpaca_client
        self.cboe = CBOEDataProvider()

    async def get_stock_price(self, symbol: str) -> Optional[float]:
        """Get current stock price from any available source."""
        if self.ibkr and self.ibkr._connected:
            try:
                contract = self._stock_contract(symbol)
                self.ibkr.ib.qualifyContracts(contract)
                ticker = self.ibkr.ib.reqMktData(contract, '', False, False)
                await asyncio.sleep(1)
                price = ticker.marketPrice()
                self.ibkr.ib.cancelMktData(contract)
                return float(price)
            except Exception as e:
                logger.warning(f"IBKR price fetch failed for {symbol}: {e}")

        try:
            def fetch_price() -> float:
                ticker = yf.Ticker(symbol)
                return float(ticker.fast_info.last_price)

            return await asyncio.to_thread(fetch_price)
        except Exception as e:
            logger.warning(f"yfinance price fetch failed for {symbol}: {e}")
        return None

    async def get_option_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """Get full option chain. IBKR provides free real-time data with account.

        Falls back to the free CBOE delayed-quotes feed (full Greeks, no API
        key, 15-minute delayed NBBO) and then to yfinance.
        """
        if self.ibkr and self.ibkr._connected:
            return await self.ibkr.get_option_chain(symbol)

        try:
            chain = await self.cboe.get_option_chain(symbol)
            if chain:
                return chain
        except Exception as error:
            logger.debug("CBOE chain unavailable for %s: %s", symbol, error)

        try:
            def fetch_chain() -> List[Dict[str, Any]]:
                ticker = yf.Ticker(symbol)
                chain_data = []
                for exp in ticker.options[:8]:
                    chain = ticker.option_chain(exp)
                    for _, row in chain.calls.iterrows():
                        chain_data.append(self._parse_yf_option(row, exp, "CALL"))
                    for _, row in chain.puts.iterrows():
                        chain_data.append(self._parse_yf_option(row, exp, "PUT"))
                return chain_data

            return await asyncio.to_thread(fetch_chain)
        except Exception as e:
            logger.error(f"Option chain fetch failed for {symbol}: {e}")
        return []

    async def get_historical_iv(self, symbol: str, period: str = "1y") -> List[float]:
        """Get historical implied volatility using yfinance options data."""
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            if not expirations:
                return []
            chain = ticker.option_chain(expirations[0])
            atm_calls = chain.calls[
                (chain.calls['strike'] - chain.calls['strike'].median()).abs().argsort()[:1]
            ]
            if not atm_calls.empty:
                return [float(atm_calls['impliedVolatility'].iloc[0])]
        except Exception:
            pass
        return []

    async def get_historical_prices(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        """Get historical OHLCV data via yfinance."""
        try:
            return await asyncio.to_thread(
                lambda: yf.Ticker(symbol).history(period=period, interval=interval)
            )
        except Exception as e:
            logger.error(f"Historical data failed for {symbol}: {e}")
        return pd.DataFrame()

    async def get_vix(self) -> Optional[float]:
        """Get current VIX level."""
        try:
            vix = yf.Ticker("^VIX")
            return float(vix.fast_info.last_price)
        except Exception:
            return None

    async def get_vix_term_structure(self) -> Dict[str, Optional[float]]:
        """Latest closes of VIX9D / VIX3M / VIX6M / VIX1Y.

        Primary source is Yahoo's VIX index tickers; the CBOE daily-history
        feed is the fallback. A missing index stays None rather than being
        replaced with a placeholder.
        """
        structure: Dict[str, Optional[float]] = {
            index: None for index in ("VIX9D", "VIX3M", "VIX6M", "VIX1Y")
        }
        yahoo_map = {"VIX9D": "^VIX9D", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VIX1Y": "^VIX1Y"}
        for index, ticker_symbol in yahoo_map.items():
            try:
                ticker = yf.Ticker(ticker_symbol)
                price = float(ticker.fast_info.last_price)
                if price > 0:
                    structure[index] = round(price, 2)
            except Exception:
                continue
        if all(value is not None for value in structure.values()):
            return structure
        # Fill any gaps from the CBOE daily-history feed (no key required).
        try:
            cboe_structure = await self.cboe.get_vix_term_structure()
            for index, value in cboe_structure.items():
                if structure.get(index) is None:
                    structure[index] = value
        except Exception as error:
            logger.debug("CBOE VIX term structure unavailable: %s", error)
        return structure

    def is_vix_contango(self, term_structure: Dict[str, Optional[float]]) -> Optional[bool]:
        """True when the VIX term structure is in healthy contango
        (VIX9D < VIX3M < VIX6M), False when inverted, None when data is missing.

        Inversion means front-month fear is elevated — the worst regime for
        selling premium (Tastytrade pauses sellers on inverted structure).
        """
        vix9d = term_structure.get("VIX9D")
        vix3m = term_structure.get("VIX3M")
        vix6m = term_structure.get("VIX6M")
        if vix9d is None or vix3m is None:
            return None
        if vix9d >= vix3m:
            return False
        # Contango also fails when the mid part of the curve is already
        # dipping below the front (partial inversion).
        if vix6m is not None and vix6m < vix3m:
            return False
        return True

    async def get_next_earnings_date(self, symbol: str) -> Optional[date]:
        """Next scheduled earnings date via yfinance (ETFs return None)."""
        try:
            def fetch_next() -> Optional[date]:
                ticker = yf.Ticker(symbol)
                frame = ticker.get_earnings_dates(limit=4)
                if frame is None or frame.empty:
                    return None
                today = pd.Timestamp.today().normalize()
                for index in frame.index:
                    candidate = index
                    if hasattr(index, "tzinfo") and getattr(index, "tzinfo", None) is not None:
                        candidate = index.tz_convert(None)
                    candidate = pd.Timestamp(candidate).normalize()
                    if candidate >= today:
                        return candidate.date()
                return None
            return await asyncio.to_thread(fetch_next)
        except Exception as error:
            logger.debug("Earnings date unavailable for %s: %s", symbol, error)
            return None

    async def get_vix_history(self, period: str = "1y") -> pd.DataFrame:
        """Get historical VIX data."""
        try:
            return yf.Ticker("^VIX").history(period=period)
        except Exception:
            return pd.DataFrame()

    async def get_put_call_ratio(self) -> Optional[float]:
        """Get CBOE put/call ratio (free from CBOE website)."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://cdn.cboe.com/data/us/equities/daily_statistics/TotalPutCallRatio.json",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "data" in data and data["data"]:
                        latest = data["data"][0]
                        return float(latest.get("P/C Ratio", 0))
        except Exception as e:
            logger.warning(f"CBOE put/call ratio fetch failed: {e}")
        return None

    async def get_active_stock_universe(self, limit: int = 80) -> List[str]:
        """Discover actively traded US equities from free Yahoo screeners.

        This broadens the static liquid-options seed list with current market
        leaders, movers, and liquid technology names. It is a discovery pass,
        not an assertion that every returned equity has usable options; the
        Advisor still validates chain liquidity before it can recommend one.
        """
        screeners = ("most_actives", "day_gainers", "day_losers", "growth_technology_stocks")

        def fetch_screener(name: str) -> List[str]:
            try:
                response = yf.screen(name, count=75)
                quotes = response.get("quotes", []) if isinstance(response, dict) else []
                return [str(item.get("symbol", "")).upper() for item in quotes if item.get("symbol")]
            except Exception as error:
                logger.warning("Yahoo screener failed for %s: %s", name, error)
                return []

        groups = await asyncio.gather(*(asyncio.to_thread(fetch_screener, name) for name in screeners))
        symbols: List[str] = []
        for group in groups:
            for symbol in group:
                if symbol not in symbols:
                    symbols.append(symbol)
                if len(symbols) >= limit:
                    return symbols
        return symbols

    async def get_sector_performance(self) -> Dict[str, float]:
        """Get sector ETF performance from yfinance."""
        sectors = {
            "XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare",
            "XLE": "Energy", "XLI": "Industrials", "XLP": "Consumer Staples",
            "XLY": "Consumer Discretionary", "XLU": "Utilities",
            "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Communication",
        }
        perf = {}
        for etf, name in sectors.items():
            try:
                ticker = yf.Ticker(etf)
                hist = ticker.history(period="5d")
                if len(hist) >= 2:
                    ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
                    perf[name] = round(ret, 2)
            except Exception:
                pass
        return perf

    def _stock_contract(self, symbol: str):
        from ib_insync import Stock
        return Stock(symbol, "SMART", "USD")

    def _parse_yf_option(self, row, expiry: str, opt_type: str) -> Dict[str, Any]:
        # Yahoo frequently represents unavailable option fields as NaN. A
        # single missing volume/OI value must not discard the entire chain;
        # downstream liquidity rules will reject an individual incomplete leg.
        def finite_number(value: Any, default: float = 0.0) -> float:
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) else default
            except (TypeError, ValueError):
                return default

        try:
            dte = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 0)
        except (TypeError, ValueError):
            dte = 0

        return {
            "symbol": row.get("contractSymbol", ""),
            "strike": finite_number(row.get("strike")),
            "expiry": expiry,
            "dte": dte,
            "option_type": opt_type,
            "bid": finite_number(row.get("bid")),
            "ask": finite_number(row.get("ask")),
            "last": finite_number(row.get("lastPrice")),
            "volume": int(finite_number(row.get("volume"))),
            "open_interest": int(finite_number(row.get("openInterest"))),
            "implied_volatility": finite_number(row.get("impliedVolatility")),
        }
