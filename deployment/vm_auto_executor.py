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
      capital-exhausted signal is not retried every cycle, and record what
      happened (placed / skipped+reason / bridge-rejected+message) to the
      Advisor's persisted decision trail (POST /api/advisor/executor/
      decisions) — observability only, never a gate.
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
import time
from typing import Any

import httpx

from _vm_common import is_market_open as _is_market_open, run_journal_sync as _run_journal_sync

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
    return _is_market_open(client, ADVISOR_URL, ADVISOR_HEADERS, logger)


def fetch_actionable_notifications(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(
        f"{ADVISOR_URL}/api/advisor/notifications",
        params={"unacknowledged_only": "true", "limit": 50},
        headers=ADVISOR_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("notifications", [])


def fetch_recommendation(client: httpx.Client, symbol: str) -> tuple[dict[str, Any] | None, str]:
    """Fetch a fully-specified recommendation for one symbol.

    Returns (trade, detail): detail is empty on success and otherwise a short
    reason string (regime/VIX/warnings from the Advisor) that explains why no
    structure qualified — recorded in the decision trail so a silent
    zero-trade stretch is diagnosable without VM log access.
    """
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
    body = response.json()
    recommendations = body.get("recommendations", [])
    if recommendations:
        return recommendations[0], ""
    context = body.get("market_context") or {}
    regime = context.get("regime")
    vix = context.get("vix")
    warnings = "; ".join(str(w) for w in (body.get("warnings") or [])[:3])
    return None, (
        f"no_qualified_recommendation regime={regime} "
        f"vix={vix} warnings=[{warnings}]"
    )


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


# ── Equity engine (stock/ETF longs) ──────────────────────────────────────


def fetch_open_equity_symbols(client: httpx.Client) -> list[str]:
    """Symbols of open, filled, non-closed positions from the Bridge ledger,
    so the equity recommender can enforce its sector correlation cap."""
    try:
        ledger = client.get(f"{BRIDGE_URL}/orders", headers=BRIDGE_HEADERS, timeout=15)
        ledger.raise_for_status()
        symbols = []
        for record in ledger.json().get("orders", []):
            if record.get("close_of") or record.get("closed_by"):
                continue
            if float(record.get("filled", 0) or 0) <= 0:
                continue
            if record.get("asset_class") != "equity":
                continue
            symbols.append(record["symbol"])
        return symbols
    except Exception:
        logger.exception("Could not read the Bridge ledger for open equity symbols")
        return []


def fetch_equity_notifications(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(
        f"{ADVISOR_URL}/api/advisor/equity/notifications",
        params={"unacknowledged_only": "true", "limit": 50},
        headers=ADVISOR_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("notifications", [])


def fetch_equity_recommendation(
    client: httpx.Client, symbol: str, open_symbols: list[str]
) -> tuple[dict[str, Any] | None, str]:
    """Fetch a gated, sized equity recommendation.

    Returns (trade, detail): detail carries the Advisor's own gate code and
    reason when the fresh re-analysis does not qualify, so the decision trail
    shows why a scan-time buy did not become an order.
    """
    response = client.post(
        f"{ADVISOR_URL}/api/advisor/equity/recommend",
        headers=ADVISOR_HEADERS,
        json={
            "symbol": symbol,
            "capital": CAPITAL_LIMIT,
            "current_positions": open_symbols,
        },
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    recommendation = body.get("recommendation")
    reason = str(body.get("reason") or "")
    if recommendation and recommendation.get("gate") is None and int(recommendation.get("shares", 0) or 0) > 0:
        return recommendation, ""
    gate = (recommendation or {}).get("gate") or reason or "unknown"
    detail = (recommendation or {}).get("reasoning") or reason
    return None, f"gate={gate} reason={detail}"


def submit_stock_to_bridge(client: httpx.Client, trade: dict[str, Any]) -> tuple[bool, str]:
    try:
        response = client.post(
            f"{BRIDGE_URL}/orders/stock",
            headers=BRIDGE_HEADERS,
            json={
                "symbol": trade["symbol"],
                "shares": trade["shares"],
                "capital_limit": CAPITAL_LIMIT,
                "stop_price": trade["stop_price"],
                "target_price": trade.get("target_price"),
                "recommendation_id": trade["id"],
                "strategy": trade["strategy"],
            },
            timeout=30,
        )
        body = response.json()
        if response.status_code == 200:
            return True, f"stock submitted: {body.get('status')} entry={body.get('entry_price')} stop={body.get('stop_price')}"
        return False, f"bridge rejected stock ({response.status_code}): {body.get('detail')}"
    except Exception as error:
        return False, f"bridge stock call failed: {error}"


def acknowledge_equity(client: httpx.Client, notification_id: str) -> None:
    try:
        client.post(
            f"{ADVISOR_URL}/api/advisor/equity/notifications/{notification_id}/acknowledge",
            headers=ADVISOR_HEADERS,
            timeout=10,
        )
    except Exception:
        logger.exception("Could not acknowledge equity notification %s", notification_id)


def post_decisions(client: httpx.Client, decisions: list[dict[str, Any]]) -> None:
    """Persist this cycle's decisions to the Advisor's decision trail.

    Best-effort observability: a failed POST is logged and never affects
    trading. The trail answers *why* notifications did not become orders
    without requiring VM log access.
    """
    if not decisions:
        return
    try:
        client.post(
            f"{ADVISOR_URL}/api/advisor/executor/decisions",
            headers=ADVISOR_HEADERS,
            json={"decisions": decisions},
            timeout=15,
        )
    except Exception:
        logger.exception("Could not post %d executor decision(s)", len(decisions))


def run_equity_once(client: httpx.Client) -> int:
    """Process pending equity notifications: recommend, submit via the Bridge,
    acknowledge either way. Returns the number of orders placed."""
    notifications = fetch_equity_notifications(client)
    if not notifications:
        return 0

    open_symbols = fetch_open_equity_symbols(client)
    placed = 0
    decisions: list[dict[str, Any]] = []
    for notification in notifications:
        symbol = notification["symbol"]
        detail = ""
        action = "skipped"
        try:
            trade, detail = fetch_equity_recommendation(client, symbol, open_symbols)
        except Exception as error:
            logger.exception("Failed to fetch an equity recommendation for %s", symbol)
            trade = None
            detail = f"fetch_error: {error}"

        if trade is None:
            logger.info("%s: no qualifying equity structure at fetch time (%s)", symbol, detail)
        else:
            ok, message = submit_stock_to_bridge(client, trade)
            logger.info("%s: %s", symbol, message)
            action = "placed" if ok else "bridge_rejected"
            detail = message
            if ok:
                placed += 1
                open_symbols.append(symbol)

        decisions.append({
            "engine": "equity",
            "symbol": symbol,
            "notification_id": notification.get("id"),
            "action": action,
            "detail": detail,
        })
        acknowledge_equity(client, notification["id"])
    post_decisions(client, decisions)
    return placed


def run_journal_sync() -> None:
    _run_journal_sync(JOURNAL_SYNC_SCRIPT, logger)


def run_once(client: httpx.Client) -> int:
    if not is_market_open(client):
        logger.info("Market closed; nothing to do this cycle")
        return 0

    placed = 0
    decisions: list[dict[str, Any]] = []

    notifications = fetch_actionable_notifications(client)
    if notifications:
        for notification in notifications:
            symbol = notification["symbol"]
            detail = ""
            action = "skipped"
            try:
                trade, detail = fetch_recommendation(client, symbol)
            except Exception as error:
                logger.exception("Failed to fetch a recommendation for %s", symbol)
                trade = None
                detail = f"fetch_error: {error}"

            if trade is None:
                logger.info("%s: no qualifying structure at fetch time (%s)", symbol, detail)
            else:
                ok, message = submit_to_bridge(client, trade)
                logger.info("%s: %s", symbol, message)
                action = "placed" if ok else "bridge_rejected"
                detail = message
                if ok:
                    placed += 1

            decisions.append({
                "engine": "options",
                "symbol": symbol,
                "notification_id": notification.get("id"),
                "action": action,
                "detail": detail,
            })
            acknowledge(client, notification["id"])
        post_decisions(client, decisions)
    else:
        logger.info("No new actionable notifications")

    try:
        placed += run_equity_once(client)
    except Exception:
        logger.exception("Equity cycle failed; continuing")

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
