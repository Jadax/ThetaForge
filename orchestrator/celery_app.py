"""
Celery application configuration for background task processing.
Manages asynchronous agent execution and scheduled scans.
All FREE - no paid API dependencies.
"""
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery(
    "thetaforge",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "agents.data_ingestion.tasks",
        "agents.scanner.tasks",
        "agents.strategies.tasks",
        "agents.execution.tasks",
        "agents.volatility.tasks",
        "agents.flow_analysis.tasks",
        "agents.sentiment.tasks",
        "agents.technical.tasks",
        "agents.trade_engine.tasks",
    ]
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        # Scanner: Every 30 minutes during market hours
        "run-scanner-every-30-mins": {
            "task": "agents.scanner.tasks.run_full_scan",
            "schedule": 1800.0,
        },
        # GEX Update: Every 15 minutes (intraday dealer positioning changes)
        "update-gex-15-mins": {
            "task": "agents.flow_analysis.tasks.update_gex",
            "schedule": 900.0,
        },
        # Flow Scan: Every 10 minutes (catch unusual activity early)
        "scan-flow-10-mins": {
            "task": "agents.flow_analysis.tasks.scan_unusual_activity",
            "schedule": 600.0,
        },
        # Reddit Sentiment: Every hour
        "scan-reddit-hourly": {
            "task": "agents.sentiment.tasks.scan_reddit",
            "schedule": 3600.0,
        },
        # IV Metrics: Daily at market open
        "update-iv-metrics-daily": {
            "task": "agents.volatility.tasks.update_iv_metrics",
            "schedule": 86400.0,
        },
        # Technical Indicators: Daily
        "update-technical-daily": {
            "task": "agents.technical.tasks.update_technical_indicators",
            "schedule": 86400.0,
        },
        # Full Multi-Layer Scan: 9:35 AM ET (after market open settlement)
        "full-scan-market-open": {
            "task": "agents.scanner.tasks.run_full_scan",
            "schedule": "35 13 * * 1-5",  # 13:35 UTC = 9:35 AM ET
        },
        # Trade Advisor: Generate recommendations every 30 min during market hours
        "advisor-recommendations": {
            "task": "agents.trade_engine.tasks.generate_advisory",
            "schedule": 1800.0,
            "kwargs": {
                "capital": 100000,
                "buying_power": 200000,
                "risk_tolerance": "moderate",
            },
        },
        # ROI Comparison: Every hour (OptionsellerROI feature)
        "roi-comparison-hourly": {
            "task": "agents.trade_engine.tasks.compare_opportunities",
            "schedule": 3600.0,
        },
    },
)
