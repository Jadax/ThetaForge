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
Dashboard (Next.js, GitHub Pages/localhost, or hosted terminal)
  -> Advisor (Render FastAPI free tier, orchestrator/main.py, 5-min background scan)
     -> Paper Bridge (bridge/main.py) -> Paper TWS/IB Gateway
        Bridge + Gateway run on an always-on Oracle Cloud VM, not locally --
        see docs/AUTONOMOUS_TRADING.md. A headless auto-executor on that same
        VM (deployment/vm_auto_executor.py) autonomously submits qualifying
        paper orders through the Bridge; a market-hours supervisor starts
        and stops the whole stack around the real NYSE session.
```

- `agents/trade_engine/` is the authoritative recommendation path.
- `bridge/main.py` is the ONLY paper-order path; it rejects live accounts,
  naked options, and undefined-risk structures. This is a hard, intentional
  invariant -- do not weaken it to make a future live-trading switch easier;
  see the guardrail below.
- State lives in `data/*.json` (gitignored). No database, no Celery.

## Module Map (production)

| Path | Role |
| --- | --- |
| `agents/data_ingestion/cboe_data.py` | Free no-key CBOE delayed quotes: option chains (Greeks+IV), quotes, VIX term structure |
| `agents/data_ingestion/free_data.py` | Multi-source provider: IBKR > CBOE > Alpaca > yfinance; VIX term structure, contango, earnings date, short interest |
| `agents/trade_engine/background_scanner.py` | Universe discovery + `_analyze_one` (feeds all vol inputs to Brain); bounded-concurrency scan (`SCAN_CONCURRENCY`) |
| `agents/trade_engine/ai_brain.py` | Regime, signal aggregation, `iv_signal` (rank/percentile/term-structure/iv_skew/short_interest/earnings_move), strategy selection gates |
| `agents/trade_engine/recommender.py` | Authoritative candidate scoring and quality gates |
| `agents/trade_engine/theoretical_edge.py` | Own BS model value vs CBOE mid → `theoretical_edge_pct` on recommendations |
| `agents/trade_engine/historical_backtest.py` | Empirical win rate/expectancy/drawdown over realized credit-spread outcomes |
| `agents/trade_engine/high_winrate.py` | Research-backed entry context vetoes (trend alignment, expected-move buffer, DTE band, earnings blackout, relative strength) — pure gates used by both the Brain and Recommender step 4c |
| `agents/trade_engine/trade_manager.py` | Open-position management rules (50% take-profit, 21-DTE gamma, 2×-credit stop, pre-earnings, tested-strike review) + portfolio plan; recommended via `POST /api/advisor/positions/management`, never an order path |
| `agents/volatility/desk_analytics.py` | Desk surfaces from the free chain: IV skew (RR25/BF25), earnings implied-vs-realized move, front-month straddle move |
| `agents/volatility/iv_history.py` | Daily per-symbol ATM-IV snapshots → IV rank/percentile |
| `agents/volatility/iv_metrics.py` | `calculate_iv_rank`, `calculate_iv_percentile`, realized vol |
| `agents/volatility/flow_metrics.py` | Free-data RV bands, unusual volume, OI divergence, OI center-of-mass, IV mover |
| `orchestrator/routes/advisor.py` | Authenticated dashboard API |
| `bridge/main.py` | Paper-only IBKR order checks and submission |
| `dashboard/app/page.tsx` | Private terminal — local, or hosted on Cloudflare Pages behind Cloudflare Access (never on the public journal's GitHub Pages) |
| `deployment/cloudflare_deploy_terminal.ps1` | Builds and deploys the private terminal to Cloudflare Pages |
| `deployment/vm_auto_executor.py` | Autonomous paper-order executor, runs on the Oracle VM beside the Bridge |
| `deployment/market_hours_supervisor.sh` | Starts/stops Gateway+Bridge+executor on the VM around real market hours |
| `deployment/journal_sync_push.sh` | Auto-publishes the journal from the VM's ledger after an autonomous fill |
| `journal/` | Public trade journal — static site served at `https://journal.astraiva.app/` (custom domain on the `gh-pages` branch, `CNAME` file); only entries placed on TWS from the paper-order ledger |
| `scripts/sync_journal.py` | Regenerates `journal/trades.json` from the paper-order ledger (single source of truth) |
| `scripts/add_trade.py` | Journal narrative input CLI; `--from-ledger` attaches to a TWS-placed trade |
| `scripts/journal_common.py` | Shared ledger→journal leg mapping used by `add_trade.py` and `sync_journal.py` |
| `scripts/recap.py` | Weekly/monthly recap export from `journal/trades.json` |
| `journal/learn/` | Static free-education playbook (IV rank, expected move, POP) |
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
- Do not raise `SCAN_CONCURRENCY` (`background_scanner.py`) back toward 20
  without re-verifying against Render's actual logs first. It was lowered to
  5 after confirming live (not just locally) that 20-way fan-out got
  CBOE-429'd on nearly every request from Render's outbound IP and appeared
  to starve other concurrent `/api/advisor/*` requests into 502s for the
  scan's duration. A local measurement will not reproduce this -- CBOE did
  not rate-limit a residential IP at the same concurrency. See the comment
  above the constant.
- Do not add an in-app password/login check to `dashboard/app/page.tsx` as a
  substitute for Cloudflare Access. The terminal is a static export with no
  server; any client-side check is fully bypassable (the JS is downloadable
  regardless of what it does at runtime) and would be fake security. Access
  is a platform-level gate in front of the site, not something the app code
  can or should reimplement — see `docs/HOSTED_TERMINAL.md`.
- Do not loosen `bridge/main.py`'s paper-only checks (`PAPER_ONLY`, the
  `DU`-account check, `TradingMode=paper` in IBC's config on the VM) to make
  a future live-trading switch more convenient. If live trading is ever
  wanted, that is a deliberate, separately-reviewed change made at the time
  it's actually needed — not a standing unlock left in autonomous, unattended
  code. See `docs/AUTONOMOUS_TRADING.md`.
