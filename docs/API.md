# ThetaForge API Documentation

## Base URL

```
http://localhost:8000
```

## Health Check

### GET /health/

Check orchestrator health status.

**Response:**
```json
{
  "status": "healthy",
  "service": "thetaforge-orchestrator"
}
```

## Strategies

### GET /strategies/

List all available strategies.

**Response:**
```json
{
  "strategies": [
    "wheel",
    "vertical_spreads",
    "iron_condor",
    "credit_spread",
    "covered_call",
    "earnings_straddle",
    "gamma_blast"
  ]
}
```

### POST /strategies/configure

Configure a strategy's parameters.

**Request:**
```json
{
  "name": "wheel",
  "enabled": true,
  "allocation_pct": 40.0
}
```

**Response:**
```json
{
  "message": "Strategy wheel configured.",
  "config": {
    "name": "wheel",
    "enabled": true,
    "allocation_pct": 40.0
  }
}
```

## Positions

### GET /positions/

Get current IBKR positions.

**Response:**
```json
{
  "positions": [],
  "message": "Positions fetched from IBKR."
}
```

### GET /positions/greeks

Get aggregated portfolio Greeks.

**Response:**
```json
{
  "delta": 0.0,
  "gamma": 0.0,
  "theta": 0.0,
  "vega": 0.0
}
```

## Trading mode

ThetaForge is paper-only. The hosted Advisor cannot enable live execution;
the local Bridge accepts only explicitly confirmed paper orders through a
paper TWS / IB Gateway session.

## Error Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 403 | Attempted live trading activation (not supported) |
| 500 | Internal server error |

## WebSocket (Planned)

Real-time updates for positions, P&L, and alerts will be available via WebSocket at `/ws`.

## Alerts and signal performance

The advisor API can persist alert rules and maintain an auditable record of
Brain predictions. These features are informational only and do not place
orders.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/advisor/alerts` | List alert rules, optionally with `?symbol=AAPL` |
| `POST /api/advisor/alerts` | Create an alert rule (`symbol`, `alert_type`, `threshold`) |
| `POST /api/advisor/alerts/check` | Evaluate saved rules against supplied market data |
| `DELETE /api/advisor/alerts/{rule_id}` | Delete an alert rule |
| `GET /api/advisor/alerts/history` | Retrieve triggered alert history |
| `GET /api/advisor/signals/performance` | Review recorded signal accuracy and derived weights |
| `POST /api/advisor/signals/outcomes` | Record a current price for due prediction outcomes |
