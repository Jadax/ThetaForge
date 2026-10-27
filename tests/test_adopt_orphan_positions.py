"""Orphan re-adoption: IBKR is the source of truth for carried receipts."""
import json

from scripts import adopt_orphan_positions as adopt

NOW = "2026-10-24T00:00:00+00:00"


def _carried(source_id, symbol, qty, entry, stop, status="Filled", filled=None, submitted="2026-09-29T13:43:00+00:00"):
    return {
        "id": source_id, "source_id": source_id, "symbol": symbol, "asset_class": "equity",
        "carried": True, "strategy": "etf_rotation", "opened": "2026-09-29",
        "entry_price": entry, "stop_price": stop, "target_price": entry * 1.05,
        "order": {"status": status, "filled": qty if filled is None else filled,
                  "quantity": qty, "submitted_at": submitted, "average_fill_price": entry},
    }


def test_adopts_only_shares_ibkr_proves_are_unmanaged():
    ledger = [{"id": "live-xlv", "asset_class": "equity", "symbol": "XLV", "status": "Filled", "filled": 8}]
    journal = [
        _carried("a", "XLV", 8, 170.5, 165.8),
        _carried("b", "XLV", 8, 170.9, 166.0, submitted="2026-09-29T18:40:00+00:00"),
        # Frozen "Submitted 0/8" receipt that DID fill (IBKR holds the shares).
        _carried("c", "XLV", 8, 170.5, 165.8, status="Submitted", filled=0.0,
                 submitted="2026-09-29T19:33:00+00:00"),
        _carried("d", "XLV", 8, 170.0, 165.0, submitted="2026-09-29T19:59:00+00:00"),
        _carried("e", "MARA", 29, 12.19, 10.42),
    ]
    records = adopt.adoption_records(ledger, journal, {"XLV": 32.0, "MARA": 29.0}, NOW)
    by_id = {record["id"]: record for record in records}
    # 32 held - 8 managed = 24 unmanaged -> the three oldest XLV receipts.
    assert set(by_id) == {"a", "b", "c", "e"}
    assert by_id["c"]["status"] == "Filled" and by_id["c"]["filled"] == 8
    assert by_id["e"]["max_loss_total"] == round((12.19 - 10.42) * 29, 2)
    assert all(record["recommendation_id"].startswith("adopted:") for record in records)


def test_never_adopts_more_than_is_held_or_already_known():
    journal = [_carried("a", "NOK", 53, 10.38, 9.5)]
    assert adopt.adoption_records([], journal, {"NOK": 20.0}, NOW) == []
    known = [{"id": "a", "asset_class": "equity", "symbol": "NOK", "status": "Filled", "filled": 53}]
    assert adopt.adoption_records(known, journal, {"NOK": 53.0}, NOW) == []


def test_dry_run_leaves_ledger_untouched(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text("[]", encoding="utf-8")
    journal_path = tmp_path / "trades.json"
    journal_path.write_text(json.dumps({"trades": [_carried("a", "NOK", 53, 10.38, 9.5)]}), encoding="utf-8")
    positions = tmp_path / "positions.json"
    positions.write_text(json.dumps({"NOK": 53}), encoding="utf-8")
    args = ["--ledger", str(ledger_path), "--journal", str(journal_path), "--positions", str(positions)]
    assert adopt.main(args) == 0
    assert json.loads(ledger_path.read_text()) == []
    assert adopt.main(args + ["--apply"]) == 0
    assert [r["id"] for r in json.loads(ledger_path.read_text())] == ["a"]


def test_never_adopts_a_closed_receipt():
    """Oct 27 2026 Oracle move: yesterday's closed XLF exit (older) was matched
    ahead of today's open XLF buy of the same size."""
    closed = {**_carried("old", "XLF", 26, 50.0, 48.0, submitted="2026-10-01T13:43:00+00:00"), "status": "closed"}
    live = _carried("new", "XLF", 26, 56.22, 54.81, submitted="2026-10-26T19:40:00+00:00")
    adopted = adopt.adoption_records([], [closed, live], {"XLF": 26.0}, "2026-10-27T14:30:00+00:00")
    assert [record["id"] for record in adopted] == ["new"]
