# ThetaForge

**Personal, paper-first options trading intelligence and IBKR execution system.**

---

> **CRITICAL WARNING**: This system CANNOT and WILL NOT guarantee winning trades. Options trading involves substantial risk of loss. This is a probabilistic decision-support and execution tool. Past performance does not guarantee future results. Never invest money you cannot afford to lose.

---

## Current Production Path

- **Website dashboard**: GitHub Pages frontend backed by the Render Advisor.
- **Automatic discovery**: Screens up to 300 liquid/active underlyings and runs
  deeper options analysis on the evidence-based shortlist.
- **Unified Brain**: Combines regime, volatility, technical, positioning, flow,
  sentiment, event, and portfolio context into one decision.
- **Quality gates**: Recommendations must clear composite, edge, and modeled
  probability thresholds. A no-trade result is intentional.
- **Live IBKR verification**: Selected structures are repriced from live IBKR
  bid/ask data immediately before paper submission.
- **Paper-only execution**: The local Bridge rejects live accounts, delayed
  quotes, naked options, unsupported structures, and capital-limit violations.
- **Order ledger**: Bridge-submitted paper orders, fills, status, and weekly
  capital reservations appear on the dashboard.

## Free Data Sources

| Source | Data | Cost |
|--------|------|------|
| CBOE delayed quotes (no key) | Option chains with full Greeks + IV, quotes, VIX term structure (15-min delayed) | Free |
| IBKR API | Real-time options, Greeks, execution | Account and relevant market-data entitlements required |
| Alpaca Markets | Stocks, options (backup) | Free tier |
| yfinance | Historical prices, option chains, VIX indices, earnings calendar | Free |

Every feed in the production path is free. There is no paid API dependency.

## Quick Start

1. Install Python 3.12 and Node.js 22 or newer.
2. Install Python dependencies with `pip install -r requirements.txt`.
3. Run `npm install` inside `dashboard`.
4. Copy `.env.example` to `.env`, set a long random `BRIDGE_ACCESS_TOKEN` and a
   long random `ADVISOR_API_TOKEN`, and keep the Bridge locked to the paper
   port. Both tokens are required: the Bridge and the Advisor refuse requests
   without them. Set `ADVISOR_API_TOKEN` on the hosted Advisor too, then enter
   the same value in the dashboard's Advisor panel.
5. Start and sign in to IBKR Paper TWS or IB Gateway.
6. Double-click `Start-ThetaForge.cmd`.

Docker is optional development infrastructure; it is not required for the
personal dashboard and paper Bridge.

## Scanner Pipeline

```
Liquid core + public screeners + optional local IBKR discoveries
  → up to 300 first-pass underlyings
  → free CBOE chain/quote + VIX term structure per symbol
  → price/volume liquidity and movement screen
  → top 10 full option-chain analyses
  → composite + edge + modeled POP quality gates
  → diversified Advisor-selected stocks
  → live IBKR verification before paper submission
```

Each analyzed symbol feeds the Brain a real volatility context: current IV,
20-day realized volatility, IV percentile from the symbol's own per-symbol IV
history, expected move, VIX term structure (contango/inversion), and earnings
proximity. Selling premium is gated on elevated IV rank/percentile **and** a
healthy VIX curve; an inverted curve or earnings inside 7 days blocks new
positions.

The hosted Render scanner cannot directly reach a Bridge running on a personal
computer. Local IBKR discoveries are sent by the dashboard when the Bridge is
connected; the hosted background scan otherwise uses its public discovery path.
A scheduled GitHub Actions workflow (`.github/workflows/keep-advisor-warm.yml`)
pings the Advisor's public `/health/` probe every 10 minutes so Render's free
tier never idles it to sleep — a sleep/wake cycle would otherwise reset the
persisted IV history and notification state on every wake, since Render's free
web services have no persistent disk.

## Strategy Research Library

| # | Strategy | Win Rate | Difficulty | Best Market | IVR Required |
|---|----------|----------|------------|-------------|--------------|
| 1 | Bull Put Credit Spreads | 65-80% | Medium | Bullish/Sideways | >40 |
| 2 | Bear Call Credit Spreads | 65-80% | Medium | Bearish/Sideways | >40 |
| 3 | Cash-Secured Puts | 70-85% | Easy | Bullish/Neutral | >40 |
| 4 | LEAPS | 40-55% | Easy | Long-term bull | <30 |
| 5 | Covered Calls | 75-90% | Easy | Neutral | Any |
| 6 | Iron Condors | 65-80% | Hard | Sideways | >50 |
| 7 | Long Calls | 35-45% | Easy | Strong uptrend | <25 |
| 8 | Long Puts | 35-45% | Easy | Strong downtrend | <25 |
| 9 | The Wheel | 70-85% | Easy | Bullish/Neutral | >40 |
| 10 | Call Debit Spreads | 45-55% | Easy | Trending up | <30 |
| 11 | Calendar Spreads | 55-65% | Medium | Low IV → High IV | <30 |
| 12 | Butterfly Spreads | 60-75% | Hard | Range-bound | <30 |
| 13 | 0DTE Plays | 30-40% | Expert | Catalyst/expansion | Low VIX |

These are research modules, not a promise that every strategy is emitted or
executable. The current dashboard execution path supports cash-secured puts,
covered calls, defined-risk vertical spreads, and iron condors. Other modules
remain analysis/research work until they have production-grade pricing, risk,
and execution tests.

## Architecture

```
Dashboard (GitHub Pages or localhost)
        │ authenticated HTTPS requests
Advisor on Render (FastAPI + background scanner)
        │ authenticated local request when an order is requested
Paper-only IBKR Bridge on your computer
        │
Paper TWS or IB Gateway
```

The production system is a single FastAPI Advisor and a local paper-only
Bridge. `agents/trade_engine/` is the authoritative recommendation path: it
uses the AI brain, option-chain data, strategy scoring, probability, liquidity,
volatility and risk gates before surfacing a candidate. The dashboard is a
client; it never receives IBKR credentials.

## Risk Management

### Circuit Breakers

| Circuit Breaker | Threshold | Action |
|----------------|-----------|--------|
| Daily Loss | -15% | Halt all trading |
| Max Drawdown | -50% | Permanent halt |
| Net Delta | >20% | Reject new positions |
| Net Vega | >5% | Reject new positions |
| Position Risk | >2% per trade | Reject trade |

### Position Sizing
- **Half-Kelly Criterion**: f = (p * b - q) / (2 * b)
- **Max risk per trade**: 2% of portfolio
- **Correlation limit**: Max 20% in any single sector

## IBKR Setup

1. Download IBKR TWS or Gateway
2. Enable API: File > Global Configuration > API > Settings
3. For IB Gateway, use port 4002 (Paper) or 4001 (Live)
4. Enable "Accept inbound connections"
5. Copy `.env.example` to `.env` and configure

### Live Trading

The personal Bridge is intentionally paper-only and accepts only IBKR paper
ports `4002` or `7497` plus a `DU` paper account. Live-account execution is not
enabled by the dashboard or the Bridge.

Every paper order goes through a single endpoint that requires live executable
IBKR bid/ask data, proves the structure is defined-risk, checks the order
against the weekly capital reservation in the ledger, and verifies available
funds for cash-secured puts and share ownership for covered calls. Structures
it cannot prove defined-risk — including naked short options — are rejected.

## Project Structure

```
thetaforge/
├── Dockerfile
├── requirements.txt
├── .env.example
├── architecture_diagram.mermaid
├── orchestrator/              # FastAPI app, routes, access control
├── bridge/                    # Local paper-only IBKR order Bridge
├── dashboard/                 # Next.js dashboard
├── agents/
│   ├── trade_engine/          # Production path: Brain, recommender, scanner
│   ├── data_ingestion/        # CBOE (free no-key), yfinance, Alpaca
│   ├── volatility/            # IV history store, IV Rank/Percentile, realized vol, Greeks
│   ├── flow_analysis/         # Unusual activity, GEX engine
│   ├── technical/             # RSI, MACD, Bollinger, trend
│   ├── risk_management/       # Kelly + portfolio limits
│   └── backtest/              # SignalEngine indicators (macd/rsi/adx) for the Brain
├── tests/                     # Unit tests
├── scripts/                   # Journal CLI, journal sync, recap
├── journal/                   # Public trade journal site
└── docs/                      # Handover, signal/data policy
```

The deployed Advisor is a single FastAPI process using JSON files in `data/`
for state. It does not use a database, Celery worker, Docker Compose, or a
separate Go scanner. The Dockerfile remains because Render builds the service
from it directly (see `render.yaml`).

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=agents --cov-report=term-missing
```

## Configuration

All configuration via environment variables. See `.env.example`.

## Deployment

The Advisor deploys to [Render](https://render.com)'s free web-service tier
via `render.yaml` — it builds the existing `Dockerfile` directly, no separate
image config needed. `.github/workflows/keep-advisor-warm.yml` pings the
public `/health/` probe every 10 minutes so the free tier's 15-minute idle
sleep never triggers; a sleep/wake cycle would otherwise reset the persisted
IV history and notification state on every wake, since Render's free web
services have no persistent disk. Full setup steps are in
`docs/HANDOVER.md` → Deployment Requirements.

## Disclaimers

- **Not Financial Advice**: This is a decision-support tool only
- **Substantial Risk**: Options trading can result in total loss of capital
- **No Guarantees**: Past performance does not guarantee future results
- **IBKR Compliance**: Comply with Interactive Brokers API terms of service
- **Paper First**: Minimum 3 months paper trading before live deployment

## License

MIT License
