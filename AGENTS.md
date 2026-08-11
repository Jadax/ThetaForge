# AGENTS.md

Operating notes for AI agents working in ThetaForge. Read `docs/SIGNAL_POLICY.md`
before touching anything volatility-, CBOE-, or scanner-related — several
"unused" files are upstream suppliers for active gates.

## Repository

- Windows + PowerShell 5.1. No `rg`; use the grep/glob tools.
- Tests: `python -m pytest tests/ -q` (from repo root). Keep it green.
- Lint/typecheck: Python has no configured linter; the dashboard has
  `npm exec tsc -- --noEmit` and `npm run build -- --webpack` from `dashboard/`.
- Version is a single number bumped in exactly three places
  (see `docs/HANDOVER.md` → Versioning).

## Architecture (paper-only, single FastAPI service)

```
Dashboard (Next.js, GitHub Pages/localhost)
  -> Advisor (Cloud Run free tier, orchestrator/main.py, request-driven scan
     via Cloud Scheduler every 20 min — see background_scanner.py's
     SCAN_CONCURRENCY comment and docs/HANDOVER.md)
     -> Paper Bridge (local FastAPI, bridge/main.py) -> Paper TWS/IB Gateway
```

- `agents/trade_engine/` is the authoritative recommendation path.
- `bridge/main.py` is the ONLY paper-order path; it rejects live accounts,
  naked options, and undefined-risk structures.
- State lives in `data/*.json` (gitignored). No database, no Celery.

## Module Map (production)

| Path | Role |
| --- | --- |
| `agents/data_ingestion/cboe_data.py` | Free no-key CBOE delayed quotes: option chains (Greeks+IV), quotes, VIX term structure |
| `agents/data_ingestion/free_data.py` | Multi-source provider: IBKR > CBOE > Alpaca > yfinance; VIX term structure, contango, earnings date, short interest |
| `agents/trade_engine/background_scanner.py` | Universe discovery + `_analyze_one` (feeds all vol inputs to Brain) |
| `agents/trade_engine/ai_brain.py` | Regime, signal aggregation, `iv_signal` (rank/percentile/term-structure/iv_skew/short_interest/earnings_move), strategy selection gates |
| `agents/trade_engine/recommender.py` | Authoritative candidate scoring and quality gates |
| `agents/trade_engine/theoretical_edge.py` | Own BS model value vs CBOE mid → `theoretical_edge_pct` on recommendations |
| `agents/trade_engine/historical_backtest.py` | Empirical win rate/expectancy/drawdown over realized credit-spread outcomes |
| `agents/volatility/desk_analytics.py` | Desk surfaces from the free chain: IV skew (RR25/BF25), earnings implied-vs-realized move, front-month straddle move |
| `agents/volatility/iv_history.py` | Daily per-symbol ATM-IV snapshots → IV rank/percentile |
| `agents/volatility/iv_metrics.py` | `calculate_iv_rank`, `calculate_iv_percentile`, realized vol |
| `agents/volatility/flow_metrics.py` | Free-data RV bands, unusual volume, OI divergence, OI center-of-mass, IV mover |
| `orchestrator/routes/advisor.py` | Authenticated dashboard API |
| `bridge/main.py` | Paper-only IBKR order checks and submission |
| `dashboard/app/page.tsx` | Private terminal (local-only, NOT on Pages) |
| `journal/` | Public trade journal — static site served at `https://jadax.github.io/ThetaForge/`; only entries placed on TWS from the paper-order ledger |
| `scripts/sync_journal.py` | Regenerates `journal/trades.json` from the paper-order ledger (single source of truth) |
| `scripts/add_trade.py` | Journal narrative input CLI; `--from-ledger` attaches to a TWS-placed trade |
| `scripts/journal_common.py` | Shared ledger→journal leg mapping used by `add_trade.py` and `sync_journal.py` |
| `scripts/recap.py` | Weekly/monthly recap export from `journal/trades.json` |
| `journal/learn/` | Static free-education playbook (IV rank, expected move, POP) |
| `deployment/gcp_deploy.ps1` | Deploys the Advisor to Cloud Run + sets up the Cloud Scheduler scan trigger |
| `tests/` | Backend regression suite |

## Volatility Model (keep intact)

- IV Rank is computed against a real per-symbol history store, not spot IV.
- `iv_signal` in the Brain carries `iv_rank`, `iv_percentile`,
  `expected_move_pct`, `term_structure`, plus the `iv_hv_ratio` fields.
- Inverted VIX term structure = `no_trade` for premium selling.
- Fail-closed: missing price/chain/VIX/history → recorded skip reason, never a
  placeholder signal.

## Do Not

- Do not delete "dead-looking" modules without grepping for importers across
  the whole repo (including `orchestrator/` and `bridge/`). `advanced_backtest.py`
  looks dead but `ai_brain.py` imports its `SignalEngine`.
- Do not add paid data dependencies; every feed is free (see SIGNAL_POLICY).
- Do not add a second scoring or order path.
- Do not reintroduce removed fake/backtest/live-toggle routes or Celery.
- Do not lower `SCAN_CONCURRENCY` or tighten the Cloud Scheduler interval
  below what `docs/HANDOVER.md` → Deployment Requirements documents without
  re-measuring: both are load-bearing for staying inside Cloud Run's free
  vCPU-second quota, not arbitrary tuning knobs.
