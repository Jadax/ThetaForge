# ThetaForge

**Personal, paper-first options trading intelligence and IBKR execution system.**

---

> **CRITICAL WARNING**: This system CANNOT and WILL NOT guarantee winning trades. Options trading involves substantial risk of loss. This is a probabilistic decision-support and execution tool. Past performance does not guarantee future results. Never invest money you cannot afford to lose.

---

## Current Production Path

- **Website dashboard**: GitHub Pages frontend backed by the Railway Advisor.
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
| IBKR API | Real-time options, Greeks, execution | Account and relevant market-data entitlements required |
| Alpaca Markets | Stocks, options (backup) | Free tier |
| yfinance | Historical data, option chains, VIX | Free |
| Reddit API | Community sentiment (8 subreddits) | Free |
| CBOE | Put/call ratio, VIX data | Free |
| FINRA | Dark pool volume data (weekly) | Free |

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
  → price/volume liquidity and movement screen
  → top 10 full option-chain analyses
  → composite + edge + modeled POP quality gates
  → diversified Advisor-selected stocks
  → live IBKR verification before paper submission
```

The hosted Railway scanner cannot directly reach a Bridge running on a personal
computer. Local IBKR discoveries are sent by the dashboard when the Bridge is
connected; the hosted background scan otherwise uses its public discovery path.

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
┌─────────────────────────────────────────────────────────────────┐
│                     USER INTERFACE LAYER                        │
│              React Dashboard + Discord/Telegram Alerts          │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│               ORCHESTRATOR (FastAPI + Celery)                   │
│         Manages all agents, scheduling, state management        │
└────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────────┘
     │      │      │      │      │      │      │      │
┌────▼──┐┌──▼───┐┌─▼──┐┌──▼───┐┌─▼──┐┌──▼──┐┌──▼──┐┌─▼────┐
│  Data ││Scan  ││Vol  ││Flow  ││Tech││GEX  ││Dark ││Reddit│
│  Inj. ││(Go)  ││Eng. ││Anly. ││Ind.││Eng. ││Pool ││Senti.│
└───────┘└──────┘└─────┘└──────┘└────┘└─────┘└─────┘└──────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│                         DATA LAYER                              │
│     TimescaleDB (tick/OHLC) + PostgreSQL (trades) + Redis       │
└─────────────────────────────────────────────────────────────────┘
```

### 12 Agent System

| Agent | Purpose | Data Source |
|-------|---------|-------------|
| Data Ingestion | Real-time + historical data | IBKR, yfinance, Alpaca |
| Scanner | 7000+ securities scan | IBKR, yfinance |
| Volatility Engine | IV Rank, IV Percentile, Greeks | IBKR, yfinance |
| Flow Analysis | Unusual activity, sweep detection | IBKR, volume analysis |
| GEX Engine | Dealer positioning, gamma levels | Option chain calculation |
| Dark Pool | Institutional activity detection | Volume anomalies, OI |
| Technical | Trend, RSI, MACD, Bollinger | yfinance |
| Sentiment | Reddit community sentiment | Reddit API |
| Risk Management | Kelly sizing, portfolio limits | Internal |
| Execution | Order management with stop-losses | IBKR |
| Performance | Kinfo-style PnL tracking | Internal |
| Alerts | Discord/Telegram/Slack notifications | Webhooks |

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
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── architecture_diagram.mermaid
├── orchestrator/              # FastAPI app, routes, access control
├── bridge/                    # Local paper-only IBKR order Bridge
├── dashboard/                 # Next.js dashboard
├── agents/
│   ├── trade_engine/          # Production path: Brain, recommender, scanner
│   ├── data_ingestion/        # IBKR, yfinance, Alpaca
│   ├── volatility/            # IV Rank/Percentile, Greeks, term structure
│   ├── flow_analysis/         # Unusual activity, dark pool, GEX engine
│   ├── technical/             # RSI, MACD, Bollinger, trend
│   ├── strategies/            # 13 strategy modules (research)
│   ├── sentiment/             # Reddit sentiment (8 subreddits)
│   ├── risk_management/       # Kelly + portfolio limits
│   ├── backtest/              # Backtesting framework
│   └── execution/             # Celery task stubs (not deployed)
├── tests/                     # Unit tests
├── deployment/                # Panic button, setup scripts
└── docs/                      # API docs, strategy guide
```

`docker-compose.yml`, `orchestrator/celery_app.py`, and the `tasks.py` modules
describe a queue-and-database architecture that is not part of the running
system. The deployed Advisor is a single FastAPI process using JSON files in
`data/` for state.

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=agents --cov-report=term-missing
```

## Configuration

All configuration via environment variables. See `.env.example`.

## Disclaimers

- **Not Financial Advice**: This is a decision-support tool only
- **Substantial Risk**: Options trading can result in total loss of capital
- **No Guarantees**: Past performance does not guarantee future results
- **IBKR Compliance**: Comply with Interactive Brokers API terms of service
- **Paper First**: Minimum 3 months paper trading before live deployment

## License

MIT License
