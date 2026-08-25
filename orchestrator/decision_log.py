"""Persisted decision trail for the autonomous paper-order executor.

The executor acknowledges every notification whether or not an order was
placed, so without a durable record of *why* each signal produced no order,
a silent zero-trade stretch is undiagnosable from outside the VM. This store
keeps the last MAX_DECISIONS executor decisions (recommend rejections with
their gate/reason, bridge rejections with the bridge's own message, placed
orders) so the funnel can be inspected via

    GET /api/advisor/executor/decisions

All writes are appended by the executor through

    POST /api/advisor/executor/decisions  {"decisions": [...]}

This is observability only: nothing here gates, scores, or places orders,
and the Bridge remains the sole order path. File I/O runs in worker threads
(event-loop rule); appends are serialized by an asyncio.Lock.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DECISIONS_FILE = os.path.join(DATA_DIR, "executor_decisions.json")
MAX_DECISIONS = 400
_MAX_FIELD_LEN = 500

_lock = asyncio.Lock()


def _ensure_file() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DECISIONS_FILE):
        with open(DECISIONS_FILE, "w", encoding="utf-8") as handle:
            json.dump([], handle)


def _read_sync() -> List[Dict[str, Any]]:
    _ensure_file()
    try:
        with open(DECISIONS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_sync(items: List[Dict[str, Any]]) -> None:
    _ensure_file()
    with open(DECISIONS_FILE, "w", encoding="utf-8") as handle:
        json.dump(items, handle, separators=(",", ":"))


def _sanitize(entry: Any) -> Dict[str, Any]:
    """Coerce one executor decision into a bounded, storable record."""
    if not isinstance(entry, dict):
        return {}
    clean: Dict[str, Any] = {}
    for key, value in entry.items():
        if isinstance(value, str):
            clean[str(key)[:64]] = value[:_MAX_FIELD_LEN]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[str(key)[:64]] = value
        else:
            clean[str(key)[:64]] = str(value)[:_MAX_FIELD_LEN]
    clean["received_at"] = datetime.now(timezone.utc).isoformat()
    return clean


async def append(entries: List[Dict[str, Any]]) -> int:
    """Append sanitized executor decisions; returns the number stored."""
    records = [clean for clean in (_sanitize(e) for e in entries) if clean]
    if not records:
        return 0
    async with _lock:
        items = await asyncio.to_thread(_read_sync)
        items.extend(records)
        items = items[-MAX_DECISIONS:]
        await asyncio.to_thread(_write_sync, items)
    return len(records)


async def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """Newest-first view of the stored decisions."""
    limit = max(1, min(int(limit or 50), MAX_DECISIONS))
    items = await asyncio.to_thread(_read_sync)
    return list(reversed(items[-limit:]))
