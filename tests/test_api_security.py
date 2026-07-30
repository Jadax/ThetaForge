"""Access-control tests for the hosted Advisor API and the local Bridge."""
import asyncio

import pytest
from fastapi import HTTPException

import bridge.main as bridge_main
from orchestrator.security import RateLimiter, require_advisor_token


TOKEN = "test-advisor-token-abcdef123456"


def _call(coroutine):
    return asyncio.run(coroutine)


# ── Advisor token ────────────────────────────────────────────────────────


def test_advisor_rejects_request_when_token_is_not_configured(monkeypatch):
    """An unset token must fail closed rather than leaving the API public."""
    monkeypatch.delenv("ADVISOR_API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as error:
        _call(require_advisor_token(TOKEN))
    assert error.value.status_code == 503


def test_advisor_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("ADVISOR_API_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as error:
        _call(require_advisor_token(None))
    assert error.value.status_code == 401


def test_advisor_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("ADVISOR_API_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as error:
        _call(require_advisor_token("not-the-token"))
    assert error.value.status_code == 401


def test_advisor_accepts_matching_token(monkeypatch):
    monkeypatch.setenv("ADVISOR_API_TOKEN", TOKEN)
    assert _call(require_advisor_token(TOKEN)) is None


# ── Rate limiting ────────────────────────────────────────────────────────


def test_rate_limiter_blocks_beyond_the_window_allowance():
    limiter = RateLimiter(max_calls=2, window_seconds=300, name="scan")

    async def exercise():
        await limiter()
        await limiter()
        with pytest.raises(HTTPException) as error:
            await limiter()
        return error.value

    blocked = _call(exercise())
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_rate_limiter_allows_calls_once_the_window_has_passed():
    limiter = RateLimiter(max_calls=1, window_seconds=0.05, name="scan")

    async def exercise():
        await limiter()
        await asyncio.sleep(0.1)
        await limiter()

    _call(exercise())


# ── Bridge token ─────────────────────────────────────────────────────────


def test_bridge_rejects_request_when_token_is_not_configured(monkeypatch):
    monkeypatch.setattr(bridge_main, "ACCESS_TOKEN", "")
    with pytest.raises(HTTPException) as error:
        _call(bridge_main.require_access_token("anything"))
    assert error.value.status_code == 503


def test_bridge_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(bridge_main, "ACCESS_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as error:
        _call(bridge_main.require_access_token("not-the-token"))
    assert error.value.status_code == 401


def test_bridge_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(bridge_main, "ACCESS_TOKEN", TOKEN)
    assert _call(bridge_main.require_access_token(TOKEN)) is None


# ── Removed unguarded order path ─────────────────────────────────────────


def test_bridge_exposes_no_order_path_that_skips_the_risk_controls():
    """Only /orders/submit-combo may place an order.

    The removed /orders/stage and /orders/{id}/submit pair accepted any
    action/right combination — including a naked short call — without live
    quotes, defined-risk proof, or capital-limit enforcement, and never wrote
    to the ledger that backs weekly capital reservation.
    """
    order_paths = {
        route.path
        for route in bridge_main.app.routes
        if getattr(route, "methods", None) and "POST" in route.methods
    }
    assert "/orders/submit-combo" in order_paths
    assert "/orders/stage" not in order_paths
    assert not any(path.endswith("/submit") for path in order_paths)
