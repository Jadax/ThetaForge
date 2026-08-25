"""Tests for the executor decision trail (v1.17.8).

The VM auto-executor acknowledges notifications whether or not they become
orders; the decision trail is what makes "why no trade?" answerable without
VM log access. These tests pin append/sanitize/cap behavior and the two
Advisor endpoints.
"""
import json

import pytest

from orchestrator.routes import advisor
from orchestrator import decision_log


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(decision_log, "DECISIONS_FILE", str(tmp_path / "executor_decisions.json"))
    decision_log._lock = __import__("asyncio").Lock()


@pytest.mark.asyncio
async def test_append_sanitizes_and_stamps_records():
    stored = await decision_log.append([
        {"engine": "equity", "symbol": "nvda", "action": "skipped",
         "detail": "gate=not_actionable reason=" + "x" * 2000},
        "not-a-dict",
        {"engine": "options", "symbol": "SPY", "action": "placed", "score": 81.5},
    ])

    assert stored == 2
    items = await decision_log.recent(limit=10)
    assert [item["symbol"] for item in items] == ["SPY", "nvda"]  # newest first
    assert all("received_at" in item for item in items)
    # Long free-text fields are truncated to keep records bounded.
    assert len(items[1]["detail"]) == decision_log._MAX_FIELD_LEN


@pytest.mark.asyncio
async def test_append_caps_history_at_max_decisions():
    await decision_log.append([
        {"engine": "options", "symbol": f"S{i}", "action": "skipped"}
        for i in range(decision_log.MAX_DECISIONS + 25)
    ])
    items = await decision_log.recent(limit=decision_log.MAX_DECISIONS)
    assert len(items) == decision_log.MAX_DECISIONS
    # Oldest entries were trimmed: 425 stored, last 400 kept.
    assert items[-1]["symbol"] == "S25"


@pytest.mark.asyncio
async def test_endpoints_round_trip(monkeypatch):
    batch = advisor.ExecutorDecisionBatch(decisions=[
        {"engine": "options", "symbol": "AAPL", "notification_id": "NTF-AAPL-1",
         "action": "bridge_rejected", "detail": "bridge rejected (422): no live quote"},
    ])
    response = await advisor.post_executor_decisions(batch)
    assert response["stored"] == 1

    listed = await advisor.get_executor_decisions(limit=10)
    assert listed["decisions"][0]["detail"].startswith("bridge rejected")


@pytest.mark.asyncio
async def test_endpoint_rejects_empty_batch():
    response = await advisor.post_executor_decisions(advisor.ExecutorDecisionBatch(decisions=[]))
    assert response["stored"] == 0
