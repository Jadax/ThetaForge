import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent / "agents" / "volatility" / "flow_metrics.py"


def _load():
    spec = importlib.util.spec_from_file_location("flow_metrics", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


flow_metrics = _load()


def test_relative_volatility_bands():
    assert flow_metrics.relative_volatility_band(0.30, 0.20)["band"] == "very_rich"
    assert flow_metrics.relative_volatility_band(0.25, 0.20)["band"] == "rich"
    assert flow_metrics.relative_volatility_band(0.30, 0.10)["band"] == "very_rich"
    assert flow_metrics.relative_volatility_band(0.18, 0.20)["band"] == "cheap"
    assert flow_metrics.relative_volatility_band(0.20, 0.20)["band"] == "fair"
    assert flow_metrics.relative_volatility_band(0.25, 0.20)["premium_edge"] is True
    assert flow_metrics.relative_volatility_band(None, 0.20) is None
    assert flow_metrics.relative_volatility_band(0.20, 0) is None


def test_unusual_volume_tiers():
    assert flow_metrics.unusual_volume(500, 20)["tier"] == "extreme"
    assert flow_metrics.unusual_volume(160, 30)["tier"] == "high"
    assert flow_metrics.unusual_volume(120, 30)["tier"] == "elevated"
    assert flow_metrics.unusual_volume(20, 30)["tier"] == "normal"
    assert flow_metrics.unusual_volume(None, 30) is None


def test_oi_divergence():
    assert flow_metrics.oi_divergence(1000, 10)["hint"] == "likely_opening"
    assert flow_metrics.oi_divergence(100, 50)["hint"] == "neutral"
    assert flow_metrics.oi_divergence(10, 100)["hint"] == "likely_closing"
    assert flow_metrics.oi_divergence(0, 10) is None


def test_oi_center_of_mass():
    chain = [
        {"strike": 100, "open_interest": 10},
        {"strike": 110, "open_interest": 30},
        {"strike": 120, "open_interest": 10},
    ]
    result = flow_metrics.oi_center_of_mass(chain)
    assert result["center_of_mass"] == 110.0
    assert result["peak_oi_strike"] == 110
    assert result["total_oi"] == 50
    assert flow_metrics.oi_center_of_mass([]) is None
    assert flow_metrics.oi_center_of_mass([{"strike": 100}]) is None


def test_iv_mover():
    assert flow_metrics.iv_mover(0.30, 0.20)["trend"] == "rising_fast"
    assert flow_metrics.iv_mover(0.22, 0.20)["trend"] == "rising"
    assert flow_metrics.iv_mover(0.10, 0.20)["trend"] == "falling_fast"
    assert flow_metrics.iv_mover(0.18, 0.20)["trend"] == "falling"
    assert flow_metrics.iv_mover(None, 0.20) is None
