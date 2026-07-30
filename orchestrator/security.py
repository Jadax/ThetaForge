"""Access control for the hosted Advisor API.

The Advisor runs on a public URL and holds a single, shared set of JSON-backed
state files: one watchlist, one alert-rule set, one notification queue. CORS
constrains browsers only, so without a token any client can trigger a full
universe scan or mutate the state that drives the dashboard. Every
``/api/advisor`` route therefore requires a shared secret, and the expensive
scan endpoints are additionally rate limited.
"""
import hmac
import os
import time
from collections import deque
from typing import Deque

from fastapi import Header, HTTPException

ADVISOR_TOKEN_HEADER = "X-ThetaForge-Advisor-Token"


def _configured_token() -> str:
    return os.getenv("ADVISOR_API_TOKEN", "").strip()


async def require_advisor_token(
    x_thetaforge_advisor_token: str | None = Header(default=None),
) -> None:
    """Authenticate an Advisor request against ``ADVISOR_API_TOKEN``.

    This deliberately fails closed. An unset token previously left every
    endpoint open to the public internet, so a missing configuration is
    reported as a service error rather than silently degrading to no auth.
    """
    expected = _configured_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "ADVISOR_API_TOKEN is not configured on the Advisor. Set it in the "
                "deployment environment, then supply the same value from the dashboard."
            ),
        )
    supplied = x_thetaforge_advisor_token or ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or missing {ADVISOR_TOKEN_HEADER}",
        )


class RateLimiter:
    """Fixed-window limiter for endpoints that fan out to public data sources.

    A single scan issues hundreds of outbound yfinance requests, so an
    unthrottled endpoint is both a denial-of-service surface and a fast route
    to being blocked by the upstream free data providers.
    """

    def __init__(self, max_calls: int, window_seconds: float, name: str):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.name = name
        self._calls: Deque[float] = deque()

    async def __call__(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > self.window_seconds:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            retry_after = max(1, int(self.window_seconds - (now - self._calls[0])))
            raise HTTPException(
                status_code=429,
                detail=f"Too many {self.name} requests. Retry in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        self._calls.append(now)


# A full scan takes minutes and saturates the free data sources; the background
# scanner already refreshes results every five minutes without being asked.
scan_rate_limit = RateLimiter(max_calls=6, window_seconds=300, name="market scan")
# Per-symbol analysis is cheaper but still fans out to several network calls.
analysis_rate_limit = RateLimiter(max_calls=60, window_seconds=60, name="analysis")
