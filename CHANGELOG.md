# ThetaForge Changelog

## v0.5.4 — 2026-07-27

- Extended paper execution to every currently supported Advisor strategy:
  defined-risk spreads and condors, cash-secured puts, and covered calls.
- Cash-secured puts now require verified IBKR available funds; covered calls
  require verified ownership of at least 100 shares per contract. Naked short
  options continue to be rejected.

## v0.5.3 — 2026-07-27

- Added live-quote-gated paper order submission from each eligible trade card.
- The local IBKR Bridge now requotes every leg immediately before submitting a
  single combo limit order, rejects delayed/frozen quotes, and enforces the
  dashboard capital limit against live maximum loss.
- Automated execution is limited to defined-risk verticals and iron condors;
  uncovered and stock-dependent structures remain analysis-only.

## v0.5.2 — 2026-07-27

- Expanded every trade card into a decision view with maximum loss, maximum
  profit, probability of profit, capital required, credit/debit, breakeven,
  confidence, volatility context, and return-on-capital metrics.
- Added a compact defined-risk/reward visual and expandable entry/exit plan
  for each eligible paper-trading recommendation.

## v0.5.1 — 2026-07-27

- Corrected strategy scoring to use each underlying's actual volatility rank,
  realized volatility, technical regime, and market VIX context.
- Fixed neutral MACD normalization so it does not create a bearish bias.
- Enforced out-of-the-money strike geometry for defined-risk credit spreads.
- Retained the strict 70/100 composite-score eligibility floor across all
  supported strategy types.

## v0.5.0 — 2026-07-27

- Added the Advisor-selected stock workflow: the scanner chooses diversified
  underlyings first, and each stock opens its own filtered trade structures.
- Expanded market discovery with live active, gaining, losing, and growth-stock
  screeners, while retaining liquidity and options-chain validation.
- Corrected option-chain ingestion, actual DTE, strategy ranking, and
  multi-leg direction handling.
- Strengthened the Advisor Brain with regime-aware, event-aware, risk-defined
  strategy controls and paper-only execution guardrails.
- Added website-first local Bridge autostart for Windows.
