# ThetaForge Strategy Guide
## Complete Reference with Documented Win Rates

> All strategies are risk-defined. No undefined risk positions ever.

---

## Strategy Performance Reference

| # | Strategy | Typical Win Rate | Avg Profit/Trade | Max Loss | Difficulty | Best Market | Best IV |
|---|----------|-----------------|------------------|----------|------------|-------------|---------|
| 1 | Bull Put Credit Spreads | 65-80% | 50% of credit | Width - credit | Medium | Bullish/Sideways | High (>40 IVR) |
| 2 | Bear Call Credit Spreads | 65-80% | 50% of credit | Width - credit | Medium | Bearish/Sideways | High (>40 IVR) |
| 3 | Cash-Secured Puts | 70-85% | Premium collected | Stock cost - premium | Easy | Bullish/Neutral | High (>40 IVR) |
| 4 | LEAPS | 40-55% | 100-300% | Total debit | Easy | Long-term bull | Low (<30 IVR) |
| 5 | Covered Calls | 75-90% | Premium collected | Stock decline | Easy | Neutral | Any |
| 6 | Iron Condors | 65-80% | 50% of credit | Width - credit | Hard | Sideways | High (>50 IVR) |
| 7 | Long Calls | 35-45% | 100-500% | Total debit | Easy | Strong uptrend | Low (<25 IVR) |
| 8 | Long Puts | 35-45% | 100-500% | Total debit | Easy | Strong downtrend | Low (<25 IVR) |
| 9 | The Wheel | 70-85% | Premium + dividends | Stock decline | Easy | Bullish/Neutral | High (>40 IVR) |
| 10 | Call Debit Spreads | 45-55% | Spread width - debit | Total debit | Easy | Trending up | Low (<30 IVR) |
| 11 | Calendar Spreads | 55-65% | 50-100% of debit | Total debit | Medium | Low IV → High IV | Low entry |
| 12 | Butterfly Spreads | 60-75% | 3:1 to 5:1 reward | Total debit | Hard | Range-bound | Low (<30 IVR) |
| 13 | 0DTE Plays | 30-40% | 100-300% | Total debit | Expert | Catalyst/expansion | Low VIX |

**Key insight**: Higher win rate strategies (credit spreads, covered calls) have lower max profit. Lower win rate strategies (long options, 0DTE) have higher max profit potential. The edge comes from position sizing and management, not win rate alone.

---

## Strategy 1: Bull Put Credit Spreads
**Win Rate: 65-80% | Difficulty: Medium | Best: Bullish/Sideways | IVR > 40**

### Setup
- Sell OTM put + Buy further OTM put (same expiry)
- Short strike: 15-20 delta (~80-85% probability of profit)
- Width: $2-5 wide (risk-defined)

### Entry Rules
- IV Rank > 40 (higher = more premium = better)
- Underlying in uptrend or neutral (SMA20 > SMA50)
- 30-45 DTE (optimal theta decay)
- No earnings within 7 days
- Net credit >= 1/3 of spread width

### Management
- **Close at 50% profit** (single most impactful rule)
- Stop loss: 2x credit received
- Close if DTE < 7 (gamma risk)
- Roll up if underlying approaches short strike

### Example
- SPY at $500, sell $480 put / buy $475 put
- Width = $5, credit received = $1.50
- Max profit = $1.50 per share = $150
- Max loss = $5 - $1.50 = $3.50 per share = $350
- Probability of profit: ~75%

---

## Strategy 2: Cash-Secured Puts
**Win Rate: 70-85% | Difficulty: Easy | Best: Bullish/Neutral | IVR > 40**

### Setup
- Sell OTM put, cash secured at strike price
- Target stocks you want to own at a discount

### Entry Rules
- IV Rank > 40
- Stock in uptrend or neutral
- 30-45 DTE
- Strike: 20-30 delta (~70-80% probability of profit)
- Stock must be one you'd own at the strike price

### Management
- **Close at 50% profit**
- If assigned: sell covered calls (Wheel phase)
- Exit if stock drops >20% from strike
- Never sell CSPs on stocks you don't want to own

### Example
- AAPL at $190, sell $180 put for $3.00
- Max profit = $300 per contract
- Break-even = $177
- If assigned, own AAPL at effective cost basis of $177

---

## Strategy 3: Covered Calls
**Win Rate: 75-90% | Difficulty: Easy | Best: Neutral | Any IV**

### Setup
- Own 100+ shares, sell OTM call against position
- Generates income on existing stock holdings

### Entry Rules
- Must own at least 100 shares
- Strike: 3-5% OTM (30 delta)
- 30 DTE
- Higher IV = more premium = better

### Management
- **Close at 50% profit** or let expire worthless
- Roll up and out if stock rallies through strike
- If assigned: resell CSP to restart Wheel
- Never sell covered call below cost basis

---

## Strategy 4: Iron Condors
**Win Rate: 65-80% | Difficulty: Hard | Best: Sideways | IVR > 50**

### Setup
- Sell OTM put spread + Sell OTM call spread
- Collect premium from both sides
- 4-leg strategy with defined risk

### Entry Rules
- IV Rank > 50 (need premium)
- VIX between 15-25 (sweet spot)
- Underlying range-bound (technical confirmation)
- 30-45 DTE
- Short strikes: 10-15 delta

### Width Selection
- $5 wide on SPY/QQQ ($500 max risk per spread)
- Wider wings = more credit but more risk
- Narrower wings = less credit but higher win rate

### Management
- **Close at 50% profit** (most important rule)
- Close if either short strike is threatened
- Close if DTE < 14 (gamma risk accelerates)
- Never hold through expiration week
- Adjust: roll untested side closer if one side threatened

---

## Strategy 5: Long Calls/Puts
**Win Rate: 35-45% | Difficulty: Easy | Best: Strong Trends | Low IVR**

### Setup
- Buy OTM or ATM call/put for directional exposure
- Low IV = cheaper options = better risk/reward

### Entry Rules
- IV Rank < 25 (buy cheap options)
- Strong trend confirmation (SMA alignment)
- 30-60 DTE (time to work)
- Delta: 0.50-0.60 (Goldilocks zone)

### Management
- **Take profit at 50-100% gain** (don't get greedy)
- Stop loss: 50% of premium paid
- Trail stop after 2x profit reached
- Never hold through expiry week

---

## Strategy 6: The Wheel
**Win Rate: 70-85% | Difficulty: Easy | Best: Bullish/Neutral | IVR > 40**

### Setup
- CSP → Assignment → Covered Call → Repeat
- Default allocation: SPY 40%, QQQ 30%, TLT 20%, individual 10%

### Entry Rules (CSP Phase)
- IV Rank > 75 (sell when IV is expensive)
- 25 delta (~75% probability of profit)
- 30-45 DTE
- Stock must be one you want to own

### Entry Rules (Covered Call Phase)
- After assignment, sell ATM or slightly OTM call
- 30 DTE
- Close at 50% profit

### Management
- Close CSP at 50% profit
- If assigned: immediately sell covered call
- Exit cycle if stock drops >20% from CSP strike
- VIX call hedge: buy VIX calls when VIX < 15

---

## Strategy 7: Call Debit Spreads
**Win Rate: 45-55% | Difficulty: Easy | Best: Trending Up | Low IVR**

### Setup
- Buy ATM call + Sell OTM call
- Defined risk, defined reward

### Entry Rules
- IV Rank < 30 (low IV = cheap debit)
- Strong uptrend (SMA20 > SMA50 > SMA200)
- Short strike: 5-10% above current price
- 30-45 DTE

### Management
- **Close at 75% of max profit**
- Stop loss: 50% of debit paid
- Close if trend breaks (SMA20 crosses below SMA50)

---

## Strategy 8: LEAPS
**Win Rate: 40-55% | Difficulty: Easy | Best: Long-term Investing | Low IVR**

### Setup
- Deep ITM call (0.80+ delta) as stock replacement
- 18-24 month expiry (time decay minimal)
- Use 80/80 rule: 80% intrinsic value, 80 DTE or longer

### Entry Rules
- Underlying with strong fundamentals
- IV Rank < 30 (cheaper premium)
- Delta >= 0.80 (behave like stock)
- 400+ DTE (minimize theta)

### Management
- Hold for 6-12 months minimum
- Can sell covered calls against LEAPS (PMCC)
- Exit if fundamental thesis changes
- Roll at 60 DTE to avoid gamma risk

---

## Strategy 9: Calendar Spreads
**Win Rate: 55-65% | Difficulty: Medium | Best: Low IV → High IV**

### Setup
- Buy longer-dated option + Sell shorter-dated option (same strike)
- Profits from IV increase and time decay differential

### Entry Rules
- IV Rank < 30 (enter when IV is low)
- VIX term structure: contango (normal)
- Expect IV to increase (earnings, events)
- Strike: ATM (maximum vega exposure)

### Management
- Close short leg at 50% profit
- Roll short leg to next month
- Close entire position when IV peaks

---

## Strategy 10: Butterfly Spreads
**Win Rate: 60-75% | Difficulty: Hard | Best: Range-Bound | Low IVR**

### Setup
- Buy 1 ATM call + Sell 2 OTM calls + Buy 1 further OTM call
- Max profit at the middle strike at expiry
- 3:1 to 5:1 reward-to-risk ratio

### Entry Rules
- IV Rank < 30
- Expect underlying to pin near target price
- 30-45 DTE
- Center strike at expected pin level

### Management
- **Close before 5-7 DTE** (gamma risk)
- Close at 50% of max profit
- Width determines risk/reward

---

## VIX Regime Strategy Selection

| VIX Level | Regime | Best Strategies | Avoid |
|-----------|--------|----------------|-------|
| < 12 | Very Low | LEAPS, Call/Put Debit, Long Options | Credit spreads (low premium) |
| 12-15 | Low | LEAPS, Calendar Spreads, Debit | Iron Condors (low premium) |
| 15-20 | Normal | All strategies viable | None |
| 20-25 | Elevated | Credit Spreads, Iron Condors, Wheel | Long options (overpriced) |
| 25-30 | High | Credit Spreads, Iron Condors, CSP | Long options, LEAPS |
| > 30 | Extreme | Sell premium aggressively | Buying premium |

---

## Multi-Layer Scanner Workflow

The system filters candidates through 6 layers:

```
Layer 1: Flow Analysis     → Unusual volume/premium (Vol/OI >= 2.0, Premium >= $25K)
Layer 2: Dark Pool          → Institutional confirmation (volume anomaly or strong flow)
Layer 3: GEX/Dealer         → Gamma exposure alignment (favorable regime)
Layer 4: Technical          → Price trend confirmation (SMA, RSI, MACD alignment)
Layer 5: Catalyst           → Earnings/macro event check (skip if <3 days)
Layer 6: Risk Management    → Position sizing, risk/reward, confidence (final filter)
```

Typical pipeline: 7000 symbols → ~200 flow signals → ~80 dark pool confirmed → ~40 GEX aligned → ~25 technically confirmed → ~15 catalyst-cleared → ~8-10 final high-conviction setups

---

## Position Sizing Framework

1. **Max risk per trade**: 2% of portfolio
2. **Half-Kelly sizing**: f = (p * b - q) / (2 * b)
3. **Max portfolio delta**: 20% net
4. **Max portfolio vega**: 5% net
5. **Correlation limit**: Max 20% in any single sector

---

## Disclaimer

Past performance does not guarantee future results. Win rates are approximate ranges based on backtesting and practitioner reports. Real-world results vary based on market conditions, execution quality, and position management. Options trading involves substantial risk of loss.
