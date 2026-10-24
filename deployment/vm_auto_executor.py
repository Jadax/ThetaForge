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
from datetime import datetime, timezone
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
RECOMMEND_DELAY_SECONDS = float(os.getenv("RECOMMEND_DELAY_SECONDS", "3"))
JOURNAL_SYNC_SCRIPT = os.getenv("JOURNAL_SYNC_SCRIPT", "/opt/thetaforge-bridge/journal_sync_push.sh")

ADVISOR_HEADERS = {"X-ThetaForge-Advisor-Token": ADVISOR_API_TOKEN}
BRIDGE_HEADERS = {"X-ThetaForge-Bridge-Token": BRIDGE_ACCESS_TOKEN, "Content-Type": "application/json"}


def is_market_open(client: httpx.Client) -> bool:
    return _is_market_open(client, ADVISOR_URL, ADVISOR_HEADERS, logger)


def _captured_at() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _num(value: Any, digits: int = 2) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None


def option_conditions(trade: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the Advisor's entry conditions for the public journal.

    Sent with the order so the Bridge stores it on the ledger record, letting
    scripts/sync_journal.py publish *why* the structure was entered (regime,
    IV rank, expected move, scores, entry/exit rules) beside the fill receipt.
    """
    return {
        "engine": "options",
        "captured_at": _captured_at(),
        "regime": trade.get("market_regime"),
        "vix": _num(trade.get("vix"), 2),
        "iv_rank": _num(trade.get("iv_rank"), 1),
        "expected_move_pct": _num(trade.get("expected_move_pct"), 2),
        "composite_score": _num(trade.get("composite_score"), 1),
        "confidence_score": _num(trade.get("confidence_score"), 1),
        "pop": _num(trade.get("probability_of_profit"), 1),
        "edge_pct": _num(trade.get("theoretical_edge_pct"), 2),
        "reasoning": trade.get("reasoning"),
        "entry_rules": trade.get("entry_rules") or None,
        "exit_rules": trade.get("exit_rules") or None,
    }


def equity_conditions(trade: dict[str, Any], notification: dict[str, Any]) -> dict[str, Any]:
    """Same journal snapshot for a long stock/ETF entry: the equity Brain's
    score, read, trend, RSI/ADX/ATR, entry price, and risk tilt."""
    return {
        "engine": "equity",
        "captured_at": _captured_at(),
        "score": _num(trade.get("score"), 1),
        "signal": trade.get("read"),
        "trend": trade.get("trend"),
        "rsi_14": _num(trade.get("rsi_14"), 1),
        "adx_14": _num(trade.get("adx_14"), 1),
        "atr_14": _num(trade.get("atr_14"), 2),
        "price": _num(trade.get("price") or trade.get("entry_price"), 2),
        "risk_tilt": notification.get("market_risk_tilt"),
        "reasoning": trade.get("reasoning"),
    }


def fetch_actionable_notifications(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(
        f"{ADVISOR_URL}/api/advisor/notifications",
        params={"unacknowledged_only": "true", "limit": 50},
        headers=ADVISOR_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("notifications", [])


def fetch_recommendation(
    client: httpx.Client, symbol: str, current_positions: list[dict[str, Any]] | None = None
) -> tuple[dict[str, Any] | None, str]:
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
            # Covered-call inventory (shares not owned by the equity engine);
            # the Recommender only scores CCs against >= 100 owned shares.
            "current_positions": current_positions or [],
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
                "conditions": option_conditions(trade),
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


# A DAY order still working at IBKR holds the name just as a fill does: the
# duplicate guard used to count only filled records, so a pending DIA entry
# was invisible and DIA was bought again 30 minutes later (Oct 20 2026: three
# DIA entries in one session).
PENDING_ORDER_STATUSES = {"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted"}


def fetch_ledger_orders(client: httpx.Client) -> list[dict[str, Any]] | None:
    """The Bridge ledger records, or None when it can't be read (callers then
    fail closed rather than treat an unreadable ledger as an empty book)."""
    try:
        response = client.get(f"{BRIDGE_URL}/orders", headers=BRIDGE_HEADERS, timeout=15)
        response.raise_for_status()
        return response.json().get("orders", [])
    except Exception:
        logger.exception("Could not read the Bridge ledger")
        return None


def held_symbols(records: list[dict[str, Any]], asset_class: str) -> list[str]:
    """Symbols with an open (filled, not closed) or still-working entry."""
    symbols: list[str] = []
    for record in records:
        if record.get("close_of") or record.get("closed_by"):
            continue
        is_equity = record.get("asset_class") == "equity"
        if is_equity != (asset_class == "equity"):
            continue
        filled = float(record.get("filled", 0) or 0) > 0
        working = record.get("status") in PENDING_ORDER_STATUSES
        if (filled or working) and record.get("symbol") and record["symbol"] not in symbols:
            symbols.append(record["symbol"])
    return symbols


def fetch_open_equity_symbols(client: httpx.Client) -> list[str]:
    """Symbols of open or still-working equity entries from the Bridge ledger,
    so the equity recommender can enforce its same-name and sector caps."""
    records = fetch_ledger_orders(client)
    return held_symbols(records, "equity") if records is not None else []


def fetch_wheel_share_positions(client: httpx.Client, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stock positions available to back a covered call: IBKR share holdings
    minus shares the equity engine holds for its own (stop-managed) entries.

    Shares assigned from a CSP are the wheel's covered-call inventory. Equity-
    engine shares must never back a call: the equity manager can sell them on
    a stop at any moment, which would leave the call naked.
    """
    try:
        response = client.get(f"{BRIDGE_URL}/positions", headers=BRIDGE_HEADERS, timeout=15)
        response.raise_for_status()
        positions = response.json()
    except Exception:
        logger.exception("Could not read Bridge positions for covered-call inventory")
        return []
    engine_shares: dict[str, float] = {}
    for record in records:
        if record.get("asset_class") != "equity" or record.get("close_of") or record.get("closed_by"):
            continue
        symbol = str(record.get("symbol", "")).upper()
        engine_shares[symbol] = engine_shares.get(symbol, 0.0) + float(record.get("filled", 0) or 0)
    wheel = []
    for position in positions:
        if position.get("contract_type") != "STK":
            continue
        symbol = str(position.get("symbol", "")).upper()
        free_shares = float(position.get("position", 0) or 0) - engine_shares.get(symbol, 0.0)
        if free_shares >= 100:
            wheel.append({"symbol": symbol, "position": free_shares})
    return wheel


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


def submit_stock_to_bridge(
    client: httpx.Client,
    trade: dict[str, Any],
    notification: dict[str, Any] | None = None,
) -> tuple[bool, str]:
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
                "conditions": equity_conditions(trade, notification or {}),
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
            ok, message = submit_stock_to_bridge(client, trade, notification)
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
        if RECOMMEND_DELAY_SECONDS > 0 and notification != notifications[-1]:
            time.sleep(RECOMMEND_DELAY_SECONDS)
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
        records = fetch_ledger_orders(client)
        open_option_symbols = held_symbols(records, "options") if records is not None else []
        wheel_shares = fetch_wheel_share_positions(client, records) if records is not None else []
        for notification in notifications:
            symbol = notification["symbol"]
            detail = ""
            action = "skipped"
            trade = None
            if records is None:
                # Fail closed: without the ledger the same-name guard can't run.
                detail = "ledger_unreadable"
            elif symbol in open_option_symbols:
                # One option position per underlying (the wheel never stacks a
                # second CSP on a name it's already short premium on).
                detail = "already_open: an option position on this underlying is open or working"
            else:
                try:
                    trade, detail = fetch_recommendation(client, symbol, wheel_shares)
                except Exception as error:
                    logger.exception("Failed to fetch a recommendation for %s", symbol)
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
                    open_option_symbols.append(symbol)

            decisions.append({
                "engine": "options",
                "symbol": symbol,
                "notification_id": notification.get("id"),
                "action": action,
                "detail": detail,
            })
            acknowledge(client, notification["id"])
            if RECOMMEND_DELAY_SECONDS > 0 and notification != notifications[-1]:
                time.sleep(RECOMMEND_DELAY_SECONDS)
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
