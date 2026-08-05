"""
Alert System for ThetaForge.
Monitors price, IV, signal changes, and portfolio thresholds.
Generates actionable alerts when conditions are met.

Alert types:
1. Price alerts (support/resistance breaks, % moves)
2. IV alerts (IV rank crosses, term structure changes)
3. Signal alerts (Brain signal flips, strategy changes)
4. Risk alerts (drawdown, position limits, Greeks breaches)
5. Earnings alerts (approaching earnings dates)
"""
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
ALERT_HISTORY_FILE = os.path.join(DATA_DIR, "alert_history.json")


class AlertType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PRICE_MOVE_PCT = "price_move_pct"
    IV_RANK_ABOVE = "iv_rank_above"
    IV_RANK_BELOW = "iv_rank_below"
    IV_ABOVE = "iv_above"
    IV_BELOW = "iv_below"
    SIGNAL_FLIP = "signal_flip"
    STRATEGY_CHANGE = "strategy_change"
    DRAWDOWN = "drawdown"
    GREEKS_BREACH = "greeks_breach"
    EARNINGS_WARNING = "earnings_warning"
    VIX_BELOW = "vix_below"
    VIX_ABOVE = "vix_above"


class AlertPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    """A user-defined alert rule."""
    rule_id: str
    symbol: str
    alert_type: str
    threshold: float
    priority: str = "medium"
    message: str = ""
    triggered: bool = False
    created_at: str = ""
    triggered_at: Optional[str] = None
    one_time: bool = True  # Trigger once then disable

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class AlertEvent:
    """A triggered alert event."""
    rule_id: str
    symbol: str
    alert_type: str
    priority: str
    message: str
    current_value: float
    threshold: float
    timestamp: str = ""
    acknowledged: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class AlertEngine:
    """
    Monitors conditions and fires alerts.
    
    Usage:
        engine = AlertEngine()
        engine.add_rule("AAPL", AlertType.PRICE_ABOVE, 200.0, "AAPL broke $200")
        events = engine.check({"AAPL": {"price": 205, "iv_rank": 40}})
    """

    def __init__(self):
        self._ensure_files()

    def _ensure_files(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        for f in [ALERTS_FILE, ALERT_HISTORY_FILE]:
            if not os.path.exists(f):
                with open(f, "w") as fh:
                    json.dump([], fh)

    def _read_rules(self) -> List[Dict]:
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)

    def _write_rules(self, data: List[Dict]):
        with open(ALERTS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _read_history(self) -> List[Dict]:
        with open(ALERT_HISTORY_FILE, "r") as f:
            return json.load(f)

    def _write_history(self, data: List[Dict]):
        with open(ALERT_HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def add_rule(
        self,
        symbol: str,
        alert_type: AlertType,
        threshold: float,
        message: str = "",
        priority: AlertPriority = AlertPriority.MEDIUM,
        one_time: bool = True,
    ) -> AlertRule:
        """Add a new alert rule."""
        rules = self._read_rules()
        rule = AlertRule(
            rule_id=f"{symbol}_{alert_type.value}_{int(datetime.now(timezone.utc).timestamp())}",
            symbol=symbol.upper(),
            alert_type=alert_type.value,
            threshold=threshold,
            priority=priority.value,
            message=message or f"{symbol} {alert_type.value} {threshold}",
            one_time=one_time,
        )
        rules.append(asdict(rule))
        self._write_rules(rules)
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        rules = self._read_rules()
        before = len(rules)
        rules = [r for r in rules if r["rule_id"] != rule_id]
        self._write_rules(rules)
        return len(rules) < before

    def list_rules(self, symbol: str = None) -> List[Dict]:
        rules = self._read_rules()
        if symbol:
            rules = [r for r in rules if r["symbol"] == symbol.upper()]
        return rules

    def check(self, market_data: Dict[str, Dict]) -> List[Dict]:
        """
        Check all rules against current market data.
        
        market_data: {"AAPL": {"price": 195, "iv": 0.25, "iv_rank": 40, "prev_price": 190}, ...}
        Returns: list of triggered alert events
        """
        rules = self._read_rules()
        events = []

        for rule in rules:
            if rule.get("triggered") and rule.get("one_time"):
                continue

            symbol = rule["symbol"]
            data = market_data.get(symbol, {})
            if not data:
                continue

            triggered = False
            current_value = 0

            alert_type = rule["alert_type"]
            threshold = rule["threshold"]

            if alert_type == AlertType.PRICE_ABOVE.value:
                current_value = data.get("price", 0)
                triggered = current_value > threshold

            elif alert_type == AlertType.PRICE_BELOW.value:
                current_value = data.get("price", 0)
                triggered = current_value < threshold

            elif alert_type == AlertType.PRICE_MOVE_PCT.value:
                price = data.get("price", 0)
                prev_price = data.get("prev_price", price)
                if prev_price > 0:
                    current_value = abs((price - prev_price) / prev_price * 100)
                    triggered = current_value > threshold

            elif alert_type == AlertType.IV_RANK_ABOVE.value:
                current_value = data.get("iv_rank", 50)
                triggered = current_value > threshold

            elif alert_type == AlertType.IV_RANK_BELOW.value:
                current_value = data.get("iv_rank", 50)
                triggered = current_value < threshold

            elif alert_type == AlertType.IV_ABOVE.value:
                current_value = data.get("iv", 0.20)
                triggered = current_value > threshold

            elif alert_type == AlertType.IV_BELOW.value:
                current_value = data.get("iv", 0.20)
                triggered = current_value < threshold

            elif alert_type == AlertType.VIX_ABOVE.value:
                current_value = data.get("vix", 20)
                triggered = current_value > threshold

            elif alert_type == AlertType.VIX_BELOW.value:
                current_value = data.get("vix", 20)
                triggered = current_value < threshold

            elif alert_type == AlertType.SIGNAL_FLIP.value:
                current_signal = data.get("current_signal", "neutral")
                prev_signal = data.get("prev_signal", "neutral")
                current_value = 1 if current_signal != prev_signal else 0
                triggered = current_value == 1

            elif alert_type == AlertType.DRAWDOWN.value:
                current_value = data.get("drawdown_pct", 0)
                triggered = current_value > threshold

            elif alert_type == AlertType.GREEKS_BREACH.value:
                current_value = abs(data.get("net_delta", 0))
                triggered = current_value > threshold

            elif alert_type == AlertType.EARNINGS_WARNING.value:
                days = data.get("days_to_earnings", 999)
                current_value = days
                triggered = 0 < days <= threshold

            if triggered:
                event = AlertEvent(
                    rule_id=rule["rule_id"],
                    symbol=symbol,
                    alert_type=alert_type,
                    priority=rule["priority"],
                    message=rule["message"],
                    current_value=round(current_value, 4),
                    threshold=threshold,
                )
                events.append(asdict(event))

                # Update rule
                rule["triggered"] = True
                rule["triggered_at"] = datetime.now(timezone.utc).isoformat()

        if events:
            self._write_rules(rules)

            # Save to history
            history = self._read_history()
            history.extend(events)
            if len(history) > 2000:
                history = history[-2000:]
            self._write_history(history)

        return events

    def get_history(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """Get recent alert history."""
        history = self._read_history()
        if symbol:
            history = [h for h in history if h["symbol"] == symbol.upper()]
        return history[-limit:]

