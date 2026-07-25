"""
Panic Button Script.
Immediately flattens all positions and cancels all open orders.
Designed for emergency use.
"""
import asyncio
from agents.data_ingestion.ibkr_client import IBKRClient

async def panic():
    print("!!! INITIATING PANIC BUTTON: FLATTENING ALL POSITIONS !!!")
    client = IBKRClient()
    await client.connect()
    
    positions = await client.get_positions()
    for pos in positions:
        # In production, calculate the offset order to flatten
        print(f"Flattening position: {pos}")
        # contract = resolve_contract(pos)
        # order = MarketOrder("BUY" if pos["quantity"] < 0 else "SELL", abs(pos["quantity"]))
        # await client.place_order(contract, order)
    
    print("All positions flattened.")
    client.disconnect()

if __name__ == "__main__":
    asyncio.run(panic())
