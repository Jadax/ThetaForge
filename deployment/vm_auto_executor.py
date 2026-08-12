"""Autonomous paper-order executor.

Runs on the same machine as the local Bridge (never over the network --
Bridge is deliberately never exposed publicly). Adds zero new trade-safety
logic of its own: it is a thin, headless replacement for a human clicking
"Send paper order to IBKR" in the dashboard, and every real decision
(quality gates, live quote verification, defined-risk proof, weekly capital
limit) stays exactly where it already lived and was already tested --
agents/trade_engine/recommender.py's gates, and bridge/main.py's
submit_paper_combo, which independently re-verifies everything regardless of
who calls it.

Flow, mirroring dashboard/app/page.tsx's existing openStock + submitPaperTrade
functions exactly:
  1. Poll the Advisor's already-computed, already-gated background scan
     results via GET /api/advisor/notifications (cheap -- reads persisted
     state, no new scan) instead of calling POST /api/advisor/opportunities
     itself, which would duplicate a ~69-second full-universe scan the
     background scanner already ran and is rate-limited to 6 calls/5min.
  2. For each new notification's symbol, POST /api/advisor/recommend
     (single-symbol, diversify_underlyings=false) to get fully-specified
     TradeRecommendation objects with legs -- notifications alone don't
     carry strikes/expiries/quantities.
  3. Submit the best-ranked recommendation for that symbol through the local
     Bridge's POST /orders/submit-combo. Bridge itself re-verifies live
     IBKR quotes, defined-risk, and the weekly capital-limit ledger before
     accepting anything -- this script does not pre-filter on any of that.
  4. Acknowledge the notification either way, so a rejected or
     capital-exhausted signal is not retried every cycle.
  5. If any order was actually placed this cycle, sync and push the public
     journal (see journal_sync_push.sh).

Only runs while the market is open (checked here as a fast-path, and by the
systemd timer that starts/stops this whole stack -- see
market_hours_supervisor.sh) -- both consult the same source of truth, the
Advisor's /api/advisor/scanner/status market_open flag (real NYSE calendar,
not a local approximation), rather than each re-implementing the calendar.
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("auto_executor")

ADVISOR_URL = os.environ["ADVISOR_URL"].rstrip("/")
ADVISOR_API_TOKEN = os.environ["ADVISOR_API_TOKEN"]
BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8002").rstrip("/")
BRIDGE_ACCESS_TOKEN = os.environ["BRIDGE_ACCESS_TOKEN"]
CAPITAL_LIMIT = float(os.getenv("CAPITAL_LIMIT", "5000"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
JOURNAL_SYNC_SCRIPT = os.getenv("JOURNAL_SYNC_SCRIPT", "/opt/thetaforge-bridge/journal_sync_push.sh")

ADVISOR_HEADERS = {"X-ThetaForge-Advisor-Token": ADVISOR_API_TOKEN}
BRIDGE_HEADERS = {"X-ThetaForge-Bridge-Token": BRIDGE_ACCESS_TOKEN, "Content-Type": "application/json"}


def is_market_open(client: httpx.Client) -> bool:
    try:
        response = client.get(f"{ADVISOR_URL}/api/advisor/scanner/status", headers=ADVISOR_HEADERS, timeout=10)
        response.raise_for_status()
        return bool(response.json().get("market_open"))
    except Exception:
        logger.exception("Could not read /scanner/status; treating as closed for this cycle")
        return False


def fetch_actionable_notifications(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(
        f"{ADVISOR_URL}/api/advisor/notifications",
        params={"unacknowledged_only": "true", "limit": 50},
        headers=ADVISOR_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("notifications", [])


def fetch_recommendation(client: httpx.Client, symbol: str) -> dict[str, Any] | None:
    response = client.post(
        f"{ADVISOR_URL}/api/advisor/recommend",
        headers=ADVISOR_HEADERS,
        json={
            "capital": CAPITAL_LIMIT,
            "buying_power": CAPITAL_LIMIT,
            "risk_tolerance": "moderate",
            "watchlist": [symbol],
            "current_positions": [],
            "max_positions": 3,
            "diversify_underlyings": False,
        },
        timeout=60,
    )
    response.raise_for_status()
    recommendations = response.json().get("recommendations", [])
    return recommendations[0] if recommendations else None


def submit_to_bridge(client: httpx.Client, trade: dict[str, Any]) -> tuple[bool, str]:
    legs = [
        {
            "symbol": trade["symbol"],
            "expiry": leg["expiry"],
            "strike": leg["strike"],
            "right": "C" if leg["type"] == "CALL" else "P",
            "action": leg["action"],
        }
        for leg in trade["legs"]
    ]
    try:
        response = client.post(
            f"{BRIDGE_URL}/orders/submit-combo",
            headers=BRIDGE_HEADERS,
            json={
                "legs": legs,
                "quantity": trade["quantity"],
                "capital_limit": CAPITAL_LIMIT,
                "recommendation_id": trade["id"],
                "strategy": trade["strategy"],
            },
            timeout=30,
        )
        body = response.json()
        if response.status_code == 200:
            return True, f"submitted: {body.get('status')} limit={body.get('limit_price')}"
        return False, f"bridge rejected ({response.status_code}): {body.get('detail')}"
    except Exception as error:
        return False, f"bridge call failed: {error}"


def acknowledge(client: httpx.Client, notification_id: str) -> None:
    try:
        client.post(
            f"{ADVISOR_URL}/api/advisor/notifications/{notification_id}/acknowledge",
            headers=ADVISOR_HEADERS,
            timeout=10,
        )
    except Exception:
        logger.exception("Could not acknowledge notification %s", notification_id)


def run_journal_sync() -> None:
    if not Path(JOURNAL_SYNC_SCRIPT).exists():
        logger.warning("Journal sync script not found at %s; skipping", JOURNAL_SYNC_SCRIPT)
        return
    try:
        result = subprocess.run(
            [JOURNAL_SYNC_SCRIPT], capture_output=True, text=True, timeout=120
        )
        logger.info("journal sync: %s", result.stdout.strip() or result.stderr.strip())
    except Exception:
        logger.exception("Journal sync failed")


def run_once(client: httpx.Client) -> int:
    if not is_market_open(client):
        logger.info("Market closed; nothing to do this cycle")
        return 0

    notifications = fetch_actionable_notifications(client)
    if not notifications:
        logger.info("No new actionable notifications")
        return 0

    placed = 0
    for notification in notifications:
        symbol = notification["symbol"]
        try:
            trade = fetch_recommendation(client, symbol)
        except Exception:
            logger.exception("Failed to fetch a recommendation for %s", symbol)
            trade = None

        if trade is None:
            logger.info("%s: no qualifying structure at fetch time (signal likely stale)", symbol)
        else:
            ok, message = submit_to_bridge(client, trade)
            logger.info("%s: %s", symbol, message)
            if ok:
                placed += 1

        acknowledge(client, notification["id"])

    if placed:
        run_journal_sync()
    return placed


def main() -> None:
    logger.info(
        "auto_executor starting: advisor=%s bridge=%s capital_limit=%.2f poll=%ss",
        ADVISOR_URL, BRIDGE_URL, CAPITAL_LIMIT, POLL_INTERVAL_SECONDS,
    )
    with httpx.Client() as client:
        while True:
            try:
                run_once(client)
            except Exception:
                logger.exception("Unhandled error in run_once; continuing")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
