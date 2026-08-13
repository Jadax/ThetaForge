# Equity / General Trader Research

Research that shaped the equity (stock + ETF) engine. Sources are public,
free/backtested strategy write-ups and community process guidance. Everything
here is a feature/filter/metric **idea to steal**, then adapted to the same
safety rails as the options engine: free data, fail-closed gates, single
Bridge order path, single ledger.

## 1. Momentum & trend rules (steal for the equity brain + scanner)

Sources: QuantifiedStrategies (Donchian/trend), BacktestMe momentum guide,
tradingsim trend trading, DigitalNinjas backtesting guide, Mark Crisp momentum
notes.

- **Regime filter first.** Trend following only works with the market's trend:
  trade longs only when the broad tape is up (SPY/QQQ above their 200-day or
  a breadth gauge is risk_on). Steal: reuse `MarketOverview._risk_tilt()` from
  `agents/general_trader/market_overview.py` as a market-regime veto.
- **Cross-sectional / relative strength.** Rotate into the strongest names:
  rank a candidate set by 1m/3m/6m return; the top RS names with an
  absolute-trend filter (price > SMA200, SMA50 > SMA200) are the buy set.
  Steal the `relative_strength_ok` concept from `agents/trade_engine/high_winrate.py`
  but apply it as the *entry* criterion for equities, not just a veto.
- **Breakout momentum.** Entry: price closes above the 20-day high, RSI > 55
  (momentum confirmed, not exhausted), volume >= 1.3x the 20-day average.
  Stop below the breakout bar low or 2 ATR below entry.
- **Pullback momentum.** In an uptrend (rising 50-EMA) wait for RSI to pull
  to 40-50, then re-enter; bullish engulfing + volume tick confirms.
- **ADX filter.** Require ADX >= 20 (ideally >= 25) so capital only goes into
  established trends; reject chop. Direction from SMA/EMA stack.
- **Avoid overbought chases.** RSI > 80 is a warning on momentum longs (room
  to run in 55-70, chase warning > 80). No reversal-of-indicator entries.

## 2. Exit / risk management (steal for equity management rules)

Sources: QuantifiedStrategies, QuantVPS, disciplina.ai ATR guide, TruthAlpha,
StockSetups, FXGlory ATR trailing.

- **ATR stop.** Stop = entry - (ATR(14) x multiplier). Multiplier ~2.0-2.5 for
  swing/trend holds; 1.5-2.0 for tighter. This is the primary position-risk
  definition and what the Bridge will use to reserve capital.
- **ATR position sizing.** Risk per trade = 1% of account equity. Shares =
  floor(risk_per_trade / (ATR x multiplier)). This equalizes dollar risk
  across volatile and calm names.
- **Trailing stop.** Once the position is +1R (one initial risk) in profit,
  trail the stop by 2 ATR below the highest high since entry (chandelier).
  Stop only ratchets in the trade's direction, never backward.
- **Take-profit / time exit.** Fixed 2R target OR time exit (e.g., exit after
  N sessions without progress). "Exit rules matter more than entry rules" is
  the recurring lesson.
- **Event risk.** Around earnings/FOMC: cut size in half, widen stop, or stand
  aside; gap risk can blow through any stop. Equity manager must skip or
  de-risk positions entering an earnings/macro window (mirror
  `close_pre_earnings` / `close_pre_macro` from `trade_manager.py`).
- **Hard risk limits.** Daily loss limit (~3%) and weekly loss limit (~5-6%).
  Steal as portfolio-level brakes in the equity manager.

## 3. Portfolio construction / rotation (steal for universe + recommender)

Sources: Dual/GEM momentum (Antonacci), cross-sectional momentum write-ups.

- **Dual momentum.** Absolute momentum filter (price above its 200-day or
  positive 12-month return) gates the whole book; relative momentum
  (rank by 6m return) picks names; keep the top N. Simple, robust, and
  cheap to run on free daily OHLCV.
- **Rebalance / time in market.** Weekly or monthly rotation cadence reduces
  turnover vs chasing every breakout. Equity scanner emits candidates daily,
  but recommenders de-duplicate to one entry per symbol with a cooldown.
- **Sector diversification.** Cap positions per sector (reuse the
  `MAX_CORRELATED_POSITIONS` + `SYMBOL_SECTOR` idea from `recommender.py`)
  so a single sector meltdown can't sink the book.

## 4. Community process discipline (steal for executor cadence)

Source: trademomentum.org best day-trading communities 2026; r/RealDayTrading.

- **Market-open only.** Trade during the real NYSE session only, using the
  same source of truth as the options engine (Advisor `/scanner/status`
  market_open flag / market-hours supervisor).
- **Nightly watchlist, mechanical execution.** Prepare the candidate list
  after close (scanner writes persistent notifications), execute mechanically
  at open. This is exactly the existing VM auto-executor + background
  scanner pattern; the equity scanner mirrors it.
- **Process over prediction.** Every rejected idea is logged with a reason
  code (fail-closed), same as the options scanner's skip reasons.

## 5. Dashboard / analytics metrics worth stealing

Sources: ivan-pichugin/trading-performance-analyzer; premium platforms
(Finviz-style screeners, Composer-style strategy backtests, TradingView
overlays, IBD-style market-direction reads).

- **Performance dashboard metrics:** Sharpe, Sortino, Calmar, win rate, avg
  win/avg loss, expectancy, profit factor, equity curve, rolling Sharpe,
  max drawdown, monthly heatmap. → expose for the equity book via the
  journal/recap pipeline (`scripts/recap.py`), computed per asset_class.
- **Screener columns (Finviz-style):** price, % change, volume ratio vs 20d
  avg, RSI, trend (above/below 50/200), % off 52w high, RS rank, ATR%.
  → the equity scanner's notification payload carries these.
- **Market-direction read (IBD/Finviz-style):** broad-market up/down + risk
  tilt as a single "confirmed uptrend / correction / confirmed downtrend"
  banner on the dashboard. → derived from `MarketOverview` risk_tilt.
- **Strategy backtest overlay:** equity curve + rolling win rate for the
  momentum rules, built on the same empirical-outcome machinery the options
  engine uses (fail-open empirical gate).

## Where each steal lands in this repo

| Stolen feature | Lands in |
| --- | --- |
| Regime/risk-tilt veto | `agents/equity_trader/equity_brain.py` (reads `MarketOverview._risk_tilt`) |
| Cross-sectional + dual momentum ranking | `agents/equity_trader/equity_signals.py`, `equity_universe.py` |
| Breakout + pullback entries, ADX/RSI filters | `agents/equity_trader/equity_brain.py` |
| ATR stop, ATR sizing, chandelier trail, 2R target, time exit | `agents/equity_trader/equity_recommender.py`, `equity_manager.py` |
| Event-risk de-risk (earnings/macro) | `equity_manager.py` (mirrors `trade_manager.py`) |
| Sector correlation cap | `equity_recommender.py` |
| Nightly watchlist + mechanical open execution | `deployment/vm_auto_executor.py` (+equity notifications) |
| Market-open gating | reused `is_market_hours` / supervisor |
| Performance metrics (Sharpe/Sortino/Calmar/heatmap) | `scripts/recap.py` per asset_class |
| Screener columns | equity scanner notification payload + dashboard |
| Fail-open empirical gate | `equity_recommender.py` (mirrors empirical gate) |

## Deliberately rejected (or deferred)

- **Shorting equities.** Short stock is undefined risk; the Bridge rejects
  shorts. If/wanted later that is a separate reviewed decision.
- **Options on equities as the "general" engine.** The general engine is
  buy/hold + trend trades on stock and liquid ETFs; the options engine keeps
  the premium-selling book. Two brains, two books, two journal sections.
- **Machine-learning price prediction.** Free-data daily OHLCV doesn't
  support it honestly; keep the rule-based, fail-closed approach.
