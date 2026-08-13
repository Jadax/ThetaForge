"""Autonomous paper position manager.

Runs on the same machine as the local Bridge (never over the network --
Bridge is deliberately never exposed publicly). Applies the exit framework
from agents/trade_engine/trade_manager.py to open short-premium paper
positions and optionally submits the closing orders it recommends.

Division of responsibility, so no second decision or order path exists:

  * The Advisor's POST /api/advisor/positions/management endpoint is the only
    place exit decisions are made. It refreshes live spot and short-leg mid
    from free data and runs the rule engine (50% take-profit, 21-DTE gamma
    window, 2x-credit stop, pre-earnings and pre-macro exits, tested-strike
    review). This script sends it open positions from the Bridge ledger and
    relays nothing else.
  * The Bridge's POST /orders/close-combo is the only exit execution path. It
    mirrors the entry's own ledger record, re-verifies live IBKR bid/ask,
    proves structure continuity with a previously-proven defined-risk entry,
    and refuses fills that would pay the position. This script never builds
    legs itself.
  * Closing orders land in the same paper-order ledger as entries, so the
    public journal's lifecycle sync (scripts/sync_journal.py) folds them into
    the parent entry as a `closed` status with the exit receipt and realized
    P&L -- no phantom new positions.

Safety rails:
  * Exits are advisory by default. Set AUTO_CLOSE_ENABLED=true to let the
    loop submit the closing orders it recommends. review_tested actions are
    always surfaced for a human and never auto-submitted.
  * Only runs while the market is open (same source of truth as the executor:
    the Advisor's /api/advisor/scanner/status market_open flag).
"""
import logging
import os
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("auto_manager")

ADVISOR_URL = os.environ["ADVISOR_URL"].rstrip("/")
ADVISOR_API_TOKEN = os.environ["ADVISOR_API_TOKEN"]
BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8002").rstrip("/")
BRIDGE_ACCESS_TOKEN = os.environ["BRIDGE_ACCESS_TOKEN"]
CAPITAL_LIMIT = float(os.getenv("CAPITAL_LIMIT", "5000"))
AUTO_CLOSE_ENABLED = os.getenv("AUTO_CLOSE_ENABLED", "false").lower() == "true"
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
JOURNAL_SYNC_SCRIPT = os.getenv("JOURNAL_SYNC_SCRIPT", "/opt/thetaforge-bridge/journal_sync_push.sh")

ADVISOR_HEADERS = {"X-ThetaForge-Advisor-Token": ADVISOR_API_TOKEN}
BRIDGE_HEADERS = {"X-ThetaForge-Bridge-Token": BRIDGE_ACCESS_TOKEN, "Content-Type": "application/json"}

# Mirror bridge/main.py's RESERVING_ORDER_STATUSES so we only manage positions
# whose capital is actually live (open or filled) on the paper account.
RESERVING_ORDER_STATUSES = {
    "ApiPending", "PendingSubmit", "PreSubmitted", "Submitted",
    "PendingCancel", "Filled", "Unknown",
}
CLOSE_ACTIONS = {"close_profit", "close_time", "close_loss", "close_pre_earnings", "close_pre_macro"}
MANAGED_TOKENS = (
    "bull_put", "bear_call", "iron_condor", "cash_secured",
    "covered_call", "strangle", "straddle", "condor",
)


def is_market_open(client: httpx.Client) -> bool:
    try:
        response = client.get(f"{ADVISOR_URL}/api/advisor/scanner/status", headers=ADVISOR_HEADERS, timeout=10)
        response.raise_for_status()
        return bool(response.json().get("market_open"))
    except Exception:
        logger.exception("Could not read /scanner/status; treating as closed for this cycle")
        return False


def fetch_ledger(client: httpx.Client) -> dict[str, Any]:
    response = client.get(f"{BRIDGE_URL}/orders", headers=BRIDGE_HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def is_manageable(strategy: Optional[str]) -> bool:
    key = (strategy or "").lower()
    return any(token in key for token in MANAGED_TOKENS)


def open_positions(client: httpx.Client) -> tuple[list[dict[str, Any]], float, float]:
    """Open entry positions from the ledger plus realized P&L / reserved capital."""
    ledger = fetch_ledger(client)
    records = ledger.get("orders", [])
    open_entries = []
    realized_pnl = 0.0
    for record in records:
        if record.get("close_of"):
            try:
                realized_pnl += float(record.get("realized_pnl", 0) or 0)
            except (TypeError, ValueError):
                pass
            continue
        if not record.get("recommendation_id"):
            continue
        if record.get("status", "Unknown") not in RESERVING_ORDER_STATUSES:
            continue
        if float(record.get("filled", 0) or 0) <= 0:
            continue
        if record.get("closed_by"):
            continue
        if not is_manageable(record.get("strategy")):
            continue
        open_entries.append(record)
    return open_entries, realized_pnl, float(ledger.get("capital_reserved", 0) or 0)


def _short_strike(legs: List[Dict[str, Any]], strategy: str) -> float:
    """Short (sold) strike driving the tested-strike check.

    Vertical spreads carry one sold leg; iron condors carry one per wing, and
    the downside wing is the short-strike reference the rule engine checks.
    """
    sells = [float(leg.get("strike", 0)) for leg in legs if leg.get("action") == "SELL" and float(leg.get("strike", 0)) > 0]
    if not sells:
        return 0.0
    if "condor" in (strategy or "").lower():
        return min(sells)
    return sells[0]


def _long_strike(legs: List[Dict[str, Any]], strategy: str) -> float:
    buys = [float(leg.get("strike", 0)) for leg in legs if leg.get("action") == "BUY" and float(leg.get("strike", 0)) > 0]
    if not buys:
        return 0.0
    if "condor" in (strategy or "").lower():
        return min(buys)
    return buys[0]


def build_position_inputs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    today = date.today()
    inputs = []
    for record in records:
        legs = record.get("legs", [])
        expiry = str(legs[0].get("expiry")) if legs and legs[0].get("expiry") else None
        dte = None
        if expiry:
            try:
                dte = max((date.fromisoformat(expiry) - today).days, 0)
            except ValueError:
                dte = None
        inputs.append({
            "symbol": record["symbol"],
            "strategy": record.get("strategy") or "bull_put",
            "short_strike": _short_strike(legs, record.get("strategy")),
            "long_strike": _long_strike(legs, record.get("strategy")),
            "expiry": expiry,
            "credit_received": float(record.get("net_credit", 0) or 0),
            "quantity": int(record.get("quantity", 1) or 1),
            "dte": dte,
        })
    return inputs


def request_management(client: httpx.Client, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    response = client.post(
        f"{ADVISOR_URL}/api/advisor/positions/management",
        headers=ADVISOR_HEADERS,
        json={"positions": positions, "capital": CAPITAL_LIMIT},
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("actions", [])


def submit_close(client: httpx.Client, ledger_id: str, reason: str) -> tuple[bool, str]:
    try:
        response = client.post(
            f"{BRIDGE_URL}/orders/close-combo",
            headers=BRIDGE_HEADERS,
            json={"ledger_id": ledger_id, "reason": reason, "capital_limit": CAPITAL_LIMIT},
            timeout=30,
        )
        body = response.json()
        if response.status_code == 200:
            return True, f"close submitted: {body.get('status')} cost={body.get('cost_to_close')} pnl={body.get('realized_pnl')}"
        return False, f"bridge rejected close ({response.status_code}): {body.get('detail')}"
    except Exception as error:
        return False, f"bridge close call failed: {error}"


# ── Equity engine (stock/ETF longs) ──────────────────────────────────────

EQUITY_CLOSE_ACTIONS = {
    "close_stop", "close_trail", "close_profit", "close_time",
    "close_pre_earnings", "close_pre_macro",
}


def open_equity_positions(client: httpx.Client) -> list[dict[str, Any]]:
    """Open long equity entries from the Bridge ledger."""
    ledger = fetch_ledger(client)
    records = ledger.get("orders", [])
    open_entries = []
    for record in records:
        if record.get("close_of"):
            continue
        if record.get("asset_class") != "equity":
            continue
        if record.get("status", "Unknown") not in RESERVING_ORDER_STATUSES:
            continue
        if float(record.get("filled", 0) or 0) <= 0:
            continue
        if record.get("closed_by"):
            continue
        open_entries.append(record)
    return open_entries


def build_equity_position_inputs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    inputs = []
    for record in records:
        inputs.append({
            "symbol": record["symbol"],
            "entry_price": float(record.get("entry_price") or record.get("average_fill_price") or 0),
            "stop_price": float(record.get("stop_price") or 0),
            "target_price": record.get("target_price"),
            "highest_high": float(record.get("highest_high") or record.get("entry_price") or 0),
            "risk_per_share": float(record.get("risk_per_share") or 0),
            "shares": int(record.get("quantity", 1) or 1),
            "opened_at": record.get("submitted_at"),
        })
    return inputs


def request_equity_management(client: httpx.Client, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    response = client.post(
        f"{ADVISOR_URL}/api/advisor/equity/positions/management",
        headers=ADVISOR_HEADERS,
        json={"positions": positions, "capital": CAPITAL_LIMIT},
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("actions", [])


def submit_stock_close(client: httpx.Client, ledger_id: str, reason: str) -> tuple[bool, str]:
    try:
        response = client.post(
            f"{BRIDGE_URL}/orders/close-stock",
            headers=BRIDGE_HEADERS,
            json={"ledger_id": ledger_id, "reason": reason, "capital_limit": CAPITAL_LIMIT},
            timeout=30,
        )
        body = response.json()
        if response.status_code == 200:
            return True, f"stock close submitted: {body.get('status')} cost={body.get('cost_to_close')} pnl={body.get('realized_pnl')}"
        return False, f"bridge rejected stock close ({response.status_code}): {body.get('detail')}"
    except Exception as error:
        return False, f"bridge stock close call failed: {error}"


def update_highest_high(client: httpx.Client, ledger_id: str, highest_high: float) -> None:
    try:
        client.post(
            f"{BRIDGE_URL}/orders/{ledger_id}/position-meta",
            headers=BRIDGE_HEADERS,
            json={"ledger_id": ledger_id, "highest_high": highest_high},
            timeout=10,
        )
    except Exception:
        logger.exception("Could not ratchet highest_high for %s", ledger_id)


def run_equity_once(client: httpx.Client) -> int:
    """Manage open long equity positions: request exits, optionally close them
    via the Bridge, and ratchet trailing anchors. Returns closes submitted."""
    entries = open_equity_positions(client)
    if not entries:
        logger.info("No open manageable equity positions")
        return 0

    positions = build_equity_position_inputs(entries)
    try:
        actions = request_equity_management(client, positions)
    except Exception:
        logger.exception("Equity management decision request failed; skipping this cycle")
        return 0

    closes_submitted = 0
    for record, action in zip(entries, actions):
        symbol = record["symbol"]
        action_name = action.get("action", "hold")
        reason = action.get("reason", "")

        updated_high = action.get("highest_high")
        if updated_high:
            update_highest_high(client, str(record["id"]), float(updated_high))

        if action_name in EQUITY_CLOSE_ACTIONS:
            if AUTO_CLOSE_ENABLED:
                ok, message = submit_stock_close(client, str(record["id"]), action_name)
                logger.info("%s: %s", symbol, message)
                if ok:
                    closes_submitted += 1
            else:
                logger.info(
                    "%s: would %s (%s) — advisory mode, AUTO_CLOSE_ENABLED=false",
                    symbol, action_name, reason,
                )
        else:
            logger.info("%s: hold (%s)", symbol, reason)

    logger.info("equity cycle done: %d open, %d close(s) submitted (auto_close=%s)",
                len(entries), closes_submitted, AUTO_CLOSE_ENABLED)
    return closes_submitted


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

    try:
        entries, realized_pnl, reserved = open_positions(client)
    except Exception:
        logger.exception("Could not read the Bridge ledger; skipping this cycle")
        return 0

    if not entries:
        logger.info("No open manageable paper positions")
        return 0

    positions = build_position_inputs(entries)
    try:
        actions = request_management(client, positions)
    except Exception:
        logger.exception("Management decision request failed; skipping this cycle")
        return 0

    closes_submitted = 0
    for record, action in zip(entries, actions):
        symbol = record["symbol"]
        action_name = action.get("action", "hold")
        reason = action.get("reason", "")
        if action_name in CLOSE_ACTIONS:
            if AUTO_CLOSE_ENABLED:
                ok, message = submit_close(client, str(record["id"]), action_name)
                logger.info("%s: %s", symbol, message)
                if ok:
                    closes_submitted += 1
            else:
                logger.info(
                    "%s: would %s (%s) — advisory mode, AUTO_CLOSE_ENABLED=false",
                    symbol, action_name, reason,
                )
        elif action_name == "review_tested":
            logger.warning("%s: %s — human review required", symbol, reason)
        else:
            logger.info("%s: hold (%s)", symbol, reason)

    if closes_submitted:
        run_journal_sync()
    logger.info(
        "cycle done: %d open, %.2f realized, %.2f reserved, %d close(s) submitted (auto_close=%s)",
        len(entries), realized_pnl, reserved, closes_submitted, AUTO_CLOSE_ENABLED,
    )

    try:
        closes_submitted += run_equity_once(client)
    except Exception:
        logger.exception("Equity management cycle failed; continuing")
    if closes_submitted:
        run_journal_sync()

    return closes_submitted


def main() -> None:
    logger.info(
        "auto_manager starting: advisor=%s bridge=%s capital_limit=%.2f auto_close=%s poll=%ss",
        ADVISOR_URL, BRIDGE_URL, CAPITAL_LIMIT, AUTO_CLOSE_ENABLED, POLL_INTERVAL_SECONDS,
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
