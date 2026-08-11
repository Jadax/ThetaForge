# ThetaForge Engineering Handover

## Running Architecture

ThetaForge is a personal, paper-only options decision-support system.

```
Dashboard (GitHub Pages or localhost)
    -> authenticated requests -> Advisor (Google Cloud Run, free tier)
    -> authenticated local requests -> Paper Bridge (local FastAPI)
    -> Paper TWS or IB Gateway
```

- `orchestrator/main.py` starts the FastAPI Advisor. Its `lifespan` still
  starts a 300-second internal background-scan loop, which is what runs when
  the app is hosted anywhere that stays up continuously (e.g. `docker-compose`
  locally). On Cloud Run's free tier that internal loop does not reliably
  fire on its own schedule — see Deployment Requirements below for why, and
  for the Cloud Scheduler trigger that replaces it there. Only `/health` is
  public; `/api/advisor/*` requires `ADVISOR_API_TOKEN`.
- `agents/trade_engine/` is the production recommendation path. Its
  `background_scanner.py` discovers a liquid universe and invokes
  `ai_brain.py` and `recommender.py`.
- `bridge/main.py` is the only paper-order path. It requires
  `BRIDGE_ACCESS_TOKEN`, rejects live ports/accounts, verifies executable IBKR
  quotes, proves defined risk, and applies the weekly-capital ledger.
- `dashboard/app/page.tsx` is the single-page private terminal. Tokens are kept
  only in the browser session and must never be committed. It is NOT deployed
  to GitHub Pages — it runs locally (it needs the local Paper Bridge anyway).
- `journal/` is the public trade journal: a standalone static site
  (`index.html`, `styles.css`, `app.js`, `trades.json`, `.nojekyll`) served from
  the gh-pages branch root at `https://jadax.github.io/ThetaForge/`. Metrics
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
The Dockerfile remains because Cloud Run builds the single Advisor service
from it directly (`deployment/gcp_deploy.ps1` runs `gcloud run deploy
--source .`).

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
| `dashboard/app/page.tsx` | Private terminal: analysis, alert-to-trade modal, token entry. Local-only. |
| `journal/` | Public trade journal (static site on Pages; client-computed metrics). |
| `scripts/sync_journal.py` | Regenerates `journal/trades.json` from the paper-order ledger. |
| `scripts/add_trade.py` | Journal narrative input; `--from-ledger` attaches to a TWS-placed trade. |
| `deployment/gcp_deploy.ps1` | Deploys the Advisor to Cloud Run and sets up the Cloud Scheduler scan trigger. |
| `tests/` | Backend regression suite. |
| `docs/SIGNAL_POLICY.md` | Provenance of the free feeds and volatility gates — read before removing anything that looks unused. |

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

### Why Cloud Run changes the scan architecture

Cloud Run's Always Free tier (180,000 vCPU-seconds, 360,000 GiB-seconds, 2M
requests per month) is only free if the container scales to zero when idle
(`min-instances=0`). Keeping one instance alive 24/7 to let the app's own
internal 300-second `asyncio` loop (`orchestrator/main.py` lifespan →
`background_scanner.py`) run continuously requires "CPU always allocated,"
which bills roughly 2.6M vCPU-seconds/month for a single always-on instance —
about 14× the entire free monthly quota. There is no way to keep that loop
running around the clock on this tier without it costing real money almost
immediately.

The fix is architectural, not a workaround: a Cloud Scheduler job (free tier:
3 jobs/billing account/month, we use 1) calls the already-authenticated
`POST /api/advisor/scanner/trigger` on a schedule instead. Each call is a
normal, billable Cloud Run request that completes and lets the instance scale
back to zero — which fits the free tier's actual economics (pay only while
handling a request) instead of fighting them.

That still has to fit inside the vCPU-second budget, which is why
`background_scanner.py`'s `_analyze_one` calls were parallelized
(`SCAN_CONCURRENCY = 20`, bounded `asyncio.Semaphore`) instead of running
sequentially. Sequential, a full scan of the ~130-symbol universe took
several minutes; measured against live data sources at `SCAN_CONCURRENCY=20`,
it completes in **~69 seconds** with no increase in skipped/failed symbols.
At that measured rate:

```
180,000 vCPU-sec/month free ÷ 69 vCPU-sec/scan ≈ 2,600 scans/month max
43,200 min/month ÷ 2,600 scans ≈ 16.5 min = the break-even interval
```

The deploy script schedules the trigger every **20 minutes** — comfortably
under that break-even point, leaving margin for real-world variance (Cloud
Run's network path to yfinance/CBOE may differ from wherever this was
measured, plus per-invocation cold-start overhead) and for the dashboard's
own on-demand `/brain/analyze` / `/recommend` calls, which draw from the same
budget. **Do not tighten this below 20 minutes without re-measuring** — see
the comment above `SCAN_CONCURRENCY` in `background_scanner.py`. After a few
days live, check Cloud Run's Metrics tab against the free quota before
considering it.

### Known limitation: state does not persist between scan cycles

Because the container is not kept warm between Cloud Scheduler triggers
(that's what keeps it free), `data/*.json` — the per-symbol IV history behind
IV Rank/Percentile (`iv_history.json`), the notification queue, and the
watchlist — does not reliably survive from one scan cycle to the next. Each
cold start effectively starts that history fresh. Live Brain analysis
(`/brain/analyze`, `/recommend`) is unaffected since it doesn't depend on
that history; **IV Rank quality and notification continuity are what
degrade**. Fixing this properly means moving that state off the container's
local disk — e.g. a Cloud Run Cloud Storage FUSE volume mount, batched to a
small number of writes per scan cycle to stay inside GCS's free-tier
operation quota (5,000 Class A / 50,000 Class B ops per month; the current
per-symbol write pattern would blow through that in a single day and needs
batching first) — which was deliberately scoped out of this deployment change
as separate follow-up work rather than shipped partially.

### Setup

1. Create a GCP project with billing enabled (required by Cloud Run even for
   free-tier usage — this is an account-level step only you can do) and run
   `gcloud auth login`.
2. From the repository root: `deployment/gcp_deploy.ps1 -ProjectId <your-project-id>`.
   It enables the required APIs, creates `ADVISOR_API_TOKEN` in Secret
   Manager (prompts once, masked, if the secret doesn't already exist — the
   value is never written to a file or committed), deploys the Advisor to
   Cloud Run, and creates the Cloud Scheduler trigger job. Safe to re-run;
   every step is idempotent.
3. Note the printed service URL. If it's not what `DEFAULT_ADVISOR_API` in
   `dashboard/app/page.tsx` expects, update that constant to match.
4. The dashboard needs that same Advisor token once per browser session,
   entered in its "Advisor API address and token" panel.
5. Local `.env` needs `BRIDGE_ACCESS_TOKEN`; the dashboard needs it once per
   browser session before connecting the local Bridge.
6. TWS/IB Gateway must be logged into the paper account with its API socket
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
