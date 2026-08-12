# ThetaForge Research Sources

Reference sites the ThetaForge signal work is informed by. This file catalogs
what each one is, what signal or method it informs, and whether it offers free
data. **Requirement (see `SIGNAL_POLICY.md`): none of these may be pulled into
the pipeline as a paid API. They are methodology references and free-data
benchmarks only.** Every feed ThetaForge consumes is free (CBOE delayed quotes,
yfinance, Yahoo screeners, the account's own IBKR feed).

Legend: **Signal** = what it contributes; **Data** = Free / Freemium / Paid.

## Options scanning & screeners

| Source | What it is | Signal it informs | Data |
| --- | --- | --- | --- |
| [thetagang.com](https://thetagang.com/) | Public options-income trade journal (open-interest dashboards, premium selling community) | The community-standard metrics for wheel/credit-spread hygiene: width-to-credit ratios, POP vs expected move, DTE bands; validates our "journal only real, placed trades" honesty rule | Free |
| [optionistics.com](https://www.optionistics.com/) | Options screener / quotes with IV stats per chain | IV percentile and IV/historical-vol ratio display patterns; cheap-vs-expensive option flags | Freemium |
| [optiondash.com](https://optiondash.com/) | Options analytics dashboard (Greeks, IV, chains) | Chain-level IV surface display and Greek read patterns | Freemium |
| [opscanbot.com](https://opscanbot.com/) | Automated options scanner (unusual volume, IV) | Unusual-volume / IV-spike screening workflow — our `flow_signals` (unusual volume, OI divergence) concept source | Freemium |
| [quantcha.com — Option Strategy Engine](https://quantcha.com/OSE#/Screeners) | Strategy-construction screener (probability-based) | Probability-of-profit-driven strategy selection; reinforces using POP/distribution math over guesswork in `recommender.py` | Freemium |
| [Unusual Whales — Options Profit Calculator](https://unusualwhales.com/options-profit-calculator) | Profit/loss calculator for option structures | P/L-zone presentation (max loss, max profit, breakeven) we mirror in the dashboard `ExpectedMoveChart` | Freemium |
| [OptionsProfitCalculator — Option Finder](https://www.optionsprofitcalculator.com/option-finder.html) | Free structure-finder (find trades from a given premium) | Credit-to-width / premium-per-day floors for verticals (used in our credit-spread gates) | Free |
| [OIC — Trending Options Volume](https://www.optionseducation.org/toolsoptionquotes/trending-options-volume) | CBOE/OIC free screen of trending option volume | Volume-momentum read — the "rising volume across an expiry" input behind unusual-volume detection | Free |
| [maxfort86/wsb — most-popular-options](https://github.com/maxfort86/wsb/blob/main/most-popular-options.php) | Open-source scraper of r/wallstreetbets's most-talked-about tickers | Sentiment/attention-discovery idea (crowd interest as a universe-expansion input). ThetaForge does not scrape Reddit; the concept informs the Yahoo most-actives screener we already use (`get_active_stock_universe`) | Free |

## Positioning / institutional flows

| Source | What it is | Signal it informs | Data |
| --- | --- | --- | --- |
| [Quiver Quantitative — Strategies](https://www.quiverquant.com/strategies/) | Political/congressional-trading & government-contract screens | Alternative-data screen ideas; demonstrates institutional-flow reads that could later be explored via free feeds | Freemium |

## High-win-rate entry & exit playbook

The trade-management rules in `agents/trade_engine/high_winrate.py` and
`agents/trade_engine/trade_manager.py` (50% take-profit, 21-DTE gamma rule,
2×-credit stop, 1-SD expected-move buffer, trend + relative-strength gates,
pre-earnings exits) are synthesized from this research:

| Source | What it is | Signal it informs | Data |
| --- | --- | --- | --- |
| [Gorilla Trades](https://www.gorillatrades.com/) | Technical stock screener with explicit stop-loss and profit-target levels on every pick | Stop-loss / profit-target pair per position → the "tested short strike → review" and take-profit rules in `trade_manager.py` | Paid |
| [StockCircle](https://www.stockcircle.com/) | Long-term value/analyst-estimate screen on public holdings | Fundamental quality context behind the technical trend read (the "why" a name trends) | Freemium |
| [StratX AI](https://stratxai.com/) | AI-assisted entry/exit timing and strategy backtests | Structured entry/exit discipline; reinforces explicit rule-ordered exits over discretionary closes | Freemium |
| [Clark Street Value](https://clarkstreetvalue.blogspot.com/) | Value-investing public journal (classic deep-value research) | Laggard-contrarian caution — the mirror image of IBD's "leaders only" RS rule we encode | Free |
| [Stratosphere](https://stratosphere.io/) | Institutional-style portfolio analytics | Risk-budgeting patterns (position caps, per-symbol slices) → `portfolio_plan` limits | Freemium |
| [Macroaxis — Investor](https://www.macroaxis.com/invest/home) | Cross-asset financial/risk analytics | Correlation & concentration hygiene (max correlated positions, max capital slice) | Freemium |
| [StockN Near](https://stocknear.com/) | Momentum / technical screener with social sentiment | Momentum-screening patterns that reinforce the RS + 50/200-day-MA gates | Freemium |
| [Trefis — Data](https://www.trefis.com/data/home?from=icon) | Company/fundamental data explorer | Fundamental confirmation layer for the technical trend read | Freemium |
| [AIOLux](https://aiolux.com/) | AI trading/analytics platform | Rule-execution workflow ideas (machine-checked trade plans) | Paid |
| [AltIndex](https://altindex.com/) | Alternative-data ranking of trending stocks | Alternative-data momentum — confirms using relative strength vs the broad market, not absolute price action | Freemium |
| [Investor's Business Daily](https://www.investors.com/) | CAN SLIM / relative-strength (RS) ratings, 50/200-day moving averages | The IBD "L" rule encoded in `relative_strength_ok`: trade only leaders (RS ≥ upper quartile, above 50/200-day MAs), and its "trade with the market" trend rule encoded in `trend_alignment_ok` | Paid |
| [Tastytrade research](https://www.tastylive.com/research) | The 200k-trade DTE & exit studies | 50%-of-credit take-profit vs hold-to-expiry; 21-DTE gamma acceleration; manage-closed rules in `trade_manager.py` | Free |
| [Cboe Options Institute](https://www.cboe.com/education/) | Options education: delta/POP, expected move, gamma | Expected-move strike buffer and ~68%-POP boundary in `high_winrate.py` | Free |
