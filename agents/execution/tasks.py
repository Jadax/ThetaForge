"""
Celery tasks for Execution Agent.
Handles order placement and management.
"""
from orchestrator.celery_app import app

@app.task(name="agents.execution.tasks.execute_trade_signal")
def execute_trade_signal(signal_id: str):
    """Execute a specific trade signal."""
    print(f"Executing trade signal {signal_id}...")
    return {"status": "trade_executed", "signal_id": signal_id}

@app.task(name="agents.execution.tasks.cancel_all_orders")
def cancel_all_orders():
    """Cancel all open orders (part of panic button)."""
    print("Cancelling all open orders...")
    return {"status": "orders_cancelled"}
