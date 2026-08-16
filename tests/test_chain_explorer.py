"""Tests for the v1.15 chain explorer: desk-style chain table + summary.
"""
import pandas as pd
import pytest

from agents.trade_engine.chain_explorer import build_chain_explorer


def _chain():
    """Two expiries (dte 12 and 33), strikes 90-110, calls+puts with greeks."""
    chain = []
    for expiry, dte in (("2026-08-28", 12), ("2026-09-18", 33)):
        for strike in range(90, 111, 5):
            for opt_type in ("CALL", "PUT"):
                chain.append({
                    "strike": strike, "expiry": expiry, "dte": dte,
                    "option_type": opt_type, "bid": 1.20, "ask": 1.40,
                    "last": 1.30, "volume": 150, "open_interest": 2000,
                    "implied_volatility": 0.30, "delta": 0.4 if opt_type == "CALL" else -0.4,
                    "gamma": 0.02, "theta": -0.05, "vega": 0.09,
                })
    return chain


def test_chain_explorer_picks_expiry_nearest_target_dte():
    result = build_chain_explorer(_chain(), 100, target_dte=30)
    assert result["expiry"] == "2026-09-18"  # dte 33, nearest to 30
    assert result["dte"] == 33
    assert len(result["table"]) == 5


def test_chain_explorer_explicit_expiry():
    result = build_chain_explorer(_chain(), 100, expiry="2026-08-28")
    assert result["expiry"] == "2026-08-28"
    assert result["dte"] == 12


def test_chain_explorer_table_sides_and_ratio():
    result = build_chain_explorer(_chain(), 100)
    row = next(r for r in result["table"] if r["strike"] == 100)
    assert row["call"]["mid"] == 1.3
    assert row["put"]["mid"] == 1.3
    assert row["call"]["iv"] == 0.3
    assert row["call"]["delta"] == 0.4 and row["put"]["delta"] == -0.4
    assert row["put_call_oi_ratio"] == 1.0


def test_chain_explorer_summary_readings():
    result = build_chain_explorer(_chain(), 100)
    summary = result["summary"]
    assert summary["atm_iv"] == pytest.approx(0.3, abs=0.0001)
    assert summary["atm_straddle_mid"] == pytest.approx(2.6, abs=0.01)
    assert summary["expected_move_pct"] > 0
    assert summary["max_pain_strike"] == 100
    assert summary["put_call_oi_ratio"] == pytest.approx(1.0, abs=0.001)
    assert summary["put_call_volume_ratio"] == pytest.approx(1.0, abs=0.001)
    # IV skew needs a proper delta/IV surface; a flat synthetic chain either
    # yields a skew read or fails open (key absent) -- never an error payload.
    assert "error" not in result


def test_chain_explorer_expiries_list():
    result = build_chain_explorer(_chain(), 100)
    expiries = {e["expiry"] for e in result["expiries"]}
    assert expiries == {"2026-08-28", "2026-09-18"}


def test_chain_explorer_fails_closed():
    assert "error" in build_chain_explorer([], 100)
    assert "error" in build_chain_explorer(_chain(), 0)
    assert "error" in build_chain_explorer(_chain(), 100, expiry="2099-01-01")


# ── Endpoint ───────────────────────────────────────────────────────────────

from orchestrator.routes import advisor


@pytest.fixture
def chain_history():
    closes = [100 * (1 + 0.005 * index) for index in range(40)]
    return pd.DataFrame({"Close": closes})


@pytest.mark.asyncio
async def test_chain_endpoint_returns_table_with_enrichment(monkeypatch, chain_history, tmp_path):
    import agents.volatility.iv_history as iv_history_module

    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMockValue(100.0))
    monkeypatch.setattr(advisor.provider, "get_option_chain", AsyncMockValue(_chain()))
    monkeypatch.setattr(advisor.provider, "get_historical_prices", AsyncMockValue(chain_history))
    monkeypatch.setattr(iv_history_module, "DEFAULT_PATH", str(tmp_path / "iv_history.json"))
    monkeypatch.setattr(iv_history_module.IVHistoryStore, "iv_rank", lambda self, symbol, current_iv=None: 45.0)
    monkeypatch.setattr(iv_history_module.IVHistoryStore, "iv_percentile", lambda self, symbol, current_iv=None: 60.0)

    request = advisor.ChainRequest(symbol="TEST")
    result = await advisor.chain_explorer(request)

    assert result["underlying"] == 100.0
    assert len(result["table"]) == 5
    assert result["summary"]["iv_rank"] == 45.0
    assert result["summary"]["iv_percentile"] == 60.0
    # history has real variance -> NVRP should be present
    assert "nvrp" in result["summary"]
    assert "hv_20" in result["summary"]


@pytest.mark.asyncio
async def test_chain_endpoint_fails_closed_on_missing_chain(monkeypatch):
    monkeypatch.setattr(advisor.provider, "get_stock_price", AsyncMockValue(100.0))
    monkeypatch.setattr(advisor.provider, "get_option_chain", AsyncMockValue([]))

    request = advisor.ChainRequest(symbol="TEST")
    with pytest.raises(advisor.HTTPException) as error:
        await advisor.chain_explorer(request)
    assert error.value.status_code == 422


class AsyncMockValue:
    """Minimal async mock that returns a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __call__(self, *args, **kwargs):
        return self._value
