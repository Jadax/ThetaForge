"""Free-data flow & volatility metrics borrowed from competitor toolkits.

These are self-contained, honest derivations from data the project already
holds (chain Greeks/IV, open interest, volume, IV history) — no paid feed, and
every function degrades to None when its inputs are missing rather than
fabricating a value.

All importers in the repo can use these; they never add a second scoring path.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def relative_volatility_band(iv: Optional[float], hv: Optional[float]) -> Optional[Dict]:
    """Relative volatility (IV / HV ratio) as a first-class premium-selling gate.

    ORATS / Option Samurai / Barchart all surface this "rich or cheap vol" stat;
    RV is the cleaner relative-vol signal than raw IV Rank. Returns a band plus
    a human label. Missing IV or HV -> None (fail-closed).
    """
    if iv is None or hv is None or hv <= 0:
        return None
    ratio = iv / hv
    if ratio < 0.8:
        band = "very_cheap"
    elif ratio < 0.95:
        band = "cheap"
    elif ratio <= 1.05:
        band = "fair"
    elif ratio <= 1.3:
        band = "rich"
    else:
        band = "very_rich"
    return {
        "iv_hv_ratio": round(ratio, 3),
        "band": band,
        "label": band.replace("_", " "),
        "premium_edge": band in ("rich", "very_rich"),
    }


def unusual_volume(volume: Optional[float], baseline_volume: Optional[float]) -> Optional[Dict]:
    """Flag strikes whose volume is a large multiple of a normal baseline.

    The uncommon-activity play: a strike trading at many multiples of its own
    baseline is where money is concentrating. Returns the multiple and a tier;
    missing inputs -> None.
    """
    if volume is None or baseline_volume is None or baseline_volume <= 0:
        return None
    multiple = volume / baseline_volume
    if multiple >= 10:
        tier = "extreme"
    elif multiple >= 5:
        tier = "high"
    elif multiple >= 3:
        tier = "elevated"
    elif multiple >= 1.5:
        tier = "notable"
    else:
        tier = "normal"
    return {"multiple": round(multiple, 2), "tier": tier}


def oi_divergence(volume: Optional[float], open_interest: Optional[float]) -> Optional[Dict]:
    """High volume without a matching rise in OI suggests opening vs closing.

    Big volume on a strike with little open-interest change is the classic
    opening-transaction (new positioning) tell; when OI swells with volume it
    is opening, when OI is flat while volume is huge it can reflect closing.
    Returns a conservative flag; missing inputs -> None.
    """
    if volume is None or open_interest is None or volume <= 0:
        return None
    if open_interest <= 0:
        return {"hint": "opening", "ratio": None,
                "reason": "fresh strikes with volume but no existing open interest"}
    ratio = volume / open_interest
    if ratio > 2.0:
        return {"hint": "likely_opening", "ratio": round(ratio, 2),
                "reason": "volume far exceeds open interest, suggesting new positions"}
    if ratio > 0.8:
        return {"hint": "neutral", "ratio": round(ratio, 2),
                "reason": "volume broadly in line with open interest"}
    return {"hint": "likely_closing", "ratio": round(ratio, 2),
            "reason": "open interest exceeds volume, suggesting existing-position trade"}


def oi_center_of_mass(chain: List[Dict]) -> Optional[Dict]:
    """Open-interest weighted center of mass / pin price across a chain.

    OptionStrat and Market Chameleon surface the "where is the market's largest
    position" stat. We sum OI by strike (both sides) and report the weighted
    mean and the peak-OI strike per expiry when available.
    """
    total_oi = 0.0
    weighted = 0.0
    oi_by_strike: Dict[float, float] = {}
    for row in chain:
        strike = row.get("strike")
        oi = row.get("open_interest") or row.get("oi") or 0
        if strike is None or oi is None:
            continue
        try:
            strike = float(strike)
            oi = float(oi)
        except (TypeError, ValueError):
            continue
        total_oi += oi
        weighted += strike * oi
        oi_by_strike[strike] = oi_by_strike.get(strike, 0.0) + oi
    if total_oi <= 0 or not oi_by_strike:
        return None
    center = weighted / total_oi
    peak = max(oi_by_strike, key=oi_by_strike.get)
    return {
        "center_of_mass": round(center, 2),
        "peak_oi_strike": peak,
        "total_oi": round(total_oi),
        "concentration": round(oi_by_strike[peak] / total_oi, 3),
    }


def iv_mover(current: Optional[float], prior: Optional[float]) -> Optional[Dict]:
    """Classify IV direction/fast-mover from two consecutive snapshots.

    Barchart's rising/falling-vol screens — a name whose IV is spiking quickly
    is where a vol regime may be shifting. Missing data -> None.
    """
    if current is None or prior is None or prior <= 0:
        return None
    change_pct = (current - prior) / prior * 100
    if current > prior and change_pct >= 15:
        trend, label = "rising_fast", "rapidly rising"
    elif current > prior:
        trend, label = "rising", "rising"
    elif current < prior and change_pct <= -15:
        trend, label = "falling_fast", "rapidly falling"
    elif current < prior:
        trend, label = "falling", "falling"
    else:
        trend, label = "flat", "flat"
    return {
        "trend": trend,
        "label": label,
        "change_pct": round(change_pct, 1),
        "current": round(float(current), 4),
    }
