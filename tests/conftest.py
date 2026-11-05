"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _no_live_learning(monkeypatch):
    """Learned adjustments come from the live public journal; tests must not.

    Tests that exercise learning pass adjustments explicitly.
    """
    import agents.learning.trade_review as trade_review

    monkeypatch.setattr(trade_review, "load_adjustments", lambda url=None: [])
    try:
        import orchestrator.routes.advisor as advisor
    except Exception:
        return
    monkeypatch.setattr(advisor, "load_adjustments", lambda url=None: [], raising=False)
