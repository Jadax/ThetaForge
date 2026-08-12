# ThetaForge Engineering Handover

## Running Architecture

ThetaForge is a personal, paper-only options decision-support system.

```
Dashboard (GitHub Pages or localhost)
    -> authenticated requests -> Advisor (Render FastAPI, free tier)
    -> authenticated local requests -> Paper Bridge (local FastAPI)
    -> Paper TWS or IB Gateway
```

- `orchestrator/main.py` starts the FastAPI Advisor and its 300-second
  background scan. Only `/health` is public; `/api/advisor/*` requires
  `ADVISOR_API_TOKEN`.
- `agents/trade_engine/` is the production recommendation path. Its
  `background_scanner.py` discovers a liquid universe and invokes
  `ai_brain.py` and `recommender.py`. Per-symbol analysis runs with bounded
  concurrency (`SCAN_CONCURRENCY = 5`, `asyncio.Semaphore`) rather than a
  sequential loop. This number is a rate-limit ceiling, not just a speed
  choice — confirmed live via Render's logs that a higher value (20) got
  CBOE-429'd on nearly every request from Render's outbound IP and appeared
  to starve other concurrent Advisor requests into 502s for the scan's
  duration. Do not raise it without re-verifying against Render's actual
  logs; a local measurement will not reproduce the failure.
- `bridge/main.py` is the only paper-order path. It requires
  `BRIDGE_ACCESS_TOKEN`, rejects live ports/accounts, verifies executable IBKR
  quotes, proves defined risk, and applies the weekly-capital ledger. It runs
  either locally beside TWS, or on an always-on Oracle Cloud VM beside a
  headless IB Gateway (`docs/AUTONOMOUS_TRADING.md`) — either way it binds
  `127.0.0.1` only and is never exposed to the public internet.
- `dashboard/app/page.tsx` is the single-page private terminal. Tokens
  persist in that browser's `localStorage` (never committed, never sent
  anywhere but the Advisor/Bridge the user points them at) and can be cleared
  with the in-app "Forget saved tokens" button. It is NOT deployed to the
  public GitHub Pages journal. It can optionally be deployed to Cloudflare
  Pages behind Cloudflare Access (`docs/HOSTED_TERMINAL.md`) for use from a
  computer that can't run the local launcher — Access, not app code, is what
  makes that safe to expose.
- `deployment/vm_auto_executor.py` autonomously submits paper orders that
  clear the Advisor's existing quality gates, through the Bridge, on the
  always-on VM — see `docs/AUTONOMOUS_TRADING.md` for the full pipeline,
  including why the paper-only lock must not be weakened for this.
- `journal/` is the public trade journal: a standalone static site
  (`index.html`, `styles.css`, `app.js`, `trades.json`, `.nojekyll`) served
  from the `gh-pages` branch root at `https://journal.astraiva.app/` — a
  custom domain via a `CNAME` file on that branch, DNS at Spaceship. Metrics
  are computed client-side from `trades.json`, never hardcoded.
- `scripts/sync_journal.py` is the journal's single source of truth: it
  regenerates `journal/trades.json` from the paper-order ledger
  (`data/paper_order_ledger.json`), keeping only trades with a
  `recommendation_id` and a live order status, and preserving narrative authored
  via `add_trade.py --from-ledger <id>` by `source_id`.
- `scripts/add_trade.py` attaches the narrative (thesis, exit note, tags, P&L,
  close date) to a ledger trade or adds a manual trade. Run add_trade and/or
  sync, commit, push — the journal on Pages redeploys.

State is JSON under `data/`; the directory is ignored by Git. There is no
database, task queue, Docker Compose setup, Go scanner, or live-order path.
The Dockerfile remains because Render builds the single Advisor service from
it directly (`render.yaml`).

Render's free web-service tier has no persistent disk and sleeps a service
after 15 minutes with no HTTP traffic, fully restarting the container (and
therefore the `data/*.json` state) on the next request — a far more frequent
reset than the occasional redeploy this already tolerated on the previous
host. `.github/workflows/keep-advisor-warm.yml` pings the public `/health/`
probe every 10 minutes, comfortably inside that 15-minute window, so the
instance does not sleep under normal operation and `iv_history.json` keeps its
real 52-week history intact. It needs no secrets — `/health/` is
unauthenticated by design (see `orchestrator/main.py`).

## Recommendation Pipeline

1. The scanner loads market and option-chain data for each liquid symbol.
2. Volatility context is assembled per symbol: current IV and 20-day realized
   volatility, IV percentile from the symbol's own per-symbol IV history store
   (`data/iv_history.json`), expected move, VIX term structure
   (contango/inversion), and earnings proximity.
3. Desk analytics run on the same free chain: IV skew (RR25/BF25 via
   `desk_analytics.calculate_iv_skew`), the earnings implied-vs-realized move
   read, and short interest (yfinance). All three degrade to `None` on missing
   data.
4. `AIBrain` derives regime and qualitative signal context. Its `iv_signal`
   carries `iv_rank`, `iv_percentile`, `expected_move_pct`, `term_structure`,
   `iv_skew`, `short_interest`, `earnings_move`, and the `iv_hv_ratio` fields.
   An inverted VIX curve downgrades sell-premium to neutral and blocks
   premium-selling strategies. `skew` and `short_interest` are weighted signal
   sources alongside `flow`, `iv`, `technical`, `cpr`, `sentiment`, `gex`, and
   `sideways`.
5. `TradeRecommender` evaluates cash-secured puts, covered calls, credit
   spreads, iron condors, and debit spreads.
6. Candidates must pass composite/edge/POP, liquidity, IV-rank, volatility,
   probability-of-touch, defined-risk, position-size and portfolio-risk gates.
7. Passing candidates carry max profit/loss, POP, expected value, alpha,
   Greeks where available, and strategy-aware exit rules.

The system intentionally permits an empty result. It must not manufacture a
trade to fill a dashboard card, and it cannot promise profitable outcomes.

## Important Files

| Path | Purpose |
| --- | --- |
| `agents/data_ingestion/cboe_data.py` | Free no-key CBOE chains (Greeks+IV), quotes, VIX term structure |
| `agents/data_ingestion/free_data.py` | IBKR > CBOE > yfinance chain, VIX term structure, contango, earnings date, short interest |
| `agents/volatility/desk_analytics.py` | IV skew (RR25/BF25), earnings implied-vs-realized move, front-month straddle move |
| `agents/volatility/iv_history.py` | Per-symbol daily IV snapshots → real IV rank/percentile |
| `agents/volatility/iv_metrics.py` | `calculate_iv_rank`, `calculate_iv_percentile`, realized vol |
| `agents/trade_engine/recommender.py` | Authoritative candidate scoring and quality gates. |
| `agents/trade_engine/background_scanner.py` | Discovery, scan cadence, notifications. |
| `agents/trade_engine/ai_brain.py` | Regime and signal aggregation. |
| `orchestrator/routes/advisor.py` | Authenticated dashboard API. |
| `orchestrator/security.py` | Token validation and request-rate limits. |
| `bridge/main.py` | Paper-only IBKR order checks and submission. |
| `dashboard/app/page.tsx` | Private terminal: analysis, alert-to-trade modal, token entry. Runs local or hosted (Cloudflare Access-gated); identical either way. |
| `deployment/cloudflare_deploy_terminal.ps1` | Builds and deploys the private terminal to Cloudflare Pages. |
| `journal/` | Public trade journal (static site on Pages; client-computed metrics). |
| `scripts/sync_journal.py` | Regenerates `journal/trades.json` from the paper-order ledger. |
| `scripts/add_trade.py` | Journal narrative input; `--from-ledger` attaches to a ledger-placed trade. |
| `tests/` | Backend regression suite. |
| `docs/SIGNAL_POLICY.md` | Provenance of the free feeds and volatility gates — read before removing anything that looks unused. |
| `docs/HOSTED_TERMINAL.md` | Cloudflare Pages + Access setup for reaching the terminal from any computer. |
| `docs/AUTONOMOUS_TRADING.md` | Always-on VM running Gateway + Bridge + the autonomous executor; setup gotchas and runbook. |

## Versioning and Validation

When changing the product, bump all three locations together:

- `orchestrator/main.py` FastAPI version
- `dashboard/app/page.tsx` `VERSION` constant
- `CHANGELOG.md`

Run from the repository root:

```powershell
python -m pytest tests -q
```

Run the dashboard checks from `dashboard/`:

```powershell
npm exec tsc -- --noEmit --incremental false
npm run build -- --webpack
```

## Deployment Requirements

1. **Render**: dashboard.render.com → New → Blueprint → point at this repo.
   Render reads `render.yaml` and builds `Dockerfile` as-is. When prompted,
   set `ADVISOR_API_TOKEN` to a strong private value (it is deliberately left
   out of `render.yaml` via `sync: false` so it is never committed). Every
   `/api/advisor/*` route requires it; an unset value makes the Advisor fail
   closed with 503 rather than serve unauthenticated (`orchestrator/security.py`).
2. If the assigned service URL differs from `thetaforge-advisor.onrender.com`
   (Render appends a suffix if that name is taken), update `DEFAULT_ADVISOR_API`
   in `dashboard/app/page.tsx` and the URL hardcoded in
   `.github/workflows/keep-advisor-warm.yml` to match.
3. The dashboard needs that same Advisor token once per browser session,
   entered in its "Advisor API address and token" panel.
4. Local `.env` needs `BRIDGE_ACCESS_TOKEN`; the dashboard needs it once per
   browser session before connecting the local Bridge.
5. TWS/IB Gateway must be logged into the paper account with its API socket
   enabled. Keep account credentials in TWS/IB Gateway, never in the dashboard.

## Removed Surface

The fake positions, strategy-settings, backtest and live-toggle endpoints,
Celery/task-worker scaffolding, unused Go scanner, unused performance tracker,
the legacy `agents/strategies/` and `agents/sentiment/` packages, the unused
execution/LLM scaffolding, the standalone backtester, and the unused dark-pool
and scanner-pipeline modules were removed. Do not reintroduce a separate
scoring or order path: the recommender and `bridge/main.py` are the respective
sources of truth.

`agents/backtest/advanced_backtest.py` is deliberately retained: `SignalEngine`
(macd/rsi/adx helpers) is imported by `ai_brain.py` and `tv_indicators.py`. See
`docs/SIGNAL_POLICY.md` before removing anything that looks unused.
