"""Executor guards: same-name dedupe and covered-call inventory (v1.17.27)."""
import importlib.util
import os
import sys
from pathlib import Path

DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent / "deployment"


def _load_executor():
    os.environ.setdefault("ADVISOR_URL", "http://advisor.test")
    os.environ.setdefault("ADVISOR_API_TOKEN", "advisor-token")
    os.environ.setdefault("BRIDGE_ACCESS_TOKEN", "bridge-token")
    if str(DEPLOYMENT_DIR) not in sys.path:
        sys.path.insert(0, str(DEPLOYMENT_DIR))
    spec = importlib.util.spec_from_file_location("vm_auto_executor", DEPLOYMENT_DIR / "vm_auto_executor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ex = _load_executor()

LEDGER = [
    {"symbol": "DIA", "asset_class": "equity", "status": "Submitted", "filled": 0},      # working
    {"symbol": "XLF", "asset_class": "equity", "status": "Filled", "filled": 26},
    {"symbol": "AMZN", "asset_class": "equity", "status": "Filled", "filled": 3, "closed_by": "z"},
    {"symbol": "QQQ", "asset_class": "equity", "status": "Inactive", "filled": 0},       # lapsed
    {"symbol": "F", "strategy": "cash_secured_put", "status": "PreSubmitted", "filled": 0},
    {"symbol": "SOFI", "strategy": "cash_secured_put", "status": "Filled", "filled": 1},
]


def test_pending_orders_count_as_held():
    """Oct 20 2026: DIA was bought three times because a working entry was
    invisible to the duplicate guard."""
    assert ex.held_symbols(LEDGER, "equity") == ["DIA", "XLF"]
    assert ex.held_symbols(LEDGER, "options") == ["F", "SOFI"]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, positions):
        self._positions = positions

    def get(self, url, **_):
        return _Resp(self._positions)


def test_covered_call_inventory_excludes_equity_engine_shares():
    """Equity-engine shares can be sold on a stop at any moment; a call written
    against them would be left naked."""
    positions = [
        {"symbol": "XLF", "position": 126, "contract_type": "STK"},   # 26 engine + 100 assigned
        {"symbol": "F", "position": 100, "contract_type": "STK"},     # assigned from a CSP
        {"symbol": "ITUB", "position": 76, "contract_type": "STK"},   # under one lot
        {"symbol": "SOFI", "position": -1, "contract_type": "OPT"},
    ]
    ledger = LEDGER + [{"symbol": "ITUB", "asset_class": "equity", "status": "Filled", "filled": 76}]
    wheel = ex.fetch_wheel_share_positions(_Client(positions), ledger)
    assert wheel == [{"symbol": "XLF", "position": 100.0}, {"symbol": "F", "position": 100.0}]
