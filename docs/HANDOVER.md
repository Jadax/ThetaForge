# ThetaForge Engineering Handover

> Architecture, module map, invariants, and do-not rules live in `AGENTS.md`
> (single source of truth — this file does not duplicate them). This file
> covers only what AGENTS.md does not: runtime/deployment specifics, the
> recommendation pipeline order, journal publishing flow, and validation
> commands.

## Runtime Facts

- `orchestrator/main.py` starts the FastAPI Advisor plus two background
  scanners (options brain + equity), each on a 300-second cadence. Only
  `/health` is public; `/api/advisor/*` requires `ADVISOR_API_TOKEN`.
- Both scanners gate scans to NYSE market hours, wait out a post-boot grace
  period before their first tick (deploy-time health probes must pass before
  heavy work starts; equity is additionally staggered so both scanners never
  start together), and run all CPU-bound analysis plus JSON state I/O in
  worker threads so the event loop always services `/health`. Do not move any
  of that back onto the loop — see the health-check starvation history in
  `CHANGELOG.md` (v1.17.2–v1.17.7).
- `SCAN_CONCURRENCY` is **3** in both scanners. It is a rate-limit ceiling,
  not just a speed choice — higher values got CBOE-429'd from Render's
  outbound IP (confirmed via Render logs; a local test will not reproduce
  it). Lowering is safe; raising requires re-verifying against Render logs.
- State is JSON under `data/` (gitignored). No database, task queue, or
  live-order path. The Dockerfile exists because Render builds the single
  Advisor service from it directly (`render.yaml`).

## Render Free Tier

- No persistent disk; sleeps after 15 minutes without HTTP traffic, wiping
  in-process state (including IV history accumulated since boot).
- `.github/workflows/keep-advisor-warm.yml` pings the public `/health/`
  probe every 10 minutes so the instance stays up and `data/iv_history.json`
  keeps its real 52-week history. Needs no secrets — `/health/` is
  unauthenticated by design.
- If the assigned service URL differs from `thetaforge-advisor.onrender.com`,
  update `DEFAULT_ADVISOR_API` in `dashboard/app/page.tsx` and the URL in
  that workflow to match.
- When prompted at deploy, set `ADVISOR_API_TOKEN` to a strong private value
  (kept out of `render.yaml` via `sync: false`). An unset value makes the
  Advisor fail closed with 503 (`orchestrator/security.py`).

## Recommendation Pipeline (order matters)

1. Scanner loads price + option chain per liquid symbol (fail-closed skips).
2. Volatility context per symbol: current IV vs 20-day realized vol, IV
   percentile from the symbol's own history store (`data/iv_history.json`),
   expected move, VIX term structure, earnings proximity. All degrade to
   `None` — never placeholders.
3. Desk analytics on the same chain: IV skew (RR25/BF25), earnings
   implied-vs-realized move edge, short interest.
4. `AIBrain` derives regime + qualitative signals (`iv_signal`, flow,
   sentiment, GEX, technicals). Inverted VIX curve blocks premium selling;
   the macro-proximity veto applies here.
5. `TradeRecommender` evaluates strategies and applies quality gates
   (composite/edge/POP, liquidity, IV rank, probability-of-touch,
   defined-risk, position size, portfolio risk).
6. Empty output is a valid outcome. Never manufacture a trade to fill a card.

## Journal Publishing Flow

1. `scripts/sync_journal.py` regenerates `journal/trades.json` from the
   paper-order ledger (`data/paper_order_ledger.json`) — single source of
   truth. Keeps only ledger trades with a `recommendation_id` and live order
   status; folds closes into their parent entries.
2. `scripts/add_trade.py --from-ledger <id>` attaches narrative (thesis,
   exit note, tags, P&L); manual trades also supported.
3. Commit + push → GitHub Pages redeploys `journal.astraiva.app`.

The dashboard terminal is NOT deployed there; see `docs/HOSTED_TERMINAL.md`
for the Cloudflare Pages + Access path.

## Versioning and Validation

Bump all three locations together:

- `orchestrator/main.py` FastAPI version
- `dashboard/app/page.tsx` `VERSION` constant
- `CHANGELOG.md`

```powershell
python -m pytest tests -q          # repo root
# from dashboard/:
npm exec tsc -- --noEmit --incremental false
npm run build -- --webpack
```

## Removed Surface (do not reintroduce)

Fake positions / strategy-settings / backtest / live-toggle endpoints, Celery
scaffolding, Go scanner, legacy `agents/strategies/` and `agents/sentiment/`,
execution/LLM scaffolding, standalone backtester, dark-pool modules.
`agents/backtest/advanced_backtest.py` looks dead but is imported by
`ai_brain.py` and `tv_indicators.py` — see `docs/SIGNAL_POLICY.md` before
removing anything that looks unused.
