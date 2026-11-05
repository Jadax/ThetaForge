# Learning Loop and Capital Buckets

## The learning loop (`agents/learning/trade_review.py`)

Every closed trade in the public journal is reviewed and fed back into the
Recommenders. One scoring path, with learned priors on top.

1. **Review** (per trade, written into `journal/trades.json` as `review`):
   verdict (`win`/`loss`/`scratch`), drivers (exit reason, outcome in risk
   units, entry context such as overbought RSI, weak tape, thin IV rank, too few
   DTE, puts sold into a downtrend) and a one-line lesson.
2. **Learn** (journal top-level `learning`): closed trades from the last
   `LEARNING_WINDOW_DAYS` (120) are grouped by setup, meaning strategy crossed
   with one entry feature at a time:
   - options: trend, IV-rank band, DTE band
   - equity: trend, RSI band, market risk tilt

   Outcomes are in the trade's own risk unit: R for stocks, fraction of the
   credit for credit spreads and CSPs, P&L / debit for debit spreads.
3. **Adjust**: with at least `MIN_SETUP_SAMPLES` (6) trades in a setup:
   - **veto** when mean + 1 standard error < 0 (reliably losing)
   - **penalize** −5 composite points when the mean is negative
   - **boost** +3 points when mean − 1 SE > 0 with ≥ 8 trades

   Deltas are capped at −10 / +6, so learning nudges the research-based score
   and never replaces it.
4. **Apply**: `TradeRecommender.generate_recommendations(learned_adjustments=…)`
   (Step 4e, before ranking) and `EquityRecommender.build(learned_adjustments=…)`
   (veto → gate `learned_setup`). The Advisor loads the adjustments from the
   published journal off the event loop (30-minute cache, fail-open).

Guard rails:
- Trades closed by a rule that no longer exists (`close_pre_macro`) teach
  nothing and are excluded.
- The rolling window lets a vetoed setup's losses age out, so it is re-tested
  rather than banned forever.
- Missing data never fabricates a veto.
- Nothing here loosens the research gates (IV-rank floor, expected-move buffer,
  DTE band, earnings/macro vetoes, Bridge checks).

**On "100% wins":** no real strategy wins every trade. Pushing the win rate
toward 100% (ever-smaller credits, cutting winners early) lowers expectancy.
The loop optimizes expectancy per unit of risk, which is what compounds.

## Capital buckets

| Bucket | Id | Capital | Sizing | Takes |
| --- | --- | --- | --- | --- |
| Algo Trader | `algo_trader` | executor `CAPITAL_LIMIT` (weekly reservation) | recommender defaults (2% options, 1% stocks) | every qualifying alert not taken below |
| $1000 to Infinity | `infinity_1k` | $1,000 + its own realized P&L (compounds) | `INFINITY_RISK_PCT` 10% options / 5% stocks, of bucket equity | top setups only: options composite ≥ 78 and POP ≥ 70; stocks score ≥ 80; ≤ 5 open; risk ≤ available |

- One paper account, one ledger, one Advisor, one Bridge. Every ledger record
  carries `bucket`, and close records inherit it.
- The **Bridge** enforces the self-funded bucket's hard limit: open risk ≤
  bucket equity (`BUCKET_STARTING_CAPITAL`, `_bucket_state`,
  `_check_bucket_capital`). The Algo Trader's weekly reservation counts only
  its own records. `GET /orders` returns per-bucket state.
- The **executor** gives the $1,000 bucket first look at each alert, sizing a
  recommendation off the bucket's current equity. If the higher bar isn't met,
  the alert flows to the Algo Trader as before.
- The **journal** tags each trade's `bucket` and publishes a top-level
  `buckets` summary (realized, unrealized, equity).
