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
import uuid
import math
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
ALERT_HISTORY_FILE = os.path.join(DATA_DIR, "alert_history.json")
WEBHOOK_CONFIG_FILE = os.path.join(DATA_DIR, "alert_webhook.json")


def _read_webhook_config() -> Optional[Dict[str, Any]]:
    """Webhook delivery target, or None when unconfigured/disabled."""
    try:
        if not os.path.exists(WEBHOOK_CONFIG_FILE):
            return None
        with open(WEBHOOK_CONFIG_FILE, "r") as fh:
            config = json.load(fh)
        url = str(config.get("url") or "").strip()
        if not url or not config.get("enabled", True):
            return None
        return {"url": url}
    except Exception:
        return None


def _notify_webhook(events: List[Dict]) -> None:
    """Fire-and-forget delivery of triggered alerts to the configured webhook.

    Discord/Slack-compatible JSON POST on a daemon thread, so a slow or down
    webhook never blocks the scan or the API. Fail-closed: any delivery error
    is swallowed — an alert failing to reach a webhook must not break the
    pipeline that produced it.
    """
    config = _read_webhook_config()
    if not config or not events:
        return

    payload = {
        "source": "thetaforge",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }

    def _deliver():
        try:
            import httpx
            httpx.post(
                config["url"], json=payload, timeout=10,
                headers={"Content-Type": "application/json"},
            )
        except Exception:
            pass

    threading.Thread(target=_deliver, daemon=True).start()


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
    # Scan-native threshold alerts (fed straight from the scanner's
    # per-symbol result rows, so alerts fire on the same numbers the
    # dashboard shows -- no duplicated data pulls).
    SCORE_ABOVE = "score_above"
    SCORE_BELOW = "score_below"
    IV_PERCENTILE_ABOVE = "iv_percentile_above"
    IV_PERCENTILE_BELOW = "iv_percentile_below"
    GEX_REGIME = "gex_regime"
    PCR_ABOVE = "pcr_above"
    PCR_BELOW = "pcr_below"
    THEORETICAL_EDGE_ABOVE = "theoretical_edge_above"


class AlertPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Curated, ready-to-instantiate alert templates. The dashboard surfaces
# these so the user can create a rule with one click instead of remembering
# type names and sensible thresholds.
ALERT_GALLERY: List[Dict[str, Any]] = [
    {"template_id": "score_above", "name": "Brain score crosses above", "alert_type": AlertType.SCORE_ABOVE.value, "default_threshold": 70.0, "priority": "high",
     "description": "Fires when a symbol's composite scan score rises above the threshold."},
    {"template_id": "score_below", "name": "Brain score falls below", "alert_type": AlertType.SCORE_BELOW.value, "default_threshold": 50.0, "priority": "medium",
     "description": "Fires when a symbol's composite scan score drops below the threshold."},
    {"template_id": "iv_rank_above", "name": "IV rank crosses above", "alert_type": AlertType.IV_RANK_ABOVE.value, "default_threshold": 70.0, "priority": "high",
     "description": "Fires when the symbol's IV rank (vs its own history) exceeds the threshold — premium selling context."},
    {"template_id": "iv_rank_below", "name": "IV rank falls below", "alert_type": AlertType.IV_RANK_BELOW.value, "default_threshold": 30.0, "priority": "medium",
     "description": "Fires when IV rank drops below the threshold — cheap premium."},
    {"template_id": "iv_percentile_above", "name": "IV percentile above", "alert_type": AlertType.IV_PERCENTILE_ABOVE.value, "default_threshold": 80.0, "priority": "medium",
     "description": "Fires when ATM IV sits above its historical percentile."},
    {"template_id": "iv_percentile_below", "name": "IV percentile below", "alert_type": AlertType.IV_PERCENTILE_BELOW.value, "default_threshold": 20.0, "priority": "low",
     "description": "Fires when ATM IV sits below its historical percentile."},
    {"template_id": "price_above", "name": "Price crosses above", "alert_type": AlertType.PRICE_ABOVE.value, "default_threshold": 0.0, "priority": "medium",
     "description": "Fires when the current price exceeds the threshold."},
    {"template_id": "price_below", "name": "Price falls below", "alert_type": AlertType.PRICE_BELOW.value, "default_threshold": 0.0, "priority": "medium",
     "description": "Fires when the current price drops below the threshold."},
    {"template_id": "vix_above", "name": "VIX crosses above", "alert_type": AlertType.VIX_ABOVE.value, "default_threshold": 25.0, "priority": "high",
     "description": "Fires when the VIX exceeds the threshold (risk-off regime context)."},
    {"template_id": "vix_below", "name": "VIX falls below", "alert_type": AlertType.VIX_BELOW.value, "default_threshold": 14.0, "priority": "low",
     "description": "Fires when the VIX drops below the threshold."},
    {"template_id": "pcr_above", "name": "Put/call ratio above", "alert_type": AlertType.PCR_ABOVE.value, "default_threshold": 1.5, "priority": "medium",
     "description": "Fires when the symbol's put/call ratio exceeds the threshold (put-heavy)."},
    {"template_id": "pcr_below", "name": "Put/call ratio below", "alert_type": AlertType.PCR_BELOW.value, "default_threshold": 0.7, "priority": "medium",
     "description": "Fires when the symbol's put/call ratio drops below the threshold (call-heavy)."},
    {"template_id": "gex_regime", "name": "GEX regime flips", "alert_type": AlertType.GEX_REGIME.value, "default_threshold": 0.0, "priority": "high",
     "description": "Fires when the GEX regime equals the configured value — set the threshold to a regime string like 'wall_below'."},
    {"template_id": "theoretical_edge_above", "name": "Theoretical edge above", "alert_type": AlertType.THEORETICAL_EDGE_ABOVE.value, "default_threshold": 1.0, "priority": "medium",
     "description": "Fires when the symbol's theoretical edge (own BS value vs CBOE mid, %) exceeds the threshold."},
    {"template_id": "earnings_warning", "name": "Earnings in N days", "alert_type": AlertType.EARNINGS_WARNING.value, "default_threshold": 5.0, "priority": "high",
     "description": "Fires when earnings are within the threshold number of trading days."},
    {"template_id": "drawdown", "name": "Portfolio drawdown exceeds", "alert_type": AlertType.DRAWDOWN.value, "default_threshold": 10.0, "priority": "critical",
     "description": "Fires when realized drawdown (%) exceeds the threshold."},
]


def rule_from_template(template_id: str, symbol: str, threshold: Any = None) -> Dict[str, Any]:
    """Instantiate a rule spec from the gallery.

    Returns the alert_type and the threshold to pass to AlertEngine.add_rule.
    Missing/unknown templates fail closed (raise) rather than minting a rule
    with wrong thresholds.
    """
    template = next((t for t in ALERT_GALLERY if t["template_id"] == template_id), None)
    if template is None:
        raise ValueError(f"unknown alert template: {template_id}")
    spec = dict(template)
    spec["symbol"] = symbol.upper()
    if threshold is not None:
        spec["threshold"] = threshold
    else:
        spec["threshold"] = template["default_threshold"]
    return spec


@dataclass
class AlertRule:
    """A user-defined alert rule."""
    rule_id: str
    symbol: str
    alert_type: str
    threshold: Any
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
    threshold: Any
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
                with open(f, "w", encoding="utf-8") as fh:
                    json.dump([], fh)

    def _read_rules(self) -> List[Dict]:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_rules(self, data: List[Dict]):
        self._atomic_write(ALERTS_FILE, data)

    def _read_history(self) -> List[Dict]:
        with open(ALERT_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_history(self, data: List[Dict]):
        self._atomic_write(ALERT_HISTORY_FILE, data)

    @staticmethod
    def _atomic_write(path: str, data: Any) -> None:
        """Replace JSON state atomically so concurrent requests cannot corrupt it."""
        tmp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

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
            rule_id=f"{symbol.upper()}_{alert_type.value}_{uuid.uuid4().hex[:12]}",
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

    def _evaluate_rule(self, rule: Dict, data: Dict) -> tuple:
        """(triggered, current_value) for one rule against one symbol's data.

        Missing reads fail closed: no data field means no trigger, never a
        placeholder value that fabricates an alert.
        """
        alert_type = rule["alert_type"]
        threshold = rule["threshold"]

        def number(key: str) -> Optional[float]:
            value = data.get(key)
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None

        if alert_type == AlertType.PRICE_ABOVE.value:
            value = number("price")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.PRICE_BELOW.value:
            value = number("price")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.PRICE_MOVE_PCT.value:
            price = number("price")
            prev_price = number("prev_price")
            if price is not None and prev_price is not None and prev_price > 0:
                move = abs((price - prev_price) / prev_price * 100)
                return move > threshold, move
            return False, 0
        if alert_type == AlertType.IV_RANK_ABOVE.value:
            value = number("iv_rank")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.IV_RANK_BELOW.value:
            value = number("iv_rank")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.IV_PERCENTILE_ABOVE.value:
            value = number("iv_percentile")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.IV_PERCENTILE_BELOW.value:
            value = number("iv_percentile")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.IV_ABOVE.value:
            value = number("iv")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.IV_BELOW.value:
            value = number("iv")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.VIX_ABOVE.value:
            value = number("vix")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.VIX_BELOW.value:
            value = number("vix")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.SIGNAL_FLIP.value:
            current = data.get("current_signal")
            previous = data.get("prev_signal")
            flip = current is not None and previous is not None and current != previous
            return bool(flip), 1 if flip else 0
        if alert_type == AlertType.DRAWDOWN.value:
            value = number("drawdown_pct")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.GREEKS_BREACH.value:
            raw = number("net_delta")
            value = abs(raw) if raw is not None else 0
            return raw is not None and value > threshold, value
        if alert_type == AlertType.EARNINGS_WARNING.value:
            days = number("days_to_earnings")
            return days is not None and 0 < days <= threshold, days or 0
        if alert_type == AlertType.SCORE_ABOVE.value:
            value = number("score")
            return (value is not None and value > threshold), value or 0
        if alert_type == AlertType.SCORE_BELOW.value:
            value = number("score")
            return (value is not None and value < threshold), value or 0
        if alert_type == AlertType.GEX_REGIME.value:
            matched = str(data.get("gex_regime") or "") == str(threshold)
            return matched, 1 if matched else 0
        if alert_type == AlertType.PCR_ABOVE.value:
            pcr = number("pcr")
            if pcr is None:
                return False, 0
            return pcr > threshold, pcr
        if alert_type == AlertType.PCR_BELOW.value:
            pcr = data.get("pcr")
            if pcr is None:
                return False, 0
            return pcr < threshold, pcr
        if alert_type == AlertType.THEORETICAL_EDGE_ABOVE.value:
            edge = number("theoretical_edge_pct")
            if edge is None:
                return False, 0
            return edge > threshold, edge
        return False, 0

    def _check_rule_set(self, rules: List[Dict], market_data: Dict[str, Dict]) -> List[Dict]:
        """Evaluate *rules* against *market_data*; persist triggered events."""
        events = []
        normalized_data = {
            str(symbol).upper(): snapshot for symbol, snapshot in market_data.items()
        }

        for rule in rules:
            if rule.get("triggered") and rule.get("one_time"):
                continue

            symbol = rule["symbol"]
            data = normalized_data.get(str(symbol).upper(), {})
            if not data:
                continue

            triggered, current_value = self._evaluate_rule(rule, data)
            if triggered:
                event = AlertEvent(
                    rule_id=rule["rule_id"],
                    symbol=symbol,
                    alert_type=rule["alert_type"],
                    priority=rule["priority"],
                    message=rule["message"],
                    current_value=round(float(current_value), 4),
                    threshold=rule["threshold"],
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

            _notify_webhook(events)

        return events

    def set_webhook(self, url: str) -> Dict[str, Any]:
        """Configure the alert webhook delivery URL (Discord/Slack-compatible)."""
        os.makedirs(DATA_DIR, exist_ok=True)
        config = {"url": str(url or "").strip(), "enabled": True, "updated_at": datetime.now(timezone.utc).isoformat()}
        with open(WEBHOOK_CONFIG_FILE, "w") as fh:
            json.dump(config, fh, indent=2)
        return {"configured": bool(config["url"]), "enabled": True}

    def clear_webhook(self) -> Dict[str, Any]:
        """Disable webhook delivery."""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(WEBHOOK_CONFIG_FILE, "w") as fh:
            json.dump({"url": "", "enabled": False}, fh, indent=2)
        return {"configured": False, "enabled": False}

    def get_webhook(self) -> Dict[str, Any]:
        """Current webhook configuration (URL included; single-user API)."""
        config = _read_webhook_config()
        if not config:
            try:
                with open(WEBHOOK_CONFIG_FILE, "r") as fh:
                    stored = json.load(fh)
                return {"configured": False, "enabled": bool(stored.get("enabled")), "url": stored.get("url", "")}
            except Exception:
                return {"configured": False, "enabled": False, "url": ""}
        return {"configured": True, "enabled": True, "url": config["url"]}

    def check(self, market_data: Dict[str, Dict]) -> List[Dict]:
        """
        Check all rules against current market data.

        market_data: {"AAPL": {"price": 195, "iv": 0.25, "iv_rank": 40, "prev_price": 190}, ...}
        Returns: list of triggered alert events
        """
        return self._check_rule_set(self._read_rules(), market_data)

    def check_one(self, symbol: str, data: Dict) -> List[Dict]:
        """Check all rules against a single symbol's market-data snapshot."""
        return self._check_rule_set(self._read_rules(), {symbol.upper(): data})

    def get_history(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """Get recent alert history."""
        history = self._read_history()
        if symbol:
            history = [h for h in history if h["symbol"] == symbol.upper()]
        return history[-limit:]

