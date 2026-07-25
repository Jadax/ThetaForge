"""
IBKR Data Ingestion Agent.
Wraps ib_insync for real-time streaming and falls back to ibapi for complex operations.
Adapted from IBKRTools and ibkr-llm-assistant projects for robust connection handling.
"""
import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from ib_insync import IB, Stock, Option, Contract, MarketOrder, LimitOrder
from ib_insync import util

logger = logging.getLogger(__name__)

class IBKRClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 4001, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self._connected = False

    async def connect(self):
        """Establish connection to IBKR TWS/Gateway with auto-reconnect."""
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self._connected = True
            logger.info(f"Connected to IBKR at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {e}")
            self._connected = False
            # Schedule retry with exponential backoff
            await asyncio.sleep(5)
            await self.connect()

    def disconnect(self):
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IBKR.")

    async def get_option_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch option chain for a given underlying symbol."""
        if not self._connected:
            await self.connect()
        
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        chains = self.ib.reqSecDefOptParams(contract.symbol, "", contract.secType, contract.conId)
        
        options_data = []
        for chain in chains:
            for expiry in chain.expirations:
                for strike in chain.strikes:
                    # Call
                    call = Option(symbol, expiry, strike, "C", "SMART")
                    # Put
                    put = Option(symbol, expiry, strike, "P", "SMART")
                    options_data.append({"call": call, "put": put, "expiry": expiry, "strike": strike})
        
        return options_data

    async def place_order(self, contract: Contract, order: MarketOrder | LimitOrder):
        """Place an order with IBKR."""
        if not self._connected:
            logger.error("Cannot place order: Not connected to IBKR.")
            return
        
        trade = self.ib.placeOrder(contract, order)
        logger.info(f"Order placed: {trade}")
        return trade

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current positions."""
        if not self._connected:
            await self.connect()
        return [pos.__dict__ for pos in self.ib.positions()]

    async def get_portfolio_value(self) -> float:
        """Fetch total portfolio value."""
        if not self._connected:
            await self.connect()
        account_summary = self.ib.accountSummary()
        for item in account_summary:
            if item.tag == "NetLiquidation" and item.currency == "USD":
                return float(item.value)
        return 0.0
