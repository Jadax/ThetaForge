"""
ThetaForge Orchestrator: The central nervous system of the trading platform.
Manages agent lifecycle, task scheduling, and API endpoints.
Adapted from general microservices architectures and FastAPI best practices.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.routes import health, advisor
from agents.trade_engine.background_scanner import get_background_scanner
from agents.equity_trader.equity_scanner import get_background_equity_scanner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown events."""
    logger.info("ThetaForge Orchestrator starting up...")

    # Start background brain scanner
    try:
        scanner = await get_background_scanner()
        await scanner.start()
        logger.info("Background Brain Scanner started (300s interval)")
    except Exception as e:
        logger.warning(f"Could not start background scanner: {e}")

    # Start the background equity scanner (stock/ETF engine)
    try:
        equity_scanner = await get_background_equity_scanner()
        await equity_scanner.start()
        logger.info("Background Equity Scanner started (300s interval)")
    except Exception as e:
        logger.warning(f"Could not start background equity scanner: {e}")

    yield

    logger.info("ThetaForge Orchestrator shutting down...")
    try:
        scanner = await get_background_scanner()
        await scanner.stop()
        logger.info("Background Brain Scanner stopped")
    except Exception:
        pass
    try:
        equity_scanner = await get_background_equity_scanner()
        await equity_scanner.stop()
        logger.info("Background Equity Scanner stopped")
    except Exception:
        pass

app = FastAPI(
    title="ThetaForge Orchestrator",
    description="Multi-agent AI-augmented options and equity trading intelligence system.",
    version="1.17.2",
    lifespan=lifespan
)

allowed_origins = [
    origin.strip() for origin in os.getenv(
        "DASHBOARD_ORIGINS",
        "https://thetaforge-terminal.pages.dev,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-ThetaForge-Advisor-Token"],
)

# Include routers. Only the health probe is public; it carries no account,
# market, or configuration data and the platform healthcheck depends on it.
# The advisor router applies the same dependency itself, alongside its own
# per-endpoint rate limits.
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(advisor.router, tags=["advisor"])

@app.get("/")
async def root():
    return {"message": "ThetaForge Orchestrator is running."}
