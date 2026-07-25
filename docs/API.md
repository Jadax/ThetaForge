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

## Admin

### POST /admin/toggle-live

Toggle between paper and live trading. Requires PIN authentication.

**Request:**
```json
{
  "pin": "123456",
  "enable_live": true
}
```

**Response (success):**
```json
{
  "message": "Trading mode set to LIVE.",
  "warning": "Ensure hardware switch is in correct position."
}
```

**Response (failure):**
```json
{
  "detail": "Invalid PIN."
}
```

## Error Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 403 | Invalid PIN for live trading toggle |
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
