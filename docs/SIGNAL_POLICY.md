# ThetaForge Signal & Data Policy

This file exists to make high-value additions hard to remove. Every entry below
was researched against a real competitor or free public data source, then
implemented deliberately. If a future refactor flags one of these as
"unused", verify whether it is still the upstream supplier of another feature
before deleting. Treat every block as load-bearing.

## Free Data Sources (keep these alive)

| Source | Endpoint / Method | Why it matters | Where it lives |
| --- | --- | --- | --- |
| CBOE delayed quotes (no key) | `cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json` | Full option chain with bid/ask/last, IV, AND Greeks (delta/gamma/theta/vega/rho) — no brokerage required | `agents/data_ingestion/cboe_data.py` |
| CBOE underlying quote | `cdn.cboe.com/api/global/delayed_quotes/quotes/{SYM}.json` | Free delayed price/52-week context | `agents/data_ingestion/cboe_data.py` |
| CBOE VIX term structure | `cdn.cboe.com/api/global/us_indices/daily_history/{VIX9D,VIX3M,VIX6M,VIX1Y}.json` | Contango/inversion regime for the premium-selling gate | `agents/data_ingestion/cboe_data.py` |
| Yahoo VIX term-structure tickers | `^VIX9D ^VIX3M ^VIX6M ^VIX1Y` | Primary contango source; CBOE is the fallback | `agents/data_ingestion/free_data.py` |
| Yahoo earnings calendar | `yf.Ticker(...).get_earnings_dates(limit=4)` | `days_to_earnings` drives the earnings-avoidance gate | `agents/data_ingestion/free_data.py` |
| yfinance fundamentals | `yf.Ticker(...).info` (`shortPercentOfFloat`, `shortRatio`, `sharesShort`) | Short-interest squeeze fuel input | `agents/data_ingestion/free_data.py` |
| yfinance historical prices | `yf.Ticker(...).history(period=...)` | 20-day realized vol, trend, CPR inputs | `agents/data_ingestion/free_data.py` |

Requirement: all feeds must stay free (no paid API key). `requirements.txt`
documents this. Do not replace a free feed with a paid one without a
documented, tested justification.

### CBOE fetching rules (do not "simplify" these away)

- Requests must carry a browser `User-Agent` + `Referer: https://www.cboe.com/`
  or the CDN returns 403.
- Throttle to ~4 requests/second (`min_request_interval=0.25`) — the CDN is a
  free shared resource and faster polling gets blocked.
- All numeric parsing goes through `_finite_number` so NaN/None never become
  "real" prices.

## Volatility Signals (keep these gates)

| Gate | Source of truth | Implemented in |
| --- | --- | --- |
| IV Rank (52-week) | `calculate_iv_rank` | `agents/volatility/iv_metrics.py` |
| IV Percentile (52-week) | `calculate_iv_percentile` | `agents/volatility/iv_metrics.py` |
| IV history store | Daily ATM-IV snapshot appended per symbol (options-data-pipeline pattern); rank/percentile are only meaningful from real history | `agents/volatility/iv_history.py` |
| Realized volatility (20d) | `realized_volatility` (annualized log-return stdev) | `agents/volatility/iv_metrics.py` |
| Expected move (1-SD) | ATM straddle / IV × √(DTE/365) | `OptionsAnalytics.expected_move` |

IV Percentile is the stronger dual filter vs IVR alone (MarketChameleon-style:
56.8% premium-selling win rate vs 48.2%). The Brain's IV signal uses
`max(ivr, percentile)` for its edge decision. Never remove the percentile
input from `AIBrain.analyze` or the scanner's `iv_percentile` pass-through.

## Desk Analytics (keep these signals)

v0.8.0 computes the surfaces every institutional desk quotes from the SAME free
CBOE chain (per-strike delta + IV) — no paid feed required. `desk_analytics.py`
is load-bearing; a refactor must not replace it with spot-IV placeholders.

- **IV skew (RR25 / BF25)** — `calculate_iv_skew` delta-interpolates the
  25-delta put and call IVs from the most-traded expiry that brackets ±25Δ,
  computes `rr25 = iv_put25 − iv_call25` and `bf25 = wing_iv − atm_iv`, both
  normalized by ATM IV. Regime bands: `fear` (RR25 ≥ 0.15 or BF25 ≥ 0.15),
  `elevated_fear` (≥ 0.08), `complacent` (RR25 ≤ 0.02 and BF25 ≤ 0.05),
  else `neutral`. The Brain emits a `skew` signal and appends a surface read to
  the selected strategy's reasoning. Fail-closed: a chain without deltas (e.g.
  yfinance fallback) returns `None`, never a fake skew.
- **Earnings move edge** — `implied_earnings_move` (front-month ATM straddle /
  spot) is compared to `historical_earnings_moves` (realized post-earnings
  one-day moves from the symbol's own price history). `earnings_move_edge`
  reports `sell_iv` (implied > realized median → sell the move, collect the IV
  crush) or `buy_iv` (implied < realized → buy the move). It only nudges an
  already-existing vol edge; it never manufactures one.
- **Short interest** — `get_short_interest` surfaces `shortPercentOfFloat`,
  `shortRatio` (days to cover) and `sharesShort`. ≥30% float or ≥15 days to
  cover emits a `squeeze_fuel` signal; ≥15% / ≥8 days emits a moderate read.

Wiring to preserve: `background_scanner._analyze_one` computes
`iv_skew`, `short_interest`, `earnings_move` (all fail-closed to `None`) and
passes them to `AIBrain.analyze`; the notification payload and the advisor
`_market_snapshot` carry the same fields; `REGIME_WEIGHTS` includes `skew` and
`short_interest` sources summing to 1.0 per regime.

## Public Trade Journal (only real, placed trades)

`journal/` is a standalone static site (index.html + styles.css + app.js +
trades.json) hosted on GitHub Pages at
`https://journal.astraiva.app/` — the ONLY public surface. The private
dashboard/terminal is NOT deployed to Pages; it runs locally and stays
token-gated. The journal shows only trades that the project recommended AND
that were placed on the TWS terminal, sourced from the paper-order ledger.

- **Single source of truth:** `scripts/sync_journal.py` regenerates
  `journal/trades.json` from `data/paper_order_ledger.json`. Every entry needs
  a `recommendation_id` (a ThetaForge recommendation) and a live order status;
  cancelled/never-executed orders and anything without a recommendation are
  dropped. No fabricated or seeded entries survive a sync — if it wasn't placed
  on TWS, it cannot appear.
- **Narrative is preserved by `source_id`:** `scripts/add_trade.py --from-ledger
  <id>` attaches the thesis, exit note, tags, P&L, and close date to a ledger
  trade; the next sync carries that narrative forward. Entries without a
  `source_id` (manual trades entered via the CLI) are kept as-is.
- **Metrics (win rate, profit factor, max drawdown, streak) are COMPUTED from
  the journal entries** at render time — never hardcoded, never 100%-win,
  losing trades stay in the journal. This honesty rule is load-bearing for the
  user's credibility.
- Trade cards carry the thesis, the legs, and the outcome plus a timestamped
  receipt — the AfterHour/TradingView trust pattern.
- Do not put the terminal back on Pages. The dashboard exposes order and
  position internals and belongs to the owner only.

## VIX Term Structure / Contango (keep this gate)

The `is_vix_contango` helper and the Brain's `term_structure` check implement
the Option Alpha / Tastytrade playbook: **selling premium into an inverted
VIX curve is structurally toxic** (front-month fear premium). In `AIBrain`:

- An `"inverted"` term structure downgrades an otherwise strong sell-premium IV
  signal to neutral.
- `_select_best_strategy` returns `no_trade` on an inverted curve before any
  premium-selling strategy is eligible.
- Missing VIX-index data resolves to `None` → neutral, never to "contango".
  A missing index must not silently authorize premium selling.

## Scanner Data Flows (keep the wiring)

`background_scanner._analyze_one` now feeds the Brain:
`current_iv`, `hv_20`, `iv_percentile`, `expected_move_pct`,
`vix_term_structure`, `days_to_earnings`, `iv_skew`, `short_interest`,
`earnings_move`, and `relative_strength` (6-month return vs SPY, computed once
per scan in `_spy_126_return` and shared across the fan-out).

Fail-closed rule (v0.6.8, preserved): missing price, chain, VIX, or history is
a recorded skip reason, never a placeholder trade signal. The volatility
enrichments added in v0.7.0 degrade to `None` individually — one broken source
must not manufacture (or block) a trade by itself. The v1.6.0 high-win-rate
gates keep this split: `relative_strength` is the *only* soft input (missing RS
disables the laggard veto); trend, expected-move, DTE, and earnings proximity
are hard gates that require their underlying data to exist.

## High-Win-Rate Entry Gates (v1.6.0, keep intact)

`agents/trade_engine/high_winrate.py` holds the research-backed context vetoes
applied on top of the generic quality gates (POP, IVR, credit-to-width, POT):

- `trend_alignment_ok`: a bull structure into a confirmed downtrend (or a bear
  structure into an uptrend) is refused in both the Brain's
  `_select_best_strategy` and the Recommender's step 4c.
- `expected_move_buffer_ok`: new short-premium strikes must sit at/outside the
  1-SD expected move (~68% expiry POP floor).
- `entry_dte_ok`: no new short premium inside the 21-DTE gamma window or beyond
  60 DTE; debit floors at 14 DTE.
- `earnings_window_ok`: no new short premium into an earnings print
  (`days_to_earnings <= 7`), mirroring the Brain's existing earnings veto.
- `relative_strength_ok` (IBD "L"): directional short premium only on names
  leading the market over 6 months. **This is the one soft gate** — missing RS
  data disables it, never fabricates a reject.

Management of *open* positions is `agents/trade_engine/trade_manager.py`:
50%-of-credit take-profit, close/roll at 21 DTE, 2×-credit loss stop,
pre-earnings close, tested-strike review, plus portfolio caps. The
`POST /api/advisor/positions/management` endpoint only *recommends* actions —
order submission stays exclusively in the Bridge, so there is still exactly one
execution path.

## Behavior to Preserve

1. Do not replace IVR with spot IV. The scanner passes `current_iv` and the
   Brain computes rank; a flat `iv_rank=0`/`iv_rank=50` default hides a data
   problem.
2. Do not clamp IV rank at 100. IV above the 52-week high is a genuine
   expansion regime and must remain visible to the gates.
3. Do not require 52-week history before the first scan. `IVHistoryStore`
   returns `None` below `MIN_SAMPLES` and callers fall back to neutral.
4. Earnings proximity (`days_to_earnings <= 7`) blocks new positions. It is a
   gate, not a signal.

## Dead-Code Removal Regret List

The following were removed in v0.7.0 as verified-dead (zero production
importers). If a future feature needs them, reimplement with a production
caller in the same commit:

- `agents/strategies/` (13 legacy strategy classes; only `tests` imported them)
- `agents/sentiment/` (NLP + Reddit)
- `agents/execution/`, `agents/llm_reasoning/`
- `agents/backtest/backtester.py` (kept `advanced_backtest.py`: `SignalEngine`
  is imported by `ai_brain.py` and `tv_indicators.py`)
- `agents/flow_analysis/scanner_pipeline.py`, `agents/flow_analysis/dark_pool.py`
- `agents/data_ingestion/market_data.py`
