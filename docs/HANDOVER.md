# ThetaForge Handover Guide

This document is written for another LLM (or engineer) picking up the codebase.
It explains what the running system actually is, which files matter, and how a
trade recommendation flows from raw market data to a dashboard notification.

## What ThetaForge Is

ThetaForge is a single-process options trading *decision-support* system:

- A **FastAPI orchestrator** (`orchestrator/`) hosts the API and the background
  **Brain scanner**.
- The **trade engine** (`agents/trade_engine/`) scans a liquid-universe list of
  symbols, evaluates seven option strategies through a stack of filters, and
  emits a recommendation **only when a trade passes every gate**.
- A **paper-only IBKR bridge** (`bridge/`) is the optional live-account connector.
- A **Next.js dashboard** (`dashboard/`) polls the API and shows alert cards.

State is stored in JSON files under `data/`. There is **no database** in the
running system. Celery, docker-compose, and the `tasks.py` modules describe an
older queue-and-database architecture that is **not deployed**; do not assume
anything runs through Celery unless you also check `orchestrator/main.py`.

## Production Path (what runs today)

```
bridge/main.py          -- IBKR paper-order gateway (optional, PAPER_ONLY by default)
orchestrator/main.py    -- FastAPI app entrypoint, mounts all routers
orchestrator/routes/    -- HTTP endpoints (advisor, backtest, health, positions, strategies, toggle_live)
agents/trade_engine/    -- THE engine: brain, recommender, background scanner, alerts, models
agents/data_ingestion/  -- FreeDataProvider (yfinance) + IBKR provider
agents/volatility/      -- IV Rank/Percentile, Greeks, term structure
agents/flow_analysis/   -- GEX engine, unusual-activity detector
agents/technical/       -- RSI, MACD, Bollinger, trend indicators
agents/risk_management/ -- Kelly sizing + portfolio limits
dashboard/app/page.tsx  -- single-page dashboard (VERSION const on line ~113)
```

## File-by-File Responsibilities (agents/trade_engine)

| File | Responsibility |
| --- | --- |
| `models.py` | Dataclasses: `AccountInfo`, `TradeRecommendation`, `AdvisoryOutput`, `OptionContract`, `StrategyLeg`, enums (`StrategyType`, `MarketRegime`, `RiskTolerance`, ...). `TradeRecommendation` carries `expected_value` and `alpha` (v0.6.6). |
| `recommender.py` | `TradeRecommender`. Owns all entry/exit gates and scoring. This is the file you will edit most. |
| `analytics.py` | Probability math: `probability_of_profit` (polynomial-approx normal CDF), `probability_of_touch` (line ~191), `expected_move`. |
| `roi_calculator.py` | `ROICalculator`: POP approximation (`_approx_pop_otm`, line ~251), ROI/annualized/EV/`alpha_score`. Single source of truth for POP. |
| `ai_brain.py` | `AIBrain`: qualitative signal aggregation (trend, RSI, GEX regime, earnings, sentiment) producing regime + score. |
| `background_scanner.py` | Background scanner that iterates `LIQUID_OPTIONS_UNIVERSE`, runs the brain + recommender every 300s, writes notifications. Constants: `NOTIFICATION_SCORE_FLOOR = 75`, `NON_ACTIONABLE_STRATEGIES`. |
| `alerts.py` | `AlertEngine`: `AlertType`, `AlertPriority`, persistence to `data/alerts.json` + `data/alert_history.json`. |
| `signal_tracker.py` | `SignalTracker`: records prediction outcomes, accuracy by source (`data/`.json files). |
| `watchlist.py` | `FavoritesStore`: user favorites. |
| `strategy_scorer.py` | `StrategyScorer`: regime-appropriate strategy weighting (research-grade). |
| `tasks.py` | Celery stubs (NOT part of running system). |

## How a Recommendation Is Made (the pipeline)

1. `background_scanner.scan_once()` iterates the liquid universe. For each
   symbol it loads an option chain via `FreeDataProvider`, computes
   volatility metrics (IV Rank, IV/HV, term structure), and calls the brain.
2. `AIBrain.analyze()` produces a **market regime** (bullish/bearish/neutral/
   high_vol) and a composite signal score.
3. `TradeRecommender.generate_recommendations(...)` runs each of the seven
   strategies (`csp`, `cc`, `bull_put`, `bear_call`, `iron_condor`,
   `call_debit`, `put_debit`) through `_score_*` methods. Each scorer builds a
   candidate dict `{type, symbol, strike(s), credit, dte, nvrp, legs, roi, ...}`.
4. Candidates pass a stack of **gates** (order matters):
   - `_passes_quality_gate` — composite score floor, liquidity (spread/width,
     volume/OI), max DTE.
   - `_passes_volatility_gate` — IV Rank/VIX conditions for sellers vs. buyers
     (Tastytrade/ORATS thresholds; constants at top of `recommender.py`).
   - `_passes_touch_gate` (v0.6.6) — rejects sell structures whose short leg's
     `probability_of_touch` exceeds `MAX_PROBABILITY_OF_TOUCH_SELL = 70`.
     Debit spreads are exempt (their short leg is a hedge, not a sell decision).
   - Min round-trip credit: `_score_bull_put`/`_score_bear_call` return `None`
     if credit `< MIN_SPREAD_CREDIT = 0.15` (can't cover round-trip costs).
5. Candidates are ranked (Step 5) by composite score, then `_build_recommendation`
   computes P&L boundaries, `expected_value` (three-outcome zone model via
   `_structure_expected_value`), `alpha = EV / max_loss` (Option Alpha metric),
   and Kelly-fraction sizing.
6. `_select_recommendations` applies the RiskManager budget: max loss per trade
   binds to 2% of equity, portfolio Greeks gate, half-Kelly clamp, position cap.
7. The winning recommendations become notifications in `data/brain_notifications.json`
   and the dashboard renders them as alert cards.

### Exit Rules (v0.6.6, strategy/regime-aware)

`_generate_exit_rules(strategy_type, candidate)` emits mechanical rules:
- Close at **50% of max profit** or at **21 DTE**, whichever comes first
  (Tastytrade research: 50% exits beat hold-to-expiry on P&L/day; gamma risk
  accelerates inside the final 21 days).
- IV Rank > 60 → raise target to **75%** (expensive premium).
- Iron condors: 25% target if the credit is thin (`credit/width < 0.5`), else
  50%; per-wing stop at 2-3x that wing's credit.
- Hard stop at 2-3x credit received; roll never for a loss.

## Version Constants (bump together)

- `orchestrator/main.py` line ~46: `version="0.6.6"`
- `dashboard/app/page.tsx` line ~113: `const VERSION = "v0.6.6"`
- `CHANGELOG.md`: add a dated entry under the new version heading.

## Run Commands

```bash
# Backend tests (from repo root)
python -m pytest tests/ -q

# Start the orchestrator (FastAPI on :8000)
uvicorn orchestrator.main:app --reload

# Paper-only IBKR bridge
python -m bridge.main            # or per docs/PAPER_BRIDGE.md

# Dashboard (from dashboard/)
npm install
npm run build                     # "test" script is just this now
npm run dev
```

## Testing Conventions

- Backend tests live in `tests/`, run with `python -m pytest tests/ -q`.
- `tests/test_trade_engine.py` prepends the repo root to `sys.path` and imports
  constants directly from `agents.trade_engine.recommender` — keep new gate
  constants importable at module level.
- New gates/metrics get a dedicated test block with plain assert style;
  `pytest.approx` is available (imported in the test file).
- Dashboard has no unit tests; `npm run build` is the smoke test.

## Data Files (state lives in data/)

| File | Contents |
| --- | --- |
| `data/brain_scan_results.json` | Last full scan results per symbol |
| `data/brain_notifications.json` | Pending trade notifications |
| `data/brain_scan_state.json` | Scanner status/progress |
| `data/alerts.json`, `data/alert_history.json` | AlertEngine state |
| `data/paper_order_ledger.json` | Paper orders placed via bridge |

`data/*.json` is gitignored; the repo ships with empty placeholders.

## Dead / Legacy Code (do not revive)

- `agents/scanner/`, `agents/performance/` — deleted in v0.6.6 (zero live consumers).
- `orchestrator/routes/scanner.py` — renamed to `backtest.py`; legacy `/scan/*`
  and `/gex/*` routes removed. The production GEX flows through
  `agents/trade_engine/ai_brain.py` + `agents/trade_engine/tasks.py`, not HTTP.
- Celery (`orchestrator/celery_app.py`), `agents/*/tasks.py`, docker-compose,
  `dashboard/app/_sites-preview/` — leftover scaffolding, not deployed.
- `agents/execution/tasks.py` — stub; there is **no live order path yet**. The
  bridge is paper-only.

## Known Quirks

- Python 3.12 and 3.14 are both installed; use `python -m pytest`.
- `py_vollib` is deprecated (import warning) — `agents/volatility/greeks.py`
  imports it directly.
- PowerShell on Windows: no `rg`; use the grep tool or `Select-String`.
- `data/*.json` is gitignored, so a fresh clone starts with empty state until
  the first scan run.

## What Comes Next (suggested roadmap)

1. Wire `agents/execution/` to the bridge's paper-order endpoints so a
   notification can be turned into an order.
2. Persist recommendation history (currently only the latest notifications are
   kept) so backtests can score the engine's own picks.
3. Expose `expected_value` and `alpha` in the dashboard alert cards (the API
   already returns them via `/api/advisor/opportunities` and `/api/advisor/recommend`).
