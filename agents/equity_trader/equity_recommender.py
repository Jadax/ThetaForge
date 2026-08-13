"""
Equity Recommender — turns an EquityBrain buy read into a sized, gated trade.

Position sizing is volatility-scaled: risk a fixed fraction of the account per
trade, divided by the ATR-based stop distance (shares = floor(risk / (entry -
stop))). This equalizes dollar risk across volatile and calm names, and the
Bridge reserves exactly the ATR stop distance as the position's defined risk in
the weekly capital ledger.

Gates (all fail-closed on missing data):
  * the EquityBrain read must be an actionable "buy",
  * the ATR stop must be computable and below entry,
  * notional must stay under the per-position cap,
  * the sector correlation cap must not be exceeded once this position is added.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

# Risk per trade as a fraction of account capital (1% — the community-backed
# consensus floor from the ATR risk-management literature).
RISK_PER_TRADE_PCT = 0.01
# A single equity position may not consume more than this share of account
# capital, so the weekly budget keeps room for several names.
MAX_POSITION_NOTIONAL_PCT = 0.30
# ATR stop distance multiplier (QuantifiedStrategies / chandelier default).
ATR_STOP_MULTIPLIER = 2.0
# 2R target: reward/risk of at least 2:1 is required for a swing/trend long.
TARGET_R_MULTIPLIER = 2.0
# Concentration cap shared with the options book: no more than this many
# selected positions may share one sector bucket.
MAX_CORRELATED_EQUITY_POSITIONS = 3
# Sector buckets reused from the options recommender so both books agree on
# what "correlated" means.
from agents.trade_engine.recommender import SYMBOL_SECTOR  # noqa: E402


@dataclass
class EquityRecommendation:
    """A fully-specified long candidate the Bridge can execute as a stock buy."""
    id: str
    symbol: str
    strategy: str
    is_etf: bool
    price: float
    shares: int
    entry_limit: float
    stop_price: float
    target_price: float
    risk_per_share: float
    max_loss_total: float
    notional: float
    max_loss_pct: float
    reasoning: str
    gate: Optional[str] = None          # rejection code when not tradeable
    extra: Dict[str, Any] = field(default_factory=dict)


class EquityRecommender:
    """Pure sizing/gating for an already-buy EquityBrain read."""

    def build(
        self,
        read: Any,
        capital: float,
        current_positions: Optional[List[str]] = None,
        is_etf: Optional[bool] = None,
    ) -> EquityRecommendation:
        if isinstance(read, dict):
            read = _AsRead(read)
        symbol = str(read.symbol or "").upper()
        price = float(getattr(read, "price", 0) or 0)
        strategy = str(getattr(read, "strategy", "equity_momentum"))
        is_etf = bool(is_etf if is_etf is not None else getattr(read, "is_etf", False))
        atr_value = getattr(read, "atr_value", None)
        atr_value = float(atr_value) if atr_value else None

        base_reason = str(getattr(read, "reasoning", ""))

        if getattr(read, "signal", "no_trade") != "buy":
            return self._reject(symbol, "not_actionable",
                                base_reason or "Equity read is not a buy signal.", price)
        if not price or price <= 0:
            return self._reject(symbol, "price_unavailable", "No usable entry price.", price)
        if atr_value is None or atr_value <= 0:
            return self._reject(symbol, "atr_unavailable",
                                "No ATR available; cannot size or set a volatility stop.", price)

        risk_dollars = capital * RISK_PER_TRADE_PCT
        stop_distance = ATR_STOP_MULTIPLIER * atr_value
        stop_price = price - stop_distance
        if stop_price <= 0 or stop_distance <= 0:
            return self._reject(symbol, "stop_invalid", "ATR stop is not below entry.", price)
        target_price = price + TARGET_R_MULTIPLIER * stop_distance

        shares = int(risk_dollars // stop_distance)
        shares = max(1, shares)
        # Enforce the notional cap (a $2 stock could otherwise take the whole
        # weekly budget through the risk formula).
        max_shares_by_notional = int((capital * MAX_POSITION_NOTIONAL_PCT) // price) if price else 0
        if max_shares_by_notional < 1:
            return self._reject(symbol, "capital_too_small",
                                f"Capital ${capital:.0f} cannot fund one share at ${price:.2f}.", price)
        shares = min(shares, max_shares_by_notional)

        notional = round(price * shares, 2)
        max_loss_total = round(stop_distance * shares, 2)
        max_loss_pct = round(max_loss_total / capital * 100, 2) if capital else None

        # Sector correlation cap: adding this position must not push any sector
        # bucket past the cap given the positions already held.
        positions = [str(s).upper() for s in (current_positions or [])]
        if positions:
            counts: Dict[str, int] = {}
            for pos in positions:
                bucket = SYMBOL_SECTOR.get(pos, pos)
                counts[bucket] = counts.get(bucket, 0) + 1
            bucket = SYMBOL_SECTOR.get(symbol, symbol)
            if counts.get(bucket, 0) >= MAX_CORRELATED_EQUITY_POSITIONS:
                return self._reject(symbol, "sector_cap",
                                    f"Sector {bucket} already at the {MAX_CORRELATED_EQUITY_POSITIONS}-position cap.",
                                    price)

        return EquityRecommendation(
            id=str(uuid4()),
            symbol=symbol,
            strategy=strategy,
            is_etf=is_etf,
            price=round(price, 2),
            shares=shares,
            entry_limit=round(price, 2),
            stop_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            risk_per_share=round(stop_distance, 2),
            max_loss_total=max_loss_total,
            notional=notional,
            max_loss_pct=max_loss_pct,
            reasoning=(
                f"{base_reason} Position sized for 1% account risk "
                f"({risk_dollars:.2f}) at a {ATR_STOP_MULTIPLIER:.0f}x ATR stop "
                f"({stop_price:.2f}), 2R target {target_price:.2f}."
            ),
        )

    def _reject(self, symbol: str, gate: str, reason: str, price: float) -> EquityRecommendation:
        return EquityRecommendation(
            id=str(uuid4()), symbol=symbol, strategy="equity_momentum", is_etf=False,
            price=round(price, 2) if price else 0.0, shares=0,
            entry_limit=0.0, stop_price=0.0, target_price=0.0,
            risk_per_share=0.0, max_loss_total=0.0, notional=0.0, max_loss_pct=0.0,
            reasoning=reason, gate=gate,
        )


class _AsRead:
    """Thin dict->attribute adapter so the recommender accepts scan payloads."""

    def __init__(self, data: Dict[str, Any]):
        self.__dict__.update(data)
