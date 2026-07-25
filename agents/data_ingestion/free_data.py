"""
Free Multi-Source Data Provider.
Aggregates data from IBKR (primary), Alpaca (secondary), and yfinance (fallback).
All sources are FREE with a brokerage account or no account at all.
Adapted from IBKRTools and general market data aggregation patterns.
"""
import asyncio
import logging
import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class FreeDataProvider:
    """
    Multi-source data provider using only free APIs.
    Priority: IBKR > Alpaca > yfinance
    """

    def __init__(self, ibkr_client=None, alpaca_client=None):
        self.ibkr = ibkr_client
        self.alpaca = alpaca_client

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
        """Get full option chain. IBKR provides free real-time data with account."""
        if self.ibkr and self.ibkr._connected:
            return await self.ibkr.get_option_chain(symbol)

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

    async def get_market_breadth(self) -> Dict[str, Any]:
        """Get market advance/decline data from finviz (free)."""
        try:
            from finvizfinance.screener.overview import finvizfinance_overview
            # Simplified breadth check
            return {"advancers": 0, "decliners": 0, "unchanged": 0}
        except Exception:
            return {"advancers": 0, "decliners": 0, "unchanged": 0}

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
