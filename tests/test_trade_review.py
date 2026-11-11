"""Post-trade review and learned setup adjustments (agents/learning)."""
from agents.equity_trader.equity_recommender import EquityRecommender
from agents.learning import trade_review as tr


def _equity(pnl, exit_reason="close_stop", closed="2026-11-01", tilt="mixed", rsi=62.0,
            strategy="equity_momentum", risk=50.0):
    return {
        "status": "closed", "asset_class": "equity", "strategy": strategy,
        "net_pnl": pnl, "capital_at_risk": risk, "closed": closed,
        "close_order": {"reason": exit_reason},
        "conditions": {"trend": "above_200d", "rsi_14": rsi, "risk_tilt": tilt},
    }


def _credit(pnl, ivr=55.0, dte=45, strategy="bear_call_credit", closed="2026-11-01", trend="bearish"):
    return {
        "status": "closed", "asset_class": "options", "strategy": strategy, "net_pnl": pnl,
        "max_profit": 30.0, "capital_at_risk": 70.0, "dte_at_entry": dte, "closed": closed,
        "close_order": {"reason": "close_profit" if pnl > 0 else "close_loss"},
        "conditions": {"iv_rank": ivr, "trend": trend},
    }


def test_review_names_the_exit_and_the_entry_context():
    review = tr.review_trade(_equity(-50.0, rsi=74))
    assert review["verdict"] == "loss" and review["units"] == -1.0
    assert any("overbought" in d for d in review["drivers"])
    assert any("mixed tape" in d for d in review["drivers"])
    assert "stop" in review["lesson"]


def test_near_zero_outcome_is_a_scratch():
    assert tr.review_trade(_equity(1.0, "close_pre_earnings"))["verdict"] == "scratch"


def test_open_trades_get_no_review():
    assert tr.review_trade({**_equity(0.0), "status": "open"}) is None


def test_a_reliably_losing_setup_is_vetoed():
    trades = [_equity(-50.0) for _ in range(5)] + [_equity(20.0)]
    learned = tr.learn(trades, today="2026-11-05")
    vetoes = [a for a in learned["adjustments"] if a["action"] == "veto"]
    assert any(a["when"] == {"asset": "equity", "strategy": "equity_momentum"} for a in vetoes)


def test_too_few_trades_learn_nothing():
    assert tr.learn([_equity(-50.0) for _ in range(tr.MIN_SETUP_SAMPLES - 1)],
                    today="2026-11-05")["adjustments"] == []


def test_noisy_setup_is_left_alone():
    trades = [_equity(p) for p in (-50, 60, -40, 55, -45, 50)]
    assert tr.learn(trades, today="2026-11-05")["adjustments"] == []


def test_reliable_winner_earns_a_capped_boost():
    trades = [_credit(18.0) for _ in range(7)] + [_credit(-10.0)]
    learned = tr.learn(trades, today="2026-11-05")
    boost = [a for a in learned["adjustments"] if a["action"] == "boost"]
    assert boost and all(a["points"] == tr.BOOST_POINTS for a in boost)


def test_old_losses_age_out_so_a_vetoed_setup_is_retested():
    old = [_equity(-50.0, closed="2026-01-05") for _ in range(8)]
    assert tr.learn(old, today="2026-02-01")["adjustments"]
    assert tr.learn(old, today="2026-11-05")["adjustments"] == []


def test_removed_rule_exits_teach_nothing():
    trades = [_equity(-20.0, "close_pre_macro") for _ in range(8)]
    assert tr.learn(trades, today="2026-11-05")["closed_learnable"] == 0


def test_setup_adjustment_matches_only_its_own_conditions():
    adjustments = [{"when": {"asset": "options", "strategy": "bear_call_credit", "ivr_band": "ivr<30"},
                    "action": "penalize", "points": -5.0, "evidence": "n=6"}]
    rich = tr.trade_features({"asset_class": "options", "strategy": "bear_call", "conditions": {"iv_rank": 60}})
    thin = tr.trade_features({"asset_class": "options", "strategy": "bear_call", "conditions": {"iv_rank": 20}})
    assert tr.setup_adjustment(adjustments, rich) == (False, 0.0, [])
    veto, delta, reasons = tr.setup_adjustment(adjustments, thin)
    assert not veto and delta == -5.0 and reasons


def test_equity_recommender_refuses_a_vetoed_setup():
    read = {"symbol": "XYZ", "signal": "buy", "score": 80, "strategy": "equity_momentum",
            "reasoning": "", "price": 50.0, "atr_value": 1.0, "above_200d": True,
            "rsi_14": 62.0, "market_risk_tilt": "mixed"}
    vetoes = [{"when": {"asset": "equity", "strategy": "equity_momentum"}, "action": "veto",
               "points": None, "evidence": "n=8, mean -0.58"}]
    assert EquityRecommender().build(read, capital=5000).gate is None
    refused = EquityRecommender().build(read, capital=5000, learned_adjustments=vetoes)
    assert refused.gate == "learned_setup"


def test_bucket_risk_override_sizes_the_equity_position():
    read = {"symbol": "XYZ", "signal": "buy", "score": 80, "strategy": "etf_rotation",
            "reasoning": "", "price": 20.0, "atr_value": 0.5, "above_200d": True}
    small = EquityRecommender().build(read, capital=1000)
    bucket = EquityRecommender().build(read, capital=1000, risk_per_trade_pct=5)
    assert bucket.max_loss_total > small.max_loss_total


def test_equity_penalty_bites_once_the_score_drops_below_the_floor():
    """ETF rotation was penalized (-5) on Nov 11 but still traded at full score."""
    from agents.equity_trader.equity_brain import BUY_SCORE_FLOOR
    penalty = [{"when": {"asset": "equity", "strategy": "etf_rotation"}, "action": "penalize",
                "points": -5.0, "evidence": "n=10, mean -0.22"}]
    base = {"symbol": "XLF", "signal": "buy", "strategy": "etf_rotation", "reasoning": "",
            "price": 50.0, "atr_value": 1.0, "above_200d": True}
    strong = EquityRecommender().build({**base, "score": BUY_SCORE_FLOOR + 10}, capital=5000,
                                       learned_adjustments=penalty)
    assert strong.gate is None and strong.learning_delta == -5.0
    marginal = EquityRecommender().build({**base, "score": BUY_SCORE_FLOOR + 3}, capital=5000,
                                         learned_adjustments=penalty)
    assert marginal.gate == "learned_setup"
