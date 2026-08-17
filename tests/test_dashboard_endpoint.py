"""Tests for the v1.17 command-center aggregate: POST /api/advisor/dashboard.

The one-call endpoint combines VIX/regime, account posture, portfolio-level
delta/vega risk, watchlist rankings, and horizon picks. These tests pin its
aggregation logic and fail-closed behavior with fake provider/brain inputs.
"""
import pytest

from orchestrator.routes import advisor


class AsyncMockValue:
    """Minimal async mock that returns a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __call__(self, *args, **kwargs):
        return self._value


class FakeWatchlistStore:
    def __init__(self, symbols):
        self._symbols = symbols

    def list_symbols(self, user="default"):
        return [type("Item", (), {"symbol": symbol})() for symbol in self._symbols]


def _brain_result(symbol, score, suitability_1m=0, suitability_1w=0):
    return {
        "symbol": symbol,
        "stock_price": 100.0,
        "overall_signal": "bull" if score >= 0 else "bear",
        "overall_score": score,
        "confidence": 0.8,
        "regime": "neutral",
        "best_strategy": "bull_put",
        "best_strategy_reasoning": "reasoning",
        "all_signals": [],
        "recommendations_1m": [{
            "strategy": "bull_put", "suitability": suitability_1m,
            "typical_dte": "30-45", "typical_delta": "0.30",
        }],
        "recommendations_1w": [{
            "strategy": "bull_put", "suitability": suitability_1w,
            "typical_dte": "7-10", "typical_delta": "0.30",
        }],
    }


def _install(monkeypatch, vix="28.5", symbols=("SPY", "AAPL")):
    monkeypatch.setattr(advisor.provider, "get_vix", AsyncMockValue(vix))
    monkeypatch.setattr(advisor, "watchlist_store", FakeWatchlistStore(symbols))


@pytest.mark.asyncio
async def test_dashboard_aggregates_regime_account_and_risk(monkeypatch):
    _install(monkeypatch)

    async def fake_brain(request):
        return _brain_result(request.symbol, 60 if request.symbol == "SPY" else 30)

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)

    request = advisor.DashboardRequest(
        capital=100000, buying_power=45000,
        current_positions=[{"delta": 0.1, "vega": 0.02, "margin": 1200}],
    )
    result = await advisor.get_dashboard(request)

    assert result["vix"] == 28.5
    assert result["regime"] == "bearish"  # vix 28.5 > 22
    assert result["account"]["equity"] == 100000
    assert result["account"]["buying_power"] == 45000
    assert result["account"]["capital_deployed"] == 1200.0
    assert result["account"]["capital_deployed_pct"] == 1.2
    assert result["account"]["num_positions"] == 1
    assert result["portfolio_risk"]["net_delta"] == pytest.approx(0.1)
    assert result["portfolio_risk"]["net_vega"] == pytest.approx(0.02)
    assert result["portfolio_risk"]["within_limits"] is True


@pytest.mark.asyncio
async def test_dashboard_ranks_watchlist_and_picks_by_horizon(monkeypatch):
    _install(monkeypatch, symbols=("AAPL", "SPY"))

    async def fake_brain(request):
        # SPY has a strong 1-month fit, AAPL a weak one and no 1-week fit.
        return _brain_result(
            request.symbol,
            62 if request.symbol == "SPY" else 12,
            suitability_1m=80 if request.symbol == "SPY" else 30,
            suitability_1w=75 if request.symbol == "SPY" else 0,
        )

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)

    result = await advisor.get_dashboard(advisor.DashboardRequest())

    assert [r["symbol"] for r in result["watchlist_rankings"]] == ["SPY", "AAPL"]
    assert result["top_picks_1m"][0]["symbol"] == "SPY"
    assert result["top_picks_1w"][0]["symbol"] == "SPY"
    assert len(result["top_picks_1m"]) == 1
    assert result["top_picks_1w"] == [{"symbol": "SPY", "signal": "bull", "score": 62}]


@pytest.mark.asyncio
async def test_dashboard_defaults_universe_when_watchlist_empty(monkeypatch):
    _install(monkeypatch, symbols=())

    async def fake_brain(request):
        return _brain_result(request.symbol, 40)

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)

    result = await advisor.get_dashboard(advisor.DashboardRequest())
    # Falls back to the default universe, not an empty watchlist.
    assert len(result["watchlist_rankings"]) == 5


@pytest.mark.asyncio
async def test_dashboard_flags_risk_outside_limits(monkeypatch):
    _install(monkeypatch)

    async def fake_brain(request):
        return _brain_result(request.symbol, 40)

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)

    request = advisor.DashboardRequest(
        capital=100000,
        current_positions=[{"delta": 0.35, "vega": 0.12}],
    )
    result = await advisor.get_dashboard(request)

    assert result["portfolio_risk"]["within_limits"] is False


@pytest.mark.asyncio
async def test_dashboard_skips_failed_analyses_fail_open(monkeypatch):
    _install(monkeypatch, symbols=("SPY", "AAPL"))

    async def fake_brain(request):
        if request.symbol == "AAPL":
            raise advisor.HTTPException(status_code=422, detail="no chain")
        return _brain_result(request.symbol, 60)

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)

    result = await advisor.get_dashboard(advisor.DashboardRequest())
    assert [r["symbol"] for r in result["watchlist_rankings"]] == ["SPY"]


@pytest.mark.asyncio
async def test_dashboard_regime_tiers(monkeypatch):
    for vix, expected in (("12.5", "bullish"), ("18.0", "neutral"),
                          ("25.0", "bearish"), ("35.0", "high_vol")):
        _install(monkeypatch, vix=vix)

        async def fake_brain(request):
            return _brain_result(request.symbol, 40)

        monkeypatch.setattr(advisor, "brain_analyze", fake_brain)
        result = await advisor.get_dashboard(advisor.DashboardRequest())
        assert result["regime"] == expected, f"vix {vix} -> {expected}"


@pytest.mark.asyncio
async def test_dashboard_vix_failure_fails_open_to_neutral(monkeypatch):
    _install(monkeypatch, vix=None)

    async def fake_brain(request):
        return _brain_result(request.symbol, 40)

    monkeypatch.setattr(advisor, "brain_analyze", fake_brain)
    result = await advisor.get_dashboard(advisor.DashboardRequest())
    assert result["vix"] == 20.0
    assert result["regime"] == "neutral"
