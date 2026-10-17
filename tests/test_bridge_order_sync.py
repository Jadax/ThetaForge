"""Ledger-sync identity tests: cross-session orderId bleed must be impossible.

IB gateway restarts reset the API orderId sequence; the Bridge historically
matched ledger records to live trades by that bare number, so older records
were overwritten with a different order's fill data. The sync must match on
the stamped orderRef (ledger id) or a persisted permId first, and never let a
record from a dead session adopt this session's colliding order.
"""
import json

import bridge.main as bm


class FakeOrder:
    def __init__(self, order_id, order_ref="", perm_id=0):
        self.orderId = order_id
        self.orderRef = order_ref
        self.permId = perm_id


class FakeStatus:
    def __init__(self, status="Filled", filled=0, remaining=0, avg=0):
        self.status = status
        self.filled = filled
        self.remaining = remaining
        self.avgFillPrice = avg


class FakeContract:
    def __init__(self, symbol, sec_type="STK"):
        self.symbol = symbol
        self.secType = sec_type


class FakeTrade:
    def __init__(self, symbol, order_id, order_ref="", perm_id=0, status="Filled",
                 filled=0, remaining=0, avg=0, sec_type="STK"):
        self.contract = FakeContract(symbol, sec_type)
        self.order = FakeOrder(order_id, order_ref, perm_id)
        self.orderStatus = FakeStatus(status, filled, remaining, avg)


class FakeIb:
    def __init__(self, trades, connected=True):
        self._trades = trades
        self._connected = connected

    def isConnected(self):
        return self._connected

    def trades(self):
        return self._trades


def _records(monkeypatch, tmp_path, records):
    ledger = tmp_path / "paper_order_ledger.json"
    ledger.write_text(json.dumps(records), encoding="utf-8")
    monkeypatch.setattr(bm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bm, "PAPER_ORDER_LEDGER_FILE", str(ledger))
    return ledger


def _sync(monkeypatch, tmp_path, records, trades):
    ledger = _records(monkeypatch, tmp_path, records)
    fake = FakeIb(trades)
    monkeypatch.setattr(bm, "ib", fake)
    result = bm._sync_order_ledger()
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    return result, saved


def test_stale_record_does_not_adopt_this_sessions_oid(monkeypatch, tmp_path):
    # Session N-1 record (DHT, oid 18) and this session's AAPL order (oid 18,
    # stamped 'rec-new'): the bleed wrote AAPL's fill onto the DHT record.
    stale = {"id": "rec-old", "asset_class": "equity", "symbol": "DHT",
             "ibkr_order_id": 18, "status": "PendingSubmit", "filled": 0,
             "average_fill_price": 0.0}
    fresh = {"id": "rec-new", "asset_class": "equity", "symbol": "AAPL",
             "ibkr_order_id": 6, "status": "Submitted", "filled": 0,
             "average_fill_price": 0.0}
    trade = FakeTrade("AAPL", 18, order_ref="rec-new", filled=3, avg=342.6)
    _, saved = _sync(monkeypatch, tmp_path, [stale, fresh], [trade])
    assert saved[0]["status"] == "PendingSubmit"
    assert saved[0]["filled"] == 0
    assert saved[1]["status"] == "Filled"
    assert saved[1]["filled"] == 3


def test_legacy_record_adopts_its_live_order_in_the_current_session(monkeypatch, tmp_path):
    # An order placed before orderRef stamping, still working after a
    # reconnect: the numeric match holds while the live order carries no
    # other record's stamp.
    record = {"id": "rec-legacy", "asset_class": "equity", "symbol": "VRNS",
              "ibkr_order_id": 10, "status": "Submitted", "filled": 0,
              "average_fill_price": 0.0}
    trade = FakeTrade("VRNS", 10, order_ref="", filled=12, avg=48.64)
    _, saved = _sync(monkeypatch, tmp_path, [record], [trade])
    assert saved[0]["filled"] == 12
    assert saved[0]["status"] == "Filled"


def test_stamped_record_matches_by_orderref(monkeypatch, tmp_path):
    # Gateway restart rolled the ids: the record's stored oid (7) now points
    # at a different order; only the orderRef identity may match.
    record = {"id": "rec-new", "asset_class": "equity", "symbol": "XLV",
              "ibkr_order_id": 7, "status": "Submitted", "filled": 0,
              "average_fill_price": 0.0}
    other = FakeTrade("AAPL", 7, order_ref="someone-else", filled=9, avg=1.0)
    mine = FakeTrade("XLV", 99, order_ref="rec-new", filled=8, avg=176.54)
    _, saved = _sync(monkeypatch, tmp_path, [record], [other, mine])
    assert saved[0]["filled"] == 8
    assert saved[0]["average_fill_price"] == 176.54


def test_bare_oid_fallback_never_crosses_symbols(monkeypatch, tmp_path):
    record = {"id": "rec-old", "asset_class": "equity", "symbol": "ECO",
              "ibkr_order_id": 14, "status": "PendingSubmit", "filled": 0,
              "average_fill_price": 0.0}
    trade = FakeTrade("CCC", 14, order_ref="", filled=86, avg=7.62)
    _, saved = _sync(monkeypatch, tmp_path, [record], [trade])
    assert saved[0]["status"] == "PendingSubmit"
    assert saved[0]["filled"] == 0


def test_persisted_perm_id_matches_across_sessions(monkeypatch, tmp_path):
    record = {"id": "rec-old", "asset_class": "equity", "symbol": "SPY",
              "ibkr_order_id": 3, "ibkr_perm_id": 424242, "status": "Submitted",
              "filled": 0, "average_fill_price": 0.0}
    trade = FakeTrade("SPY", 77, perm_id=424242, filled=5, avg=599.9)
    _, saved = _sync(monkeypatch, tmp_path, [record], [trade])
    assert saved[0]["filled"] == 5
    assert saved[0]["status"] == "Filled"


def test_perm_id_is_persisted_when_ibkr_assigns_it(monkeypatch, tmp_path):
    record = {"id": "rec-new", "asset_class": "equity", "symbol": "XLE",
              "ibkr_order_id": 6, "status": "Submitted", "filled": 0,
              "average_fill_price": 0.0}
    trade = FakeTrade("XLE", 6, order_ref="rec-new", perm_id=31337, filled=19, avg=67.98)
    _, saved = _sync(monkeypatch, tmp_path, [record], [trade])
    assert saved[0]["ibkr_perm_id"] == 31337
