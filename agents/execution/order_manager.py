"""
Execution Agent for order management.
Handles IBKR order placement, stop-losses, and take-profits.
Adapted from IBKR-trader and general execution best practices.
"""
import logging
from typing import Dict, Any
from agents.data_ingestion.ibkr_client import IBKRClient
from agents.strategies.base_strategy import TradeSignal

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self, ibkr_client: IBKRClient):
        self.client = ibkr_client

    async def execute_signal(self, signal: TradeSignal) -> bool:
        """Execute a trade signal by placing orders with IBKR."""
        if not self.client._connected:
            await self.client.connect()

        try:
            # Placeholder for actual contract resolution
            # In production, use self.client.get_option_chain to find the exact contract
            from ib_insync import Option, LimitOrder
            
            contract = Option(signal.symbol, signal.expiry, signal.strike, signal.option_type, "SMART")
            self.client.ib.qualifyContracts(contract)

            if signal.action == "SELL":
                order = LimitOrder("SELL", signal.quantity, signal.limit_price or 0.0)
            else:
                order = LimitOrder("BUY", signal.quantity, signal.limit_price or 0.0)

            trade = await self.client.place_order(contract, order)
            
            # Attach stop-loss if specified
            if signal.stop_loss and signal.action == "BUY":
                stop_order = LimitOrder("SELL", signal.quantity, signal.stop_loss)
                await self.client.place_order(contract, stop_order)
            elif signal.stop_loss and signal.action == "SELL":
                # For short positions, stop-loss is a buy stop
                stop_order = LimitOrder("BUY", signal.quantity, signal.stop_loss)
                await self.client.place_order(contract, stop_order)

            logger.info(f"Executed signal: {signal}")
            return True
        except Exception as e:
            logger.error(f"Failed to execute signal: {e}")
            return False
