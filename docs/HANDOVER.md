# ThetaForge Engineering Handover

## Running Architecture

ThetaForge is a personal, paper-only options decision-support system.

```
Dashboard (GitHub Pages or localhost)
    -> authenticated requests -> Advisor (Railway FastAPI)
    -> authenticated local requests -> Paper Bridge (local FastAPI)
    -> Paper TWS or IB Gateway
```

- `orchestrator/main.py` starts the FastAPI Advisor and its 300-second
  background scan. Only `/health` is public; `/api/advisor/*` requires
  `ADVISOR_API_TOKEN`.
- `agents/trade_engine/` is the production recommendation path. Its
  `background_scanner.py` discovers a liquid universe and invokes
  `ai_brain.py` and `recommender.py`.
- `bridge/main.py` is the only paper-order path. It requires
  `BRIDGE_ACCESS_TOKEN`, rejects live ports/accounts, verifies executable IBKR
  quotes, proves defined risk, and applies the weekly-capital ledger.
- `dashboard/app/page.tsx` is the single-page client. Tokens are kept only in
  the browser session and must never be committed.

State is JSON under `data/`; the directory is ignored by Git. There is no
database, task queue, Docker Compose setup, Go scanner, or live-order path.
The Dockerfile remains because Railway builds the single Advisor service from
it.

## Recommendation Pipeline

1. The scanner loads market and option-chain data for each liquid symbol.
2. `AIBrain` derives regime and qualitative signal context.
3. `TradeRecommender` evaluates cash-secured puts, covered calls, credit
   spreads, iron condors, and debit spreads.
4. Candidates must pass composite/edge/POP, liquidity, IV-rank, volatility,
   probability-of-touch, defined-risk, position-size and portfolio-risk gates.
5. Passing candidates carry max profit/loss, POP, expected value, alpha,
   Greeks where available, and strategy-aware exit rules.

The system intentionally permits an empty result. It must not manufacture a
trade to fill a dashboard card, and it cannot promise profitable outcomes.

## Important Files

| Path | Purpose |
| --- | --- |
| `agents/trade_engine/recommender.py` | Authoritative candidate scoring and quality gates. |
| `agents/trade_engine/background_scanner.py` | Discovery, scan cadence, notifications. |
| `agents/trade_engine/ai_brain.py` | Regime and signal aggregation. |
| `orchestrator/routes/advisor.py` | Authenticated dashboard API. |
| `orchestrator/security.py` | Token validation and request-rate limits. |
| `bridge/main.py` | Paper-only IBKR order checks and submission. |
| `dashboard/app/page.tsx` | Dashboard, alert-to-trade modal, token entry. |
| `tests/` | Backend regression suite. |

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

- Railway needs `ADVISOR_API_TOKEN` set to a strong private value.
- The dashboard needs that same Advisor token once per browser session.
- Local `.env` needs `BRIDGE_ACCESS_TOKEN`; the dashboard needs it once per
  browser session before connecting the local Bridge.
- TWS/IB Gateway must be logged into the paper account with its API socket
  enabled. Keep account credentials in TWS/IB Gateway, never in the dashboard.

## Removed Surface

The fake positions, strategy-settings, backtest and live-toggle endpoints,
Celery/task-worker scaffolding, unused Go scanner, and unused performance
tracker were removed. Do not reintroduce a separate scoring or order path:
the recommender and `bridge/main.py` are the respective sources of truth.
