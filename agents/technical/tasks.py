# Celery tasks for Technical Analysis
from orchestrator.celery_app import app

@app.task(name="agents.technical.tasks.update_technical_indicators")
def update_technical_indicators():
    """Update technical indicators for all tracked symbols."""
    print("Updating technical indicators...")
    return {"status": "technical_updated"}
