"""
Mock IBKR Client for testing.
Simulates IBKR responses without requiring a live connection.
"""
import asyncio
from typing import List, Dict, Any

class MockIBKRClient:
    def __init__(self):
        self._connected = False

    async def connect(self):
        self._connected = True
        print("Mock IBKR Client connected.")

    def disconnect(self):
        self._connected = False
        print("Mock IBKR Client disconnected.")

    async def get_option_chain(self, symbol: str) -> List[Dict[str, Any]]:
        return [
            {"call": "MOCK_CALL", "put": "MOCK_PUT", "expiry": "2026-08-15", "strike": 100.0}
        ]

    async def place_order(self, contract, order):
        print(f"Mock Order Placed: {order}")
        return {"status": "Filled"}

    async def get_positions(self) -> List[Dict[str, Any]]:
        return []

    async def get_portfolio_value(self) -> float:
        return 100000.0
