# ThetaForge

**Multi-agent, AI-augmented options trading intelligence system.**
**100% FREE - Zero paid API subscriptions required.**

---

> **CRITICAL WARNING**: This system CANNOT and WILL NOT guarantee winning trades. Options trading involves substantial risk of loss. This is a probabilistic decision-support and execution tool. Past performance does not guarantee future results. Never invest money you cannot afford to lose.

---

## What Makes ThetaForge Different

- **Zero API Costs**: Uses only free data sources (IBKR, Alpaca, yfinance, Reddit, CBOE)
- **6-Layer Scanner Pipeline**: Flow → Dark Pool → GEX → Technical → Catalyst → Risk
- **13 Proven Strategies**: All with documented win rates and entry/exit rules
- **GEX/Dealer Positioning**: Know where dealers are positioned (free GEX calculation)
- **Dark Pool Detection**: Institutional activity detection without paid data
- **Reddit Sentiment**: Community sentiment from r/thetagang, r/options, r/wallstreetbets
- **Paper First**: Default to paper trading, multi-factor live activation

## Free Data Sources

| Source | Data | Cost |
|--------|------|------|
| IBKR API | Real-time options, Greeks, execution | Free with account |
| Alpaca Markets | Stocks, options (backup) | Free tier |
| yfinance | Historical data, option chains, VIX | Free |
| Reddit API | Community sentiment (8 subreddits) | Free |
| CBOE | Put/call ratio, VIX data | Free |
| FINRA | Dark pool volume data (weekly) | Free |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/yourusername/thetaforge.git
cd thetaforge

# 2. Configure
cp .env.example .env
# Edit .env with your IBKR credentials (paper trading)

# 3. Install
pip install -r requirements.txt

# 4. Start
docker-compose up -d
# Or individually:
uvicorn orchestrator.main:app --reload --port 8000
celery -A orchestrator.celery_app worker --loglevel=info
```

## 6-Layer Scanner Pipeline

```
7,000 symbols → Layer 1: Flow (~200 signals)
              → Layer 2: Dark Pool (~80 confirmed)
              → Layer 3: GEX (~40 aligned)
              → Layer 4: Technical (~25 confirmed)
              → Layer 5: Catalyst (~15 cleared)
              → Layer 6: Risk (~8-10 final setups)
```

Each layer progressively filters to higher-conviction setups. Raw flow signals alone are noisy. Adding dark pool activity, GEX context, technical confirmation, and catalyst checks dramatically improves signal quality.

## Strategy Library

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
| LLM Failures | 3 consecutive | Shutdown LLM path |
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
3. Set port 4001 (Paper) or 4002 (Live)
4. Enable "Accept inbound connections"
5. Copy `.env.example` to `.env` and configure

### Live Trading Activation
Live trading requires multi-factor authentication:
1. Set `LIVE_ACTIVATION_PIN` in `.env`
2. POST to `/admin/toggle-live` with your PIN
3. Physically switch IBKR to port 4002
4. Hardware switch strongly recommended

## Project Structure

```
thetaforge/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── architecture_diagram.mermaid
├── orchestrator/              # FastAPI + Celery
├── agents/
│   ├── data_ingestion/        # IBKR, yfinance, Alpaca
│   ├── scanner/               # Go concurrent scanner
│   ├── volatility/            # IV Rank/Percentile, GEX, Greeks
│   ├── flow_analysis/         # Unusual activity, dark pool, GEX engine
│   ├── technical/             # RSI, MACD, Bollinger, trend
│   ├── strategies/            # 13 strategy implementations
│   ├── sentiment/             # Reddit sentiment (8 subreddits)
│   ├── llm_reasoning/         # LLM + circuit breaker
│   ├── risk_management/       # Kelly + portfolio limits
│   ├── execution/             # Order management
│   ├── performance/           # Kinfo-style PnL tracking
│   ├── backtest/              # Backtesting framework
│   └── alerts/                # Discord/Telegram/Slack
├── models/                    # Pydantic schemas
├── tests/                     # Unit tests
├── deployment/                # Panic button, setup scripts
└── docs/                      # API docs, strategy guide
```

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
