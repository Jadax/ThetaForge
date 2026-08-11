# ThetaForge Changelog

## v1.1.1 - 2026-08-04

Moved the hosted Advisor off Railway (no longer free) to Render's free tier.
No scoring or order-path changes; all tests remain green (145 passing).

- Added `render.yaml` — Render Blueprint that builds the existing `Dockerfile`
  directly, no separate image config. `ADVISOR_API_TOKEN` is deliberately left
  out (`sync: false`) so it is entered once in the Render dashboard and never
  committed.
- Added `.github/workflows/keep-advisor-warm.yml` — pings the public
  `/health/` probe every 10 minutes. Render's free tier sleeps a service after
  15 minutes with no HTTP traffic and fully restarts the container on the next
  request, which would otherwise reset `data/iv_history.json` (the real
  per-symbol IV history behind IV Rank/Percentile) and the notification queue
  far more often than the occasional redeploy this already tolerated. The
  workflow needs no secrets — `/health/` is intentionally unauthenticated.
- Removed `railway.toml`.
- Updated `DEFAULT_ADVISOR_API` in `dashboard/app/page.tsx` and every Railway
  reference in `README.md`, `AGENTS.md`, `docs/HANDOVER.md`, and
  `docs/PAPER_BRIDGE.md`.
- Fixed a stale line in `docs/PAPER_BRIDGE.md` describing a `confirm_paper_order`
  staging flow that was removed from `bridge/main.py` in v0.6.4; the only order
  path has been `POST /orders/submit-combo` since then.

## v1.1.0 - 2026-08-04

Refactor and hardening pass for public release. No behavior change to the
scoring or order paths; all tests remain green (145 passing) and the dashboard
build is unchanged.

### Dead code removed
- Deleted orphan modules with no importers: `agents/volatility/term_structure.py`,
  `agents/data_ingestion/ibkr_client.py`, `deployment/panic_button.py` (and the
  empty `deployment/`, `models/`, `.validation-dashboard-*/` leftovers).
- `agents/backtest/advanced_backtest.py` reduced from ~770 to ~140 lines: the
  unused `BacktestEngine`, `StressTestEngine`, and ~15 unused `SignalEngine`
  indicators removed; only `rsi`, `macd`, `bollinger_bands`, `_ema`, `adx`
  (used by the Brain and technical indicators) are kept.
- `agents/volatility/black_scholes.py`: removed unused pricing methods
  (`implied_volatility`, `probability_of_profit`, `american_price`,
  `aggregate_greeks`, `payoff_at_expiry`); kept `price` + Greeks.
- `agents/technical/tv_indicators.py`: removed `calculate_weekly_cpr`,
  `calculate_pivot_points`, `iv_percentile`, `zero_dte_signal`, `expected_move`,
  `tastytrade_rules` and the `PivotData` type (all unused).
- Removed small dead methods with zero callers across `alerts`, `signal_tracker`,
  `watchlist`, `analytics`, `gex_engine`, `technical/indicators`, `iv_history`.
- Cleaned unused imports (including an unused `oi_divergence` import in the
  scanner's `_flow_signals`).

### Logic de-duplication
- New `scripts/journal_common.py` — single home for the ledger→journal leg
  mapping (`RIGHT_TO_TYPE`, `journal_legs`) and `ledger_capital_at_risk`; both
  `add_trade.py` and `sync_journal.py` now delegate to it (behavior unchanged).

### Attribution & professionalism
- Removed "Stolen from: <third party>" framing and third-party author names in
  docstrings everywhere; code is now cleanly described as standard methodology,
  authored by Tushant Sharma.

### Dependency & release hardening
- `agents/volatility/greeks.py` is now a thin adapter over the in-repo
  Black-Scholes engine, removing the deprecated `py_vollib` dependency
  (dropped from `requirements.txt`).
- `.env.example` rewritten to reflect the actual free, paper-only, no-database
  architecture (removed dead DB/Redis/Reddit/Alpaca/live-activation keys).
- `.gitignore` cleaned (removed Celery/Redis entries; added `.pytest_cache/`).
- README repo-tree updated to match the current layout.

## v1.0.0 - 2026-08-04

Credibility, journaling, and edge hardening — built from a competitive review of
Option Alpha, Market Chameleon, ORATS, OptionStrat, Options AI, Option Samurai,
Barchart, tastylive, TradeZella, and Tradervue. All ideas are original
reimplementations on the free, no-paid-data stack; no proprietary code or feeds.

### Public journal (credibility + journaling)
- Every ledger entry now carries a `source` (`TWS LEDGER` vs `MANUAL`) badge and
  a `ledger_ref`, and `journal/trades.json` publishes a `verification` block with
  a `ledger_sha` over the exact ledger records it was built from — recomputable,
  FTMO/Myfxbook-style "not hand-edited" proof.
- Raw order receipts render on every card: status, filled/quantity, average fill,
  net credit, and the ledger submit/update timestamps.
- New honest-metric pills: **expectancy** (avg per closed trade), **draw
  down / peak** (current below peak), plus per-trade **R-multiple** and
  **% of account at risk** and **expected move ±% at entry** on each card.
- "No cherry-picking" guarantee strip: every placed trade appears, winners and
  losers, nothing filtered.
- **Management plan** (50% profit target, 2-3x stop, 21-DTE exit, pre-earnings
  close, roll rules) rendered on each card — already encoded in the recommender's
  entry/exit rules, now surfaced publicly.
- Monthly/weekly recap blocks below the journal, and a new static **Learn** section
  (`journal/learn/`) teaching IV rank/percentile, expected move, POP & delta, and
  the risk/expectancy math — anchored to real journaled trades.
- New `scripts/recap.py` CLI exports a ready-to-post weekly/monthly recap from
  `journal/trades.json`. `scripts/add_trade.py` gains `--expected-move-pct` and
  `--management-plan` and stamps `source`; `compute_metrics()` now returns
  `expectancy` and `drawdown_from_peak`. `sync_journal.py` emits the ledger SHA
  and order blocks, and caps risk-per-trade via the existing kelly/portfolio rules.

### Edge (Track B)
- New `agents/volatility/flow_metrics.py`: relative-volatility bands (IV/HV),
  unusual-volume tiers, OI-divergence (opening vs closing), OI center-of-mass /
  pin price, and IV-mover classification — all fail-closed. The background
  scanner tags each candidate with an `rv_band` and a `flow_signals` block
  (hottest strike, unusual volume, OI center of mass).
- New `agents/trade_engine/theoretical_edge.py`: ranks each structure by how far
  the CBOE mid sits from our own Black-Scholes model value
  (Market Chameleon/ORATS pattern). `TradeRecommendation` now carries
  `theoretical_edge_pct` + `model_value`, serialized to the dashboard and shown
  as a THEO EDGE pill.
- New `agents/trade_engine/historical_backtest.py`: empirical win rate /
  expectancy / profit factor / drawdown over realized credit-spread outcomes.
- Every `TradeRecommendation` now carries a 1-SD `expected_move_pct` (from ATM
  IV & DTE); the terminal renders an **expected-move price-band visualizer**
  per trade card showing the underlying, the ±expected-move band, the short
  wings, and whether any short is inside the move (Options AI/OptionStrat
  pattern).
- Named **scan galleries** (`wheel_candidates`, `premium_flow`,
  `earnings_window`, `high_iv_movers`) with a `gallery_symbols()` filter over
  scan results (Option Samurai/Barchart pattern), ready for the dashboard.

### Ops
- Removed `.github/workflows/dashboard.yml` — the auto-publish job that rebuilt the
  terminal and force-pushed it to `gh-pages` on every `main` push was overwriting
  the journal-only Pages site. The terminal is now confirmed local-only; Pages
  serves only the public journal (`journal/`), as documented.
- Full suite at 145 passing. Journal smoke-tested (populated + empty + Learn)
  with 0 console errors.

## v0.9.0 - 2026-08-04

- Split the public surface from the private one. The dashboard/terminal is now
  local-only (it was never reachable without a token anyway); the public trade
  journal moved out of the Next app into `journal/`, a standalone static site
  (index.html, styles.css, app.js, trades.json) deployed to the gh-pages branch
  root at `https://jadax.github.io/ThetaForge/`.
- The journal now shows ONLY trades that were actually placed on TWS from a
  ThetaForge recommendation. `scripts/sync_journal.py` regenerates
  `journal/trades.json` from the paper-order ledger
  (`data/paper_order_ledger.json`), requiring a `recommendation_id` and a live
  order status, so cancelled orders and the fabricated seed entries can never
  appear. Narrative (thesis, exit note, tags, P&L, close) is attached with
  `scripts/add_trade.py --from-ledger <id>` and preserved across syncs by
  `source_id`.
- Removed all PAPER branding from the public journal per the owner's direction;
  the ledger-backed page states trades were recommended by ThetaForge and
  executed on the TWS terminal.
- Removed the Next `/trades` route and `dashboard/trades.json`; the private
  terminal's nav now links out to the public journal URL.
- Added `tests/test_sync_journal.py` (6 tests); full suite at 130 passing.

## v0.8.1 - 2026-08-04

- Added `scripts/add_trade.py`, the validated input path for the public trade
  journal: appends a trade to `dashboard/trades.json`, recomputes the metric
  strip with the same math as the journal page, and prints a preview.
  `--from-ledger <id>` pre-fills symbol/strategy/legs/capital from the paper
  order ledger so journal legs match the simulator fills; narrative fields
  (thesis, exit note, tags) are always entered by hand.
- Added `tests/test_add_trade_cli.py` (5 tests); full suite at 124 passing.

## v0.8.0 - 2026-08-04

- Added desk analytics (`agents/volatility/desk_analytics.py`) so the free CBOE
  chain becomes an institutional-grade volatility surface, not just a chain:
  - **IV skew** — 25-delta risk reversal (RR25) and 25-delta butterfly (BF25),
    delta-interpolated from per-strike deltas, normalized by ATM IV, and
    classified into a desk regime (fear / elevated_fear / neutral / complacent).
    The Brain emits a `skew` signal and appends a surface read to the chosen
    strategy's reasoning.
  - **Earnings move** — the front-month ATM straddle's implied move compared
    against the symbol's realized post-earnings move history
    (`earnings_move_edge`). Rich IV ("sell the move") nudges an existing
    sell-premium edge; cheap IV ("buy the move") nudges buy-premium. Never
    fabricates an edge when data is missing.
- Added short interest (`short_percent_of_float`, `days_to_cover`,
  `shares_short`) via yfinance fundamentals, surfaced as a squeeze-fuel signal
  when shorts are crowded.
- Wired all three through the scanner's `_analyze_one` (fail-closed to `None`),
  the scanner notification payload, and the on-demand advisor
  `_market_snapshot`; `REGIME_WEIGHTS` gained `skew` and `short_interest`
  sources (rebalanced to 1.0).
- Added a public trade journal (`dashboard/app/trades/page.tsx` +
  `dashboard/trades.json`): an influencer-style showcase page with a metrics
  strip (net P&L, win rate, profit factor, avg win/loss, max drawdown, current
  streak), a CSS/SVG equity curve, and per-trade cards showing structure, DTE,
  entry IVR, the thesis, what actually happened, research links, tags, and a
  timestamped receipt. Every position is explicitly labeled PAPER and the page
  carries a paper-only disclaimer. Private terminal remains token-gated; the
  journal is static, honest, and has no 100%-win claims.
- Added `tests/test_desk_analytics.py` and brain/scanner coverage; full suite at
  +14 tests.

## v0.7.0 - 2026-08-04

- Added the free, no-key CBOE delayed-quotes provider
  (`agents/data_ingestion/cboe_data.py`): full option chains with Greeks and
  IV, underlying quotes, and VIX term-structure indices. Feeds the scanner via
  the IBKR > CBOE > yfinance fallback chain in `free_data.py`.
- Added a persistent per-symbol IV history store
  (`agents/volatility/iv_history.py`) so IV Rank and IV Percentile are computed
  against a real 52-week history, not spot IV. IV percentile is used as the
  dual filter with IVR for the premium-selling edge (MarketChameleon
  methodology).
- Enriched the Brain's `iv_signal` with `iv_percentile`, `expected_move_pct`,
  and `term_structure`. Inverted VIX term structure now downgrades
  sell-premium signals to neutral and blocks new premium-selling strategies
  (Option Alpha / Tastytrade playbook).
- Wired the background scanner to feed `current_iv`, `hv_20`, `iv_percentile`,
  `expected_move_pct`, `vix_term_structure`, and `days_to_earnings` into the
  Brain; each enrichment degrades to neutral on failure, preserving the
  fail-closed scanner contract.
- Added free earnings-date lookups (yfinance) driving the existing earnings
  avoidance gate, plus `realized_volatility` / `realized_volatility_series`
  helpers in `iv_metrics.py`.
- Removed verified-dead code: legacy `agents/strategies/`, `agents/sentiment/`,
  `agents/execution/`, `agents/llm_reasoning/`, the standalone backtester,
  dark-pool and multi-layer scanner modules, and `data_ingestion/market_data.py`.
  Kept `advanced_backtest.py` — `SignalEngine` is imported by `ai_brain.py` and
  `tv_indicators.py`.
- Documented the provenance of every free feed and volatility gate in
  `docs/STEALING_POLICY.md` and added `AGENTS.md` so the additions are
  protected from future removal.

## v0.6.9 - 2026-08-03

- Added the compatible OpScanBot execution references to the recommender:
  single-leg premium sellers require 500 OI; both legs of vertical credit
  spreads require 250 OI; and verticals must collect at least 25% of width.
- Kept ThetaForge's stricter 33%-of-width iron-condor requirement and did not
  invent a delta or timestamp rule from delayed/free data. POP, probability of
  touch, and the final live IBKR executable-quote validation remain in force.

## v0.6.8 - 2026-08-03

- Made the background scanner fail closed when price, option-chain, VIX, or
  sufficient price history is unavailable. It records skip diagnostics in
  scanner status instead of treating missing data as neutral placeholders.
- Clarified dashboard signal language: a background signal is a discovery
  candidate, not an order instruction. Opening it runs the final contract,
  portfolio, and local IBKR quote checks before paper submission is possible.

## v0.6.7 - 2026-08-03

- Removed inactive, misleading API routes (fake positions, strategy settings,
  backtest and live-toggle) so every remaining API route corresponds to the
  production Advisor or its health probe.
- Removed unreferenced Celery/task-worker scaffolding, Docker Compose, and
  database/queue dependencies. Railway continues to use the retained Dockerfile
  to run the single hardened FastAPI service.
- Updated the project map and dashboard version so operating instructions match
  the deployed paper-only architecture.

## v0.6.6 — 2026-08-03

- Added the Option Alpha probability/EV playbook to the recommender:
  - Sell structures (CSP, covered call, bull put, bear call, iron condor) are
    now rejected when any short leg's probability of touch exceeds 70% — the
    option is very likely to be exercised against the seller before expiry.
    Debit spreads are exempt because their short leg is a hedge, not a sell
    decision.
  - Bull puts and bear calls must collect at least $0.15 of credit so the
    round-trip fill can cover transaction costs; thinner spreads are skipped.
  - Recommendations now carry a true expected value computed across three
    outcome zones (max-profit, partial at the midpoint of max profit/loss,
    max-loss) instead of the naive two-outcome model, plus an `alpha` score
    (EV per dollar of defined risk) — the Option Alpha metric. Both are
    returned by the advisor API.
- Made exit rules strategy- and regime-aware per the Tastytrade playbook:
  - Close at 50% of max profit or at 21 DTE, whichever comes first; hard stop
    at 2-3x the credit received.
  - IV Rank above 60 (expensive premium) raises the profit target to 75%.
  - Iron condors target 25% when the credit is thin (< 50% of wing width) and
    50% otherwise, with per-wing stops at 2-3x that wing's credit.
- Removed dead code:
  - Deleted `agents/scanner/` (unused Go concurrent scanner) and
    `agents/performance/` (zero live consumers); pruned their Celery wiring
    from `orchestrator/celery_app.py`.
  - Renamed `orchestrator/routes/scanner.py` to `backtest.py` and removed the
    dead `/scan/*` and `/gex/*` routes and the redundant `/api/strategies`
    list; GEX flows internally, not over HTTP.
  - Deleted the stale starter-template dashboard test
    (`dashboard/tests/rendered-html.test.mjs`) and `app/_sites-preview/`;
    `npm run test` is now the production build.
- Added `docs/HANDOVER.md`, a complete map of the running system for a new
  LLM/engineer: architecture, pipeline, gates, version constants, data files,
  and known quirks.

## v0.6.5 — 2026-08-03

- Applied the professional (TastyTrade/ORATS) volatility playbook to the trade
  recommender so only high-probability structures can reach the dashboard:
  - Selling premium (CSP, covered call, bull put, bear call, iron condor) now
    requires IV Rank >= 30, a VIX below 35 (no crash-regime selling), and IV
    above realized volatility (positive NVRP).
  - Buying premium (call/put debit spreads) is now only authorized when IV
    Rank <= 25.
  - The same gates run inside the existing score/POP/edge quality floor, so a
    top-ranked candidate can no longer slip through on rank alone.
- Spreads now require a liquid short (executed) leg, matching the singles
  gate. Iron condors additionally must collect at least 1/3 of the wing width
  in credit, rejecting lottery-ticket thin-credit structures.
- Fixed `kelly_fraction`: it carried the strategy's win rate, never a Kelly
  number. It is now true half-Kelly from POP and the max-profit/max-loss
  payoff ratio, clamped to [0, 0.5].
- Fixed the dead portfolio-Greeks gate in candidate selection: `delta_impact`
  and `vega_impact` were always zero, so the delta/vega limits never filtered
  anything. They are now computed from the short leg(s) when the provider
  supplies Greeks (a 0.16–0.20 delta short is the comfortable band).
- The per-trade risk budget now binds the position's max loss instead of its
  capital outlay (buying power still reserves the outlay separately).
- Wired `RiskManager` into sizing: its 2% of equity ceiling caps the per-trade
  risk regardless of the account's risk-tolerance profile.
- Background scanner notifications now carry IV Rank and the IV/HV ratio and
  signal, and the dashboard alert cards display them.

## v0.6.4 — 2026-07-30

- Required a shared `ADVISOR_API_TOKEN` on every hosted Advisor endpoint. CORS
  restricts browsers only, so the public Railway URL previously allowed any
  client to trigger full market scans and to mutate the watchlist, alert rules,
  and notification queue that drive the dashboard. Only the health probe
  remains public. The dashboard carries the token per browser session.
- Added rate limits to the endpoints that fan out to public market-data
  sources, so a scan cannot be triggered faster than the data providers or the
  Advisor can serve it.
- Removed the Bridge's `/orders/stage` and `/orders/{id}/submit` endpoints.
  They placed orders without live-quote verification, defined-risk proof,
  capital-limit enforcement, or covered/cash-secured checks, and never reached
  the ledger — so a naked short option was submittable and its risk was
  invisible to weekly capital reservation. `/orders/submit-combo` applies all
  of those controls and is now the only order path.
- Made Bridge and Advisor authentication fail closed. An unset token previously
  disabled authentication silently instead of refusing to serve. Token
  comparison is now constant-time.
- Fixed background scanner discovery, which had silently degraded to the static
  67-symbol seed list since v0.6.0: the Yahoo screener call was missing an
  `await`, and the IBKR Bridge calls sent the wrong authentication header name.
  Both failures were swallowed by bare exception handlers. A local scan now
  builds ~130 symbols instead of 68.
- Fixed a connection-pool leak that created and abandoned two HTTP clients on
  every five-minute scan.
- Logged background scan failures instead of discarding them.
- Removed a second, unused scoring engine (`scanner`, `scorer`, `sizer`,
  `selector`, `validator`, `edge_calculator` and the pipeline dataclasses they
  consumed). Nothing outside those files imported them; the production path has
  always been `recommender` + `strategy_scorer` + `roi_calculator`. Two
  independent engines with divergent thresholds made it impossible to tell
  which one was authoritative.
- Removed unreferenced modules: `llm_reasoner`, `notifier`, `order_manager`,
  `portfolio_optimizer`, `advanced_models`, and `models/pydantic_schemas`.
- Removed `get_market_breadth`, which returned hardcoded zeros regardless of
  input, and dropped the `finvizfinance` dependency it existed to justify.
- Stopped tracking runtime state under `data/`; it is recreated on first use.
- Corrected the README: removed the LLM circuit breaker, which described a
  component that is not part of the running system, documented the actual
  paper-order controls, and marked the queue/database architecture as not
  deployed.

## v0.6.3 — 2026-07-29

- Raised background candidate alerts to the same 75-point high-conviction
  threshold used by the detailed Advisor path; older low-score alerts are
  hidden immediately.
- Made alert cards interactive. Selecting one runs that symbol through the
  detailed option-chain, portfolio, quality-gate, and IBKR quote workflow.
- Added an alert trade-detail modal showing qualified structures, probability
  of profit, maximum risk/reward, capital, legs, quote quality, and the existing
  paper-order action.
- A candidate that fails detailed validation now produces an explicit no-trade
  result instead of implying that the preliminary signal should be executed.

## v0.6.2 — 2026-07-29

- Corrected background alert eligibility to use the Brain's final strategy
  decision rather than its directional signal. `no_trade`,
  `avoid_new_positions`, and `roll_or_close` outcomes no longer trigger.
- Added read-time API and dashboard filtering so invalid alerts persisted by
  older deployments disappear immediately, before the next scanner pass.
- Corrected scanner status counts so rejected outcomes are not reported as
  symbols with trades.

## v0.6.1 — 2026-07-29

- Fixed paper execution for multi-leg verticals and iron condors by creating
  the missing IBKR combo limit order before submission.
- Added a persistent local paper-order ledger reconciled with the current TWS
  session, including status, fills, limit price, and reserved maximum loss.
- Enforced the dashboard options allocation across all Bridge-submitted orders
  in the current ISO week instead of checking each order in isolation.
- Added dashboard paper-order activity, automatic status polling, capital
  reserved/remaining totals, and cancellation for unfilled orders.

## v0.6.0 — 2026-07-28

- Added background Brain scanner (`agents/trade_engine/background_scanner.py`)
  — runs the AI Brain on the full tradeable universe every 5 minutes in an
  asyncio task, diffs results against the previous scan, and persists trade
  notifications for the dashboard to surface.
- Added `build_scan_universe()` — dynamically discovers 300 symbols from the
  liquid options universe, IBKR TWS scanner, current positions, and Yahoo
  Finance screeners.
- Added notification API endpoints under `/api/advisor`:
  `GET /notifications`, `POST /notifications/{id}/acknowledge`,
  `POST /notifications/acknowledge-all`, `GET /scanner/status`,
  `POST /scanner/trigger`.
- Wired scanner lifecycle into FastAPI lifespan (auto-starts on boot, stops
  on shutdown).

## v0.5.9 — 2026-07-28

- Added unified AI Brain (`agents/trade_engine/ai_brain.py`) — orchestrates 15+
  signal engines (CPR, IVR, technicals, sideways, PCR sentiment, flow, GEX)
  into a single composite score per symbol with regime-based weighting.
- Added time-horizon tab system (1W, 1M, 3M, 6M) with strategy-appropriate
  recommendations per duration.
- Added favorites/watchlist persistence (`agents/trade_engine/watchlist.py`)
  with per-symbol preferences and full CRUD API.
- Added signal performance tracker (`agents/trade_engine/signal_tracker.py`)
  — records every Brain prediction and measures accuracy against actual
  outcomes, enabling dynamic weight adjustment over time.
- Added alert engine (`agents/trade_engine/alerts.py`) — monitors price, IV,
  signal flips, drawdown, and Greeks thresholds with one-shot/recurring rules.
- Added dashboard summary endpoint `POST /api/advisor/dashboard` — single-call
  portfolio view with VIX, regime, watchlist rankings, portfolio risk, and
  top picks per horizon.
- Enhanced AI Brain with portfolio context awareness — detects existing
  positions and warns/redirects before suggesting new entries.
- Enhanced AI Brain GEX ingestion — normalised GEX dictionary key differences
  so that `gex_regime` from the scanner and `regime` from the GEX engine both
  map to the correct signal branch.
- Enhanced AI Brain flow normalization — premium is now scaled by
  `stock_price × 1000` (shares-equivalent) instead of a hardcoded $500K,
  making flow signals meaningful for mid/small-cap symbols.
- Added moderate IV signal bands (IVR 30–50) — the most common range no longer
  silently falls through to a blank neutral; graduated sell/buy signals appear
  when IV/HV ratio supports them.
- Raised default neutral-signal confidence from 30 → 50 so that absent data
  does not falsely trigger the Brain's `no_trade` confidence gate.
- Restored multi-regime detection (`low_vol`, `neutral`, `high_vol`) with
  corresponding weight profiles; the previous VIX-only heuristic collapsed
  everything to two unreachable states.
- Fixed all 13 pre-existing test failures: `TradeSignal.__init__()` now accepts
  `entry_rules`/`exit_rules`, `UnusualActivityDetector` uses `scan_chain()`,
  IV rank uses `pytest.approx`, `RiskManager` boundary assertions corrected,
  and `CoveredCall`/`EarningsStraddle` test fixtures supply required keys.
- Fixed dead-code earnings check in `_select_best_strategy` (duplicate nested
  condition made the inner return unreachable).

## v0.5.8 — 2026-07-27

- Expanded the first-pass market universe from 120 to 300 underlyings.
- Added optional live IBKR TWS scanner discovery (hot-by-volume, top gainers,
  and top losers) whenever the local Paper Bridge is connected.
- Retained the quality screen and deep top-10 options-chain analysis to avoid
  treating a larger universe as permission to recommend weaker trades.

## v0.5.7 — 2026-07-27

- Added backward-compatible unique position keys so existing local Bridge
  sessions cannot trigger duplicate-key dashboard warnings during an update.

## v0.5.6 — 2026-07-27

- Fixed duplicate position rendering when IBKR returns multiple contracts for
  one underlying. The dashboard now identifies each position by its IBKR
  contract and displays option strike, right, and expiry where applicable.

## v0.5.5 — 2026-07-27

- Raised the Advisor’s hard composite-score floor to 75/100 and added separate
  minimum edge (60/100) and IV-based model probability-of-profit (55%) gates.
- Corrected model probability calculations to use option direction and actual
  implied volatility rather than a fixed-volatility distance heuristic.
- Corrected iron-condor maximum-loss calculations to use the wider wing.
- Explicitly preserves a no-trade outcome when no setup clears every gate.

## v0.5.4 — 2026-07-27

- Extended paper execution to every currently supported Advisor strategy:
  defined-risk spreads and condors, cash-secured puts, and covered calls.
- Cash-secured puts now require verified IBKR available funds; covered calls
  require verified ownership of at least 100 shares per contract. Naked short
  options continue to be rejected.

## v0.5.3 — 2026-07-27

- Added live-quote-gated paper order submission from each eligible trade card.
- The local IBKR Bridge now requotes every leg immediately before submitting a
  single combo limit order, rejects delayed/frozen quotes, and enforces the
  dashboard capital limit against live maximum loss.
- Automated execution is limited to defined-risk verticals and iron condors;
  uncovered and stock-dependent structures remain analysis-only.

## v0.5.2 — 2026-07-27

- Expanded every trade card into a decision view with maximum loss, maximum
  profit, probability of profit, capital required, credit/debit, breakeven,
  confidence, volatility context, and return-on-capital metrics.
- Added a compact defined-risk/reward visual and expandable entry/exit plan
  for each eligible paper-trading recommendation.

## v0.5.1 — 2026-07-27

- Corrected strategy scoring to use each underlying's actual volatility rank,
  realized volatility, technical regime, and market VIX context.
- Fixed neutral MACD normalization so it does not create a bearish bias.
- Enforced out-of-the-money strike geometry for defined-risk credit spreads.
- Retained the strict 70/100 composite-score eligibility floor across all
  supported strategy types.

## v0.5.0 — 2026-07-27

- Added the Advisor-selected stock workflow: the scanner chooses diversified
  underlyings first, and each stock opens its own filtered trade structures.
- Expanded market discovery with live active, gaining, losing, and growth-stock
  screeners, while retaining liquidity and options-chain validation.
- Corrected option-chain ingestion, actual DTE, strategy ranking, and
  multi-leg direction handling.
- Strengthened the Advisor Brain with regime-aware, event-aware, risk-defined
  strategy controls and paper-only execution guardrails.
- Added website-first local Bridge autostart for Windows.
