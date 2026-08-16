"""Strategy playbook library (tastytrade / Option Alpha / r-thetagang pattern).

Curated, honest playbooks for the strategies this engine actually evaluates —
each one ties its entry/management rules to the gates the Brain, Recommender,
and Trade Manager already enforce (IV rank, expected move, DTE bands, macro
blackout, earnings proximity). This is education, not advice: every playbook
carries the strategy's real risk profile so a reader understands what they'd
be signing up for. Never an order path.
"""
from __future__ import annotations

from typing import Dict, List

PLAYBOOKS: List[Dict] = [
    {
        "id": "bull_put_credit",
        "name": "Bull Put Credit Spread",
        "strategy_type": "credit_spread",
        "risk_profile": "defined-risk, bullish",
        "premium_printer": True,
        "mechanics": (
            "Sell an OTM put and buy a further-OTM put for protection, pocketing the "
            "net credit. Profit is capped at the credit received; loss is capped at "
            "(spread width - credit) x 100."
        ),
        "entry_rules": (
            "Sell below a support level (short delta ~20-30). This engine requires a "
            "bullish or neutral-consensus signal, IV rank elevated enough to pay "
            "premium, no macro blackout, no earnings inside the trade, and a "
            "structure whose credit exceeds its own Black-Scholes model value "
            "(theoretical edge)."
        ),
        "management": (
            "Take profit at 50% of max credit, defend before 21 DTE when gamma "
            "accelerates, and close before earnings or macro events. A 2x-credit "
            "stop caps a breach."
        ),
        "common_mistakes": (
            "Selling too close to spot for the credit, ignoring earnings, holding "
            "through a macro print, and sizing the short strike so the width barely "
            "covers the credit."
        ),
        "best_for": (
            "Slow grinders in a rising or range-bound market where the underlying's "
            "IV is rich vs its own history."
        ),
        "risk_warning": "Defined loss but capital-heavy: width x 100 x contracts is at risk."
    },
    {
        "id": "iron_condor",
        "name": "Iron Condor",
        "strategy_type": "credit_spread",
        "risk_profile": "defined-risk, direction-neutral",
        "premium_printer": True,
        "mechanics": (
            "Combine a bull put spread and a bear call spread at the same expiration. "
            "Both wings sell rich IV and the position profits if spot stays inside "
            "the inner strikes at expiry."
        ),
        "entry_rules": (
            "Both short strikes must sit outside the expected 1-sigma move to expiry, "
            "IV rank must be elevated so both sides pay, and the position must pass "
            "the engine's trend-alignment veto (no strong confirmed trend in either "
            "direction, else one wing carries all the risk)."
        ),
        "management": (
            "Manage the tested wing first (roll or close the breached side) at 21 DTE. "
            "Take profit at 50% of the combined credit. A 2x-credit stop on the tested "
            "wing limits damage."
        ),
        "common_mistakes": (
            "Selling wings inside the expected move, letting one side go unmanaged "
            "into a trend, and entering when VIX is spiking rather than rich-and-steady."
        ),
        "best_for": "Range-bound, high-IV regimes where the term structure is not inverted.",
        "risk_warning": "Two defined-risk wings, but the tested wing can pin near max loss if spot trends."
    },
    {
        "id": "bear_call_credit",
        "name": "Bear Call Credit Spread",
        "strategy_type": "credit_spread",
        "risk_profile": "defined-risk, bearish",
        "premium_printer": True,
        "mechanics": (
            "Sell an OTM call and buy a further-OTM call above it, collecting the net "
            "credit. Profit capped at the credit; loss capped at (width - credit) x 100."
        ),
        "entry_rules": (
            "Sell above resistance with a bearish/neutral-consensus signal, rich IV "
            "rank, no macro blackout, and no earnings inside the trade. The engine "
            "vetoes this when the 126-day trend is still strongly bullish."
        ),
        "management": "Same 50%-profit, 21-DTE, 2x-credit-stop rhythm as the bull put.",
        "common_mistakes": "Selling into an established uptrend and confusing a cheap credit with a good one.",
        "best_for": "Distribution or outright downtrends with rich implied vol.",
        "risk_warning": "Defined loss, but upside breaching the short strike turns acute quickly."
    },
    {
        "id": "wheel",
        "name": "The Wheel (CSP then Covered Call)",
        "strategy_type": "income_cycle",
        "risk_profile": "defined-risk until assigned; long stock after",
        "premium_printer": True,
        "mechanics": (
            "Stage 1: sell cash-secured puts on a stock you want to own. If assigned, "
            "stage 2: sell covered calls against the shares until the stock is called "
            "away. Each leg collects premium; the cycle repeats."
        ),
        "entry_rules": (
            "Only on names the engine's equity gates would approve for long stock "
            "(trend intact, no earnings in the window). Sell puts below support with "
            "a high IV rank so the premium justifies the assignment risk."
        ),
        "management": (
            "Roll the put before it goes deep ITM if you don't want the stock; if "
            "assigned, sell calls above cost basis and let them decay. Do not "
            "over-leverage the cash-securing side."
        ),
        "common_mistakes": (
            "Wheeling names you'd never actually own, selling CSPs into earnings, and "
            "letting the 'assignment into a falling knife' loop take over."
        ),
        "best_for": "Quality, liquid, high-IV names where 12 months of premium beats buy-and-hold.",
        "risk_warning": "Assignment means full long-stock risk; a crash converts premium income into capital loss."
    },
    {
        "id": "covered_call",
        "name": "Covered Call",
        "strategy_type": "income",
        "risk_profile": "long stock risk, capped upside",
        "premium_printer": True,
        "mechanics": (
            "Own 100 shares and sell one OTM call. You collect premium and cap your "
            "upside at the strike; you keep all the downside of the shares."
        ),
        "entry_rules": (
            "Sell calls when IV rank is elevated so the premium is real; pick a "
            "strike above the level you'd be happy to sell at. Avoid selling into "
            "earnings unless you want the assignment."
        ),
        "management": "Roll out-and-up when the short call approaches the money; close at 50% or 21 DTE.",
        "common_mistakes": "Treating it as 'free money' — it's a capped-upside long position.",
        "best_for": "Holders of strong, high-IV names who want income instead of exit.",
        "risk_warning": "Downside is 100% of the stock; the premium is a haircut, not a hedge."
    },
    {
        "id": "debit_spreads",
        "name": "Bull/Bear Debit Spreads",
        "strategy_type": "debit_spread",
        "risk_profile": "defined-risk, directional",
        "premium_printer": False,
        "mechanics": (
            "Buy an ITM/ATM option and sell an OTM option further out, paying a net "
            "debit. You control a cheap version of a bigger directional bet with a "
            "defined max loss equal to the debit."
        ),
        "entry_rules": (
            "The engine's edge flips here: a debit structure is attractive when its "
            "model value EXCEEDS the market price (you buy below fair). Enter on "
            "direction consensus with the expected move supporting the breakeven."
        ),
        "management": "Take profit when the spread captures most of its width; cut at 50% of the debit.",
        "common_mistakes": "Overpaying (buying rich), buying too much theta-heavy time, and ignoring IV crush after events.",
        "best_for": "Defined-risk directional plays when IV is cheap rather than rich.",
        "risk_warning": "Max loss is the full debit — it can go to zero even on a correct direction if time runs out."
    },
    {
        "id": "0dte",
        "name": "0DTE Short Verticals (high risk, research only)",
        "strategy_type": "credit_spread",
        "risk_profile": "extreme intraday gamma risk",
        "premium_printer": False,
        "mechanics": (
            "Selling a vertical that expires the same session. Premium per width is "
            "tiny, so the edge is pure gamma decay — and pure gamma risk the moment "
            "spot touches the short strike."
        ),
        "entry_rules": (
            "This engine does NOT recommend 0DTE as an entry strategy; the DTE band "
            "for premium selling is 21-45 days. Anything faster belongs in a "
            "specialized, tightly-monitored workflow with position sizes a fraction "
            "of a normal vertical."
        ),
        "management": "If used at all: exit by midday, never hold through the close, and accept that stops often gap.",
        "common_mistakes": "Oversizing for the tiny credit, holding into the close, and mistaking backtest fills for live ones.",
        "best_for": "None for most accounts; shown here only to document why it is excluded.",
        "risk_warning": "A 10-wide can still lose hundreds on a single bad minute; the credit is a few dollars."
    },
]


def get_playbook(playbook_id: str) -> Dict:
    """Return one playbook; unknown ids fail closed with a clear error."""
    for playbook in PLAYBOOKS:
        if playbook["id"] == playbook_id:
            return playbook
    return {"id": playbook_id, "error": f"unknown playbook: {playbook_id}", "found": False}


def list_playbooks() -> List[Dict]:
    """Summarized list for the dashboard's strategy library."""
    return [
        {
            "id": playbook["id"],
            "name": playbook["name"],
            "strategy_type": playbook["strategy_type"],
            "risk_profile": playbook["risk_profile"],
            "premium_printer": playbook["premium_printer"],
        }
        for playbook in PLAYBOOKS
    ]
