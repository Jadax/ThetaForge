# ThetaForge Changelog

## v1.11.0 - 2026-08-15

The self-learning feedback loop is now real: the Brain has always *declared*
`signal_accuracy` and `dynamic_weights` on its output (and the advisor exposed a
trailing `/signals/performance` summary), but nothing recorded predictions from
the live scan, nothing scored outcomes automatically, and the composite score
ignored learned accuracy. This release closes the loop.

- `signal_tracker.py`: predictions are now scored per source — a source is
  credited only when its OWN directional read agrees with the realized move
  (neutral reads are data-absence and are neither credited nor faulted).
  File read-modify-write is serialized under a module lock so concurrent scan
  workers can't lose rows.
- `ai_brain.py`: `analyze()` accepts `record_feedback` and, when actionable
  (a real strategy, never a `no_trade` fail-closed verdict), persists a
  prediction snapshot. Learned per-source hit rates nudge the regime weights:
  a source earns a nudge only after `MIN_DYNAMIC_SAMPLES` outcomes, the nudge
  is `(accuracy - 50%) / 100 × trust` clamped to ±0.35, trust ramps to full
  over 60 outcomes, and the blended weights renormalize to the base total.
  The composite score uses the blended weights and the effective weights plus
  the accuracy table are surfaced on `BrainOutput.signal_accuracy` /
  `BrainOutput.dynamic_weights`. `record_outcome()` is exposed for the scan
  path; the whole feedback layer is advisory and fails closed to base weights.
- `background_scanner.py`: the scan now records actionable analyses
  (`record_feedback=True`) and scores any due predictions for a symbol with the
  price it already fetched — outcome evaluation costs no extra network calls.
- VERSION → v1.11.0. Tests: 313 pass (was 306; +7 feedback-loop tests, +1
  scanner outcome-feed assertion).

## v1.10.0 - 2026-08-15

The live brain is now fed: the background scan previously ran with three of the
AIBrain's regime-weight buckets (flow, put/call sentiment, dealer GEX) inert,
because only the manual `/api/advisor/brain/analyze` path supplied those
inputs. This release wires them into the scan path so the composite score
actually uses the weights the architecture declares.

- New `agents/volatility/pcr_history.py`: append-only per-symbol put/call-ratio
  store (daily idempotent snapshots, `MIN_SAMPLES` 20), mirroring the
  IVHistoryStore contract. Symbol-level PCR is only meaningful relative to its
  own recent distribution, so the sentiment engine now reads a z-score over
  accumulated history instead of a bare absolute ratio.
- `background_scanner.py`: `_flow_data()` (UnusualActivityDetector over the
  free chain, aggregated into the exact shape the Brain consumes),
  `_pcr_read()` (put/call volume ratio, OI fallback, persisted daily), and
  `_gex_data()` (full-chain dealer GEX regime). All three fail closed to None,
  and the Brain treats a missing read as neutral — a broken source can never
  fabricate a signal. The scan payload and results rows now carry `flow_bias`,
  `pcr_signal`, and `gex_regime`.
- `gex_engine.py`: prefer the provider-computed `dte` field when the chain row
  carries it (exact, and avoids a strptime per option).
- Dashboard (`dashboard/app/page.tsx` v1.10.0): VERSION bump only.
- Tests: 306 pass (was 299; +5 live-brain feed tests, +1 date-robust desk
  analytics fix). VERSION → v1.10.0.

## v1.9.0 - 2026-08-13

Second engine shipped: an autonomous momentum-equity (stock/ETF) long trader
alongside the options engine. Same paper-only, free-data, fail-closed
principles: the Bridge is still the only order path and its ledger stays the
single source of truth.

- New `agents/equity_trader/` module: `equity_signals.py` (trend, 6m momentum,
  RS vs SPY), `equity_brain.py` (fail-closed gates: risk_off regime, macro
  <=2d, earnings <=3d, SMA200/SMA50 trend, momentum, ADX>=20, RSI<=80, RS
  >=-5%; `BUY_SCORE_FLOOR` 62), `equity_universe.py` (free active-universe
  discovery), `equity_scanner.py` (5-min background scan, `SCAN_CONCURRENCY=5`,
  market-hours only, notification floor 70), `equity_recommender.py` (1% risk,
  2x ATR stop, 2R target, 30% notional cap, `MAX_CORRELATED_EQUITY_POSITIONS=3`
  via `SYMBOL_SECTOR`), `equity_manager.py` (stop / pre-macro / pre-earnings /
  chandelier trail / 2R profit / 60d time rules, running-high ratchet).
- Bridge (`bridge/main.py` v0.4.0): `POST /orders/stock` (long-only, live-quote
  ask required, stop-defined risk `shares * (ask - stop)` reserved in the
  weekly ledger), `POST /orders/close-stock`, and `POST
  /orders/{ledger_id}/position-meta` (metadata-only, upward-only trailing-stop
  anchor ratchet).
- Advisor (`orchestrator/routes/advisor.py`): `/api/advisor/equity/notifications`,
  `/equity/recommend`, `/equity/positions/management`, `/equity/scanner/status`.
  `/equity/recommend` always returns the payload (`entry_price`, `rationale`,
  `gate_reason`, `score`, `read`, `trend`, `rsi_14`, `adx_14`, `atr_14`); the
  `recommendations` list only ever carries ungated names.
- VM automation: `vm_auto_executor.py` runs the equity notification cycle
  (fetch -> recommend -> submit -> acknowledge); `vm_auto_manager.py` requests
  equity management and submits closes (`AUTO_CLOSE_ENABLED` gated,
  `review_tested` still never auto-submitted).
- Market data: the read-only VM proxy (`vm_market_data_service.py`) gained
  `GET /stock/{symbol}` and `GET /stock-history/{symbol}`; `free_data.py`
  routes stock price + daily history IBKR-first (proxy, then yfinance).
- Journal: two-engine site with All/Options/Stocks filters, engine chips, and
  equity cards (shares / entry / stop / 2R target); `sync_journal.py` folds
  equity closes with realized P&L and writes equity exit notes
  (`close_stop`/`close_trail`/`close_profit`/...).
- Dashboard (`dashboard/app/page.tsx` v1.9.0): equity notifications, scanner
  status, momentum-long recommendation cards, and an equity position
  management panel.
- Docs: `AGENTS.md` module map + changelog. VERSION → v1.9.0.
- Tests: 299 pass (was 261). New equity brain/recommender/manager
  (`test_equity_trader.py`), advisor endpoints (`test_equity_advisor.py`),
  equity ledger reservation (`test_paper_bridge.py`), equity journal
  (`test_sync_journal.py`), and equity CLI (`test_add_trade_cli.py`) tests.

## v1.8.0 - 2026-08-13

Brain hardening: macro-event awareness, correlation concentration, and an
empirical check that realized outcomes (not just model POP) authorize selling
premium. All four changes run through the existing gates — no second decision
or order path.

- New `agents/trade_engine/macro_calendar.py`: offline, free macro calendar
  (FOMC decision days 2026+2027, CPI 2026, NFP by the standing first-Friday
  rule). The Brain refuses new positions inside a 4-day blackout before a
  print (`macro_proximity` reason, tallied by the scanner); the trade manager
  exits open short vega with a new `close_pre_macro` rule (fired through
  `POST /api/advisor/positions/management` and auto-submitted by the VM
  auto-manager; `sync_journal` writes a matching exit note). 2027 CPI is not
  yet scheduled by BLS and is deliberately absent — missing schedule data
  fails open, it never fabricates a veto.
- Recommender correlation cap: `MAX_CORRELATED_POSITIONS` (previously dead
  code) now binds. A curated `SYMBOL_SECTOR` map over the liquid-options
  universe limits the book to 3 positions per sector; unknown symbols are
  uncorrelated singletons, so the cap never assumes a correlation it cannot
  source.
- Recommender empirical gate: a short-premium strategy whose realized record on
  the public journal is losing (win rate < 50% or non-positive expectancy over
  >= 10 closed trades) is refused even when model POP clears every other gate.
  TTL-cached, and it fails open below the sample floor and on any fetch error.
- VIX ceiling aligned: `MAX_VIX_SELL` 35 → 30 so the recommender and the
  Brain's extreme-VIX veto agree on the same crash-regime line.
- Docs: `AGENTS.md` module map + changelog. VERSION → v1.8.0.
- Tests: 261 pass (was 230). New calendar, brain-veto, pre-macro exit,
  correlation-cap, empirical-gate, and auto-manager tests.

## v1.7.0 - 2026-08-13

Closed the loop on the exit framework: the management engine can now *execute*
its own closes through the same paper-only Bridge as entries, and the public
journal shows every exit as a lifecycle event on the parent trade.

- Bridge (`bridge/main.py` v0.3.0): new `POST /orders/close-combo`. The caller
  names only `ledger_id` + `reason`; the Bridge mirrors the entry's own ledger
  record (reversing each leg action) so a mismatched structure is impossible,
  re-verifies live IBKR bid/ask, proves structure continuity with the
  already-proven defined-risk entry, and refuses fills that would pay the
  position. A close never reserves new capital — the parent's weekly
  reservation is released when its `closed_by` is set.
- Journal lifecycle (`scripts/sync_journal.py`): closing ledger records
  (carrying `close_of`) are folded into their parent entry as a `closed`
  status with an auto-written exit note, the exit receipt (`close_order`
  block), and realized P&L (`net_pnl`, `net_pnl_pct`). A close is never a
  phantom new "open" trade.
- New `deployment/vm_auto_manager.py`: the autonomous exit loop for the VM —
  reads open short-premium positions from the Bridge ledger, asks the
  Advisor's `POST /api/advisor/positions/management` (the only exit-decision
  place) for the action on each, and optionally submits the recommended closes
  via `POST /orders/close-combo` (the only exit-execution place). **Advisory by
  default; set `AUTO_CLOSE_ENABLED=true` to let it submit.** `review_tested`
  is surfaced for a human and never auto-submitted. Triggers the journal sync +
  publish after any close.
- Advisor: `POST /api/advisor/positions/management` now refreshes
  `days_to_earnings` from free data when the caller omits it (same fail-open
  enrichment as spot/short-leg), so the pre-earnings exit fires without extra
  VM-side plumbing.
- Scanner: the v1.6.0 `trend_mismatch` / `laggard` no-trade reasons were being
  tallied as generic `other` by `_no_trade_reason_code`; the mapping now
  classifies them so scan diagnostics stay honest.
- Docs: `AUTONOMOUS_TRADING.md` (auto-manager service, exit flow, safety rail),
  `AGENTS.md` (module map), `SIGNAL_POLICY.md`. VERSION → v1.7.0.
- Tests: 230 pass (was 220). New journal lifecycle collapse tests, Bridge
  close-combo helper/reservation tests, auto-manager decision-logic tests.

## v1.6.0 - 2026-08-13

High-win-rate upgrade to the AI brain: research-backed entry *context* gates
and an open-position management framework, distilled from the top-trader
playbook (tastytrade DTE/exit studies, Cboe expected-move math, IBD relative
strength, thetagang premium-selling hygiene) and the 11 new reference sources
cataloged in `docs/SOURCES.md`.

- New `agents/trade_engine/high_winrate.py`: pure, unit-tested entry vetoes
  applied after the generic quality gates (POP, IVR, credit-to-width, POT):
  trend alignment (no bull structure into a confirmed downtrend and vice
  versa), a short-strike buffer at/outside the 1-SD expected move (~68% POP
  floor), an entry-DTE band (no new short premium <21 or >60 DTE; debit floor
  at 14 DTE), an earnings blackout for new short premium, and the IBD "L" rule
  — no directional short premium on 6-month market laggards (the one soft
  gate: missing RS data disables it, never fabricates a reject).
- Brain (`ai_brain.py`): `_select_best_strategy` now enforces trend alignment
  on the bull-put/bear-call credit and debit-spread branches (new
  `trend_mismatch` reason) and the relative-strength gate on directional
  premium (`laggard` reason). `relative_strength` is surfaced on `BrainOutput`.
- Scanner (`background_scanner.py`): `_analyze_one` computes each symbol's
  6-month return minus SPY's (`_spy_126_return`, fetched once per scan and
  shared across the fan-out) and feeds it to the Brain and scan results.
- Recommender (`recommender.py`): new step 4c applies the same high-win-rate
  context gates to every scored candidate (`_passes_high_winrate_gate`) so
  the `/recommend` path and the auto-executor path agree.
- New `agents/trade_engine/trade_manager.py` + `POST
  /api/advisor/positions/management`: the exit framework for open positions —
  take profit at 50% of max credit, close/roll inside the 21-DTE gamma window,
  stop at 2× the credit, close before earnings, flag tested short strikes; plus
  a portfolio plan (position cap, per-symbol capital slice, trailing-drawdown
  circuit breaker, weekly capital limit). It only *recommends* actions — order
  submission stays exclusively in the Bridge, so there is still exactly one
  execution path.
- Dashboard: "Position management · theta exits" panel (JSON position input,
  per-position action + urgency, portfolio green/blocked banner). VERSION →
  v1.6.0.
- `docs/SIGNAL_POLICY.md` and `docs/SOURCES.md` updated with the new gates, the
  soft-vs-hard input split, and the 11 new sources (gorillatrades, stockcircle,
  stratxai, clarkstreetvalue, stratosphere, macroaxis, stocknear, trefis,
  aiolux, altindex, investors.com) plus the tastytrade/Cboe research.
- Tests: 205 pass (was 176). New `tests/test_high_winrate.py` and
  `tests/test_trade_manager.py`; brain trend/RS gate tests; recommender step-4c
  tests.
- Kept invariants: no second scoring or order path; free feeds only; fail-closed
  (soft only on relative-strength, never on hard safety inputs).

## v1.5.0 - 2026-08-13

Added the general-trader side of the platform — a read-only cross-asset market
map for stock, ETF, and bond positions, alongside the existing options path.

- New `agents/general_trader/market_overview.py`: builds the daily tape from
  the existing free data stack (yfinance through `FreeDataProvider`) — index
  levels and 1d/5d moves, bond yields (13-wk T-bill, 5/10/30-yr) and bond ETFs,
  commodities, sector performance, a yield-curve shape read, and a coarse
  risk-on/risk-off tilt that requires equity *and* credit to agree.
- Per-symbol stock/ETF reads reuse the existing `SignalEngine`
  (RSI-14, ADX, MACD), SMA 50/200 trend, 52-week-range position, 20-day
  realized volatility, and a volume-ratio — classified as bullish/bearish/
  neutral. Fail-closed: any asset with missing/too-short history is dropped
  with its label, never a placeholder.
- New `POST /api/advisor/markets` route (scan rate-limited, router-level auth):
  body `{"symbols": []}` returns `{overview, symbols}`. Verified live end to
  end (200 with token, 401 without).
- Dashboard: new "Market map · stocks · bonds · sectors" panel with risk-tilt
  banner, yield-curve read, index/bond/commodity/sector strips, and per-symbol
  reads.
- `docs/SOURCES.md` catalogs the operator's reference sources (thetagang,
  optionistics, optiondash, opscanbot, quantcha OSE, Unusual Whales, OIC
  trending volume, optionsprofitcalculator, maxfort86/wsb, Quiver) with the
  signal each informs and a free-data requirement note — none may be pulled in
  as a paid API.
- Tests: 176 pass (was 169). `tests/test_general_trader.py` covers overview
  construction, fail-closed drops, risk-tilt agreement, and per-symbol reads.
- Kept invariants: read-only (no order path), free feeds only, fail-closed.

## v1.4.0 - 2026-08-13

Rebalanced the Brain's strategy gates after a two-week live-testing dry spell
in which zero trades fired. Root cause was traced to the signal-agreement
(confidence) gate: it averaged in every *neutral* read (data-absence, strength
~0) as agreement, dragging every symbol below the floor regardless of any real
directional/vol edge — the exact failure the user surfaced. Confirmed live
before the fix: SPY 43.8, NVDA 30.0, XLF 46.2 confidence, all `no_trade`.

- Confidence is now the average of **informative signals only** (|strength| ≥
  0.05); no informative signal at all → 35.0, fail-closed below the floor.
  Strategy-selection floor eased 55 → 45 (`MIN_STRATEGY_CONFIDENCE`).
- IV/HV "sell premium" threshold aligned to FlashAlpha's published IV/RV >
  1.15 sell checklist (was 1.25, stricter than the recommender's own 1.0
  execution gate).
- Directional credit spreads now trigger at IVR ≥ 40 (was 50); iron condor
  VIX band widened to 18–28 (was 15–25).
- Per-symbol trailing VRP (IV−RV premium) z-score added to `IVHistoryStore`
  (`vrp_zscore`, `iv_change_5d`); the scanner computes a `vol_risk_premium`
  payload from the same store and feeds it to the Brain as a scored
  refinement of an existing sell-premium edge (never a standalone gate).
- Scanner now passes the symbol's own 52-week IV bounds from `IVHistoryStore`
  (once ≥10 samples exist) instead of the Brain's fixed 0.40/0.12 default
  band; `_atm_iv` hardened to a delta-50 straddle → call/put-IV parity →
  front-expiry median (never the whole-chain mean, which distorted
  `current_iv` badly on wide chains, e.g. XLF 0.859).
- No-trade rows now persist their full analysis payload plus a stable
  `no_trade_reason` code; scan state tallies `no_trade_reasons` so the funnel
  answers *why* nothing traded. Live scan after the change: 130 inputs, 125
  analyzed, 19 actionable symbols (bull/bear credit spreads, debit spreads)
  across a diversified set — previously 0.
- `wheel_candidates` gallery relaxed to IVR ≥ 35 and now also matches Brain
  credit-spread strategies (its CSP/CC-only set was unreachable: the Brain
  emits `bull_put_credit`, not `cash_secured_put`).
- Kept intentional invariants: inverted VIX term structure still = `no_trade`
  for premium selling; fail-closed on missing price/chain/VIX/history.

## v1.3.1 - 2026-08-12

Moved the public journal to a custom domain, `https://journal.astraiva.app/`,
off the `jadax.github.io` URL. `astraiva.app`'s root domain was already on
GitHub Pages (a different repo, the owner's company site), so this reuses
infrastructure already proven to work rather than introducing anything new —
just a `CNAME` file on the `gh-pages` branch plus one DNS record at the
existing DNS provider (Spaceship), scoped to the `journal` subdomain only so
it can't affect the root domain's existing site.

- Added `CNAME` (containing `journal.astraiva.app`) to the `gh-pages` branch
  root.
- Updated every genuine journal-link reference (`AGENTS.md`,
  `docs/HANDOVER.md`, `docs/SIGNAL_POLICY.md`, the dashboard's "Public
  journal" link, `deployment/journal_sync_push.sh`'s comments/output) to the
  new domain. Left the CORS origin allowlists (`.env.example`,
  `bridge/main.py`, `orchestrator/main.py`, `render.yaml`) and past
  `CHANGELOG.md` entries untouched — the former are unrelated Advisor/Bridge
  config the journal never calls, the latter is historical record.
- Confirmed `journal_sync_push.sh`'s existing `cp` step only copies specific
  named files into the `gh-pages` checkout and never wipes the directory, so
  the new `CNAME` file survives future automated journal publishes
  untouched — no script logic change needed there.

## v1.3.0 - 2026-08-12

Added a fully autonomous paper-trading pipeline running on an always-on
Oracle Cloud VM, so the system can run every day the market is open without
depending on a personal computer being on. This is a genuine capability
change, not a config tweak — the system now places paper orders without a
human reviewing each one, gated behind explicit confirmation before it was
built (see the corresponding chat decision), and adds zero new trade-safety
logic: it's a headless caller of the exact same quality gates and Bridge
verification that already existed.

- Added `deployment/vm_auto_executor.py`: polls the Advisor's already-gated
  notifications, fetches a fully-specified structure per symbol, and submits
  through the local Bridge — which independently re-verifies live quotes,
  defined-risk, and the weekly capital-limit ledger regardless of caller.
- Added `deployment/market_hours_supervisor.sh` + a systemd timer: starts
  IB Gateway, the Bridge, and the executor at market open and stops them at
  close, driven by the same real NYSE-calendar `market_open` status the
  Advisor already computes — one source of truth, not a second calendar.
- Added `deployment/journal_sync_push.sh`: autonomously placed trades are
  synced into the public journal and published after each cycle with fills.
  Fixed a real, previously-undocumented gap in the existing manual workflow
  while building this — GitHub Pages serves from the `gh-pages` branch root,
  not `main`, and nothing synced `journal/` on `main` into it; the manual
  "push and it redeploys" instructions in `docs/HANDOVER.md` were incorrect
  even before this change.
- Added `docs/AUTONOMOUS_TRADING.md`: full runbook, including several
  undocumented setup gotchas hit while building this (IBC's `JAVA_PATH`
  must point at the JRE's `bin/` directory, not its root; Ubuntu's Minimal
  image is missing X11 libraries IB Gateway's Swing UI needs even under
  Xvfb headless).
- Lowered `SCAN_CONCURRENCY` from 20 to 5 (`background_scanner.py`) after
  confirming live via Render's logs that 20-way fan-out got CBOE-429'd on
  nearly every request from Render's outbound IP and appeared to starve
  other concurrent Advisor requests into 502s for the scan's duration — not
  reproducible from a residential IP, so this had to be diagnosed live
  rather than locally.
- Declined to remove the paper-only lock in `bridge/main.py` when asked, and
  added an explicit `AGENTS.md` guardrail against it: that lock is the
  project's core safety invariant, and loosening it "for future convenience"
  would leave unattended, autonomous code with a standing path to real
  trades the moment live credentials were ever entered, with no additional
  review step. Going live should be a deliberate, separately-reviewed
  decision made at the time it's actually wanted.
- All 155 tests pass; verified live end-to-end on the actual VM (not just
  locally): IBC auto-login against the real IBKR paper session, Bridge
  connection, live position retrieval, and the executor's Advisor polling
  loop.

## v1.2.4 - 2026-08-12

Found live, via Render's logs, not guessed: `SCAN_CONCURRENCY = 20` (added in
v1.2.2) was overwhelming CBOE's rate limit specifically from Render's
outbound IP -- nearly every request during a scan came back `429 Too Many
Requests`, and the resulting burst appears to have starved the app's ability
to serve other concurrent `/api/advisor/*` requests for the duration of each
scan (`/health/`, which makes no CBOE calls, kept responding throughout).
The same measurement from a residential IP hit zero 429s, confirming this is
IP-specific and not reproducible locally.

- Lowered `SCAN_CONCURRENCY` from 20 to 5.
- `get_option_chain` already falls back to yfinance on CBOE failure (no
  change needed there) -- the fix is entirely about not bursting past
  Render's effective rate limit in the first place.
- All 155 tests still pass; this only changes a constant.

## v1.2.3 - 2026-08-12

Replaced `is_market_hours()`'s hand-rolled weekday + 9:30-16:00 ET check with
`pandas_market_calendars`' real NYSE calendar. The plain check was wrong on
every market holiday and every half day (day-after-Thanksgiving, Christmas
Eve, etc.), and would have drifted further every year since holidays like
Good Friday move.

- Added `pandas_market_calendars` (free, open-source) to `requirements.txt`.
- Correctly handles observed-date shifts (July 4, 2026 falls on a Saturday,
  so NYSE closes the preceding Friday instead) and half days (Nov 27, 2026
  closes at 1pm ET, not 4pm) — verified against the real calendar, not
  assumed.
- Falls back to the plain weekday/clock check only if the calendar lookup
  itself errors, so a bug in that dependency degrades this rather than
  silently stopping the scanner.
- Per-day cache (checked on every scan-loop tick and status poll) so this
  doesn't re-query the calendar library dozens of times a day for an answer
  that can't have changed.
- Added 6 tests (Christmas, the observed-Friday case, the half-day boundary,
  and the fallback path); full suite at 155 passing.

## v1.2.2 - 2026-08-12

Gated the background scanner's automatic loop to NYSE regular session hours
(9:30-16:00 ET, Mon-Fri). The Advisor already runs continuously on Render
(that's why Render was chosen over Cloud Run — to keep this loop alive
without resetting state), but it was scanning the full universe every 5
minutes regardless of whether the market was open, burning free-tier
compute and producing signals against stale/closed-market data.

- Added `is_market_hours()` (`background_scanner.py`) — timezone-aware via
  `zoneinfo`, so correctness doesn't depend on the host's local clock being
  set to US Eastern. Plain weekday + clock-time check, not a full NYSE
  holiday calendar; a scan that fires on a market holiday can't fabricate a
  signal (every price/chain fetch already fails closed), it just wastes a
  few minutes of compute a handful of times a year.
- `_run_loop` now skips the expensive scan outside market hours, recording a
  lightweight diagnostic (`last_closed_market_check`) rather than a stale
  reset — `last_run`/`scan_diagnostics` from the last real scan are left
  alone so `/scanner/status` reads as "closed", not "broken" or "reset".
- A manual `POST /scanner/trigger` still runs anytime, market hours or not —
  this only gates the automatic loop.
- `get_status()` now reports a live `market_open` flag, computed fresh each
  call rather than trusting a persisted value that could go stale between
  scans.
- Added 6 tests covering the boundary cases and the no-clobber behavior; full
  suite at 151 passing.

## v1.2.1 - 2026-08-11

Fixes found by actually running `deployment/cloudflare_deploy_terminal.ps1`
against a real Cloudflare account for the first time, rather than only
syntax-checking it.

- Fixed `Join-Path $PSScriptRoot ".." "dashboard"`: Windows PowerShell 5.1's
  `Join-Path` only accepts two path segments (`-Path`, `-ChildPath`); the
  three-argument form is a PowerShell 7+ feature and errored immediately on
  the target environment. Chained two calls instead.
- Fixed a wrong assumption in the script's own docstring: `wrangler pages
  deploy` does **not** create the Cloudflare Pages project on first use — it
  errors "Project not found." The script now checks `wrangler pages project
  list` and creates the project only if missing.
- **Corrected a real gap in `docs/HOSTED_TERMINAL.md`**: confirmed live that
  every deployment gets its own unique, independently public preview URL
  (`https://<hash>.thetaforge-terminal.pages.dev`) in addition to the clean
  production URL. The original instructions only covered gating the bare
  hostname in Cloudflare Access, which would have left every past and future
  deployment's preview URL unprotected. Now instructs adding the wildcard
  domain (`*.thetaforge-terminal.pages.dev`) to the Access application, and
  verifying both the production and a preview URL prompt for login.
- Removed `dashboard/dist/` (untracked local leftover from the original
  vinext-starter scaffold's build system, not the `next build` path this app
  actually uses) — wrangler was picking up a stale `wrangler.json` from it
  and emitting a "redirected configuration" warning on every deploy.

## v1.2.0 - 2026-08-11

Added a hosted deployment path for the private terminal, for use from a
computer where `Start-ThetaForge.cmd` can't run. No changes to
`dashboard/app/page.tsx` itself — same app, same tokens, same behavior;
only where it's served from is new.

- Added `deployment/cloudflare_deploy_terminal.ps1`: builds the existing
  static export (`next.config.ts` already has `output: "export"`) and
  deploys it to Cloudflare Pages via `wrangler` (already an unused
  devDependency in `dashboard/package.json` from the original scaffold).
- Added `docs/HOSTED_TERMINAL.md`: the one-time Cloudflare Access setup that
  actually makes a public URL safe to use. Deliberately not an in-app
  password check — the terminal is a static export with no server, so any
  client-side login gate would be fully bypassable (the JS is downloadable
  regardless) and would be fake security. Access authenticates a visitor at
  Cloudflare's edge before the page is served at all, gated to a single
  allowed email address; no application code involved.
- Documented the required manual step: adding the new Cloudflare Pages
  origin to `DASHBOARD_ORIGINS` on the Render Advisor, or every API call
  from the hosted terminal fails CORS.
- Noted the one real limitation: the Paper Bridge is still never hosted
  anywhere and must run beside TWS on the trading computer regardless of
  where the terminal is opened from. Analysis works from anywhere once
  deployed; placing orders from another computer additionally needs
  Tailscale to reach the Bridge, as already documented in
  `docs/PAPER_BRIDGE.md`.
- Added an `AGENTS.md` guardrail against ever reintroducing an app-level
  login as a substitute for Access.

## v1.1.4 - 2026-08-04

Persisted the Advisor and Bridge tokens to `localStorage` instead of
`sessionStorage`. Both were previously cleared on tab close by design, but
the private terminal (`dashboard/app/page.tsx`) is explicitly local-only and
never deployed publicly (`docs/HANDOVER.md`), so re-entering both tokens
every session was pure friction with no real security benefit for a
single-user local machine — `sessionStorage` vs `localStorage` doesn't change
XSS exposure either way, only how long a value survives a shared browser
profile. Added a "Forget saved tokens" button next to the Advisor token field
that clears both.

## v1.1.3 - 2026-08-04

Reverted v1.1.2's Google Cloud Run deployment back to Render. Cloud Run's
free tier only stays free with `min-instances=0`, which meant the app's
continuous background-scan design had to be replaced with an external
Scheduler trigger and accept that `data/*.json` state (IV history,
notifications, watchlist) wouldn't reliably persist between scan cycles.
Render's free tier plus a keepalive ping avoids that tradeoff entirely — the
container stays warm under normal operation, so the app's original
continuous-loop design and persisted IV history work as intended. All tests
remain green (145 passing).

- Restored `render.yaml` and `.github/workflows/keep-advisor-warm.yml` from
  v1.1.1.
- Removed `deployment/gcp_deploy.ps1` and the Cloud Run/Scheduler setup.
- Restored every README.md/AGENTS.md/docs/HANDOVER.md/docs/PAPER_BRIDGE.md
  Render reference and the dashboard's default API URL / error copy.
- **Kept** the background-scanner concurrency fix from v1.1.2
  (`SCAN_CONCURRENCY = 20` in `background_scanner.py`) — it's a genuine
  improvement independent of hosting platform: a full ~130-symbol scan
  completes in ~69 seconds instead of several minutes, measured against live
  data sources, with no increase in skipped/failed symbols. Faster scans mean
  fresher data and less chance of overlapping runs regardless of where this
  is deployed.

## v1.1.2 - 2026-08-04

Superseded v1.1.1's Render deployment with Google Cloud Run — genuinely free,
and doesn't conflict with another Google Cloud project already in use. This
is more than a config swap: Cloud Run's free tier only stays free if the
container scales to zero when idle, which means the app's internal
300-second background-scan loop cannot be relied on to keep running (keeping
one instance always-on would cost ~14× the entire free monthly vCPU-second
budget). All tests remain green (145 passing).

- **Parallelized the background scanner.** `background_scanner.py`'s
  `_analyze_one` calls ran sequentially per symbol; added a bounded
  `asyncio.Semaphore` (`SCAN_CONCURRENCY = 20`). Measured against live data
  sources: a full ~130-symbol scan dropped from several minutes to **~69
  seconds**, with no increase in skipped/failed symbols. This is what makes a
  request-driven trigger interval affordable on the free vCPU-second budget —
  see the comment above `SCAN_CONCURRENCY` and `docs/HANDOVER.md` →
  Deployment Requirements for the full math.
- Added `deployment/gcp_deploy.ps1` — one script that enables the required
  APIs, stores `ADVISOR_API_TOKEN` in Secret Manager (prompted once, masked,
  never written to a file), deploys the Advisor to Cloud Run
  (`min-instances=0`, `max-instances=1`), and creates the Cloud Scheduler job
  that calls the authenticated `/api/advisor/scanner/trigger` every 20
  minutes — replacing the app's own internal timer as the thing that actually
  drives the scan on this host. Idempotent; safe to re-run.
- Removed `render.yaml` and `.github/workflows/keep-advisor-warm.yml`.
- Documented, rather than silently shipped, a real limitation: because the
  container isn't kept warm between scheduler triggers, `data/*.json` state
  (IV history, notifications, watchlist) does not reliably persist across
  scan cycles on this tier. Fixing that properly means moving state off local
  disk (e.g. a Cloud Storage FUSE volume mount, batched to stay inside GCS's
  free operation quota) — scoped out as explicit follow-up work rather than
  done partially. Live Brain analysis is unaffected; IV Rank quality and
  notification continuity are what degrade.
- Swapped every Render/Railway reference to Cloud Run across `README.md`,
  `AGENTS.md`, `docs/HANDOVER.md`, `docs/PAPER_BRIDGE.md`, and the
  dashboard's default API URL / error copy. `DEFAULT_ADVISOR_API` is
  deliberately blank now — Cloud Run assigns the URL at deploy time rather
  than a predictable name-based subdomain — with placeholder text guiding
  where to paste it.

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
