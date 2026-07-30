# ThetaForge Changelog

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
