"""Scheduled macro-event calendar for the trade gates (FOMC / CPI / NFP).

Market-wide veto data, deliberately free and offline: the FOMC and BLS
schedules are public government calendars, so we embed them instead of paying
for an economic calendar feed. A macro print (a rate decision, a CPI release,
or the Employment Situation) is the single largest overnight-volatility event
in the week it lands — the Brain refuses new short premium into the window and
the trade manager closes existing short-vega positions before it.

Date sourcing (verified 2026-08-13):

  * FOMC decision days — the second day of each meeting, when the rate decision
    and SEP are released. The Fed publishes its schedule ~2 years ahead, so
    2026 and 2027 are embedded exactly. ``fomc_dates()`` is the source for the
    list; refresh it when the Fed extends the horizon.
  * CPI release days — BLS publishes the next year's schedule each fall.
    2026 is embedded exactly. 2027 has NOT been published yet, so it is
    deliberately absent and the calendar fails OPEN for CPI until it is
    (never fabricate a date from last year's pattern — the CPI calendar is
    hand-scheduled and shifts every year).
  * NFP (Employment Situation) — released on the first Friday of the month,
    with two standing exceptions that make the rule more accurate than a
    hardcoded list: January is released in the second week (data processing
    lag after the holidays), and a release Friday that falls on an observed
    federal holiday shifts to the following Friday. These are computed by rule
    for 2026-2027; if BLS's official schedule ever diverges from the rule,
    that schedule wins — add the exception here.

Fail-open is the standing policy for anything not yet scheduled: a missing
event must disable the macro gate, never invent one (mirrors how the rest of
the pipeline treats unavailable data).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

# How many calendar days before a scheduled macro event the gates act:
# the Brain refuses new short premium and the manager exits open short-vega
# positions. 4 days = the Friday before a mid-week print.
MACRO_BLACKOUT_DAYS = 4

# ── FOMC decision days (second day of each meeting) ────────────────────────
_FOMC_2026 = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
]
_FOMC_2027 = [
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
]

# ── CPI release days (BLS Consumer Price Index schedule, 2026) ─────────────
_CPI_2026 = [
    date(2026, 1, 13), date(2026, 2, 13), date(2026, 3, 11), date(2026, 4, 10),
    date(2026, 5, 12), date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12),
    date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 10), date(2026, 12, 10),
]

_EVENT_LABELS = {
    "FOMC": "FOMC rate decision",
    "CPI": "CPI report",
    "NFP": "Employment situation (NFP)",
}


def _first_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # weekday(): Mon=0 ... Fri=4
    return first + timedelta(days=offset)


def _observed_independence_day(year: int) -> date:
    """The day Independence Day (Jul 4) is observed by the federal government."""
    july_4 = date(year, 7, 4)
    if july_4.weekday() == 5:  # Saturday → observed Friday
        return july_4 - timedelta(days=1)
    if july_4.weekday() == 6:  # Sunday → observed Monday
        return july_4 + timedelta(days=1)
    return july_4


def _nfp_dates(year: int) -> List[date]:
    """Rule-based Employment Situation release days for a year.

    First Friday of the month, except: January moves to the second Friday
    (BLS's standing data-processing lag), and a release Friday falling on an
    observed federal holiday shifts to the following Friday.
    """
    dates = []
    for month in range(1, 13):
        release = _first_friday(year, month)
        if month == 1:
            release += timedelta(days=7)
        if release == _observed_independence_day(year):
            release += timedelta(days=7)
        dates.append(release)
    return dates


def fomc_dates() -> List[date]:
    return list(_FOMC_2026) + list(_FOMC_2027)


def cpi_dates() -> List[date]:
    # 2027 is not yet scheduled by BLS; intentionally absent (fail-open).
    return list(_CPI_2026)


def nfp_dates() -> List[date]:
    return _nfp_dates(2026) + _nfp_dates(2027)


def _events() -> List[Tuple[date, str]]:
    raw = (
        [(day, "FOMC") for day in fomc_dates()]
        + [(day, "CPI") for day in cpi_dates()]
        + [(day, "NFP") for day in nfp_dates()]
    )
    return sorted(raw)


def next_macro_event(today: Optional[date] = None) -> Optional[Dict]:
    """Next scheduled macro event on/after ``today``, or None when the
    embedded schedule is exhausted (never guesses forward)."""
    today = today or date.today()
    for day, kind in _events():
        if day >= today:
            return {
                "kind": kind,
                "label": _EVENT_LABELS[kind],
                "date": day.isoformat(),
                "days_until": (day - today).days,
            }
    return None


def macro_days_until(today: Optional[date] = None) -> Optional[int]:
    """Calendar days until the next macro event (0 = the event is today).

    None means no known future event — callers must fail open (no veto).
    """
    event = next_macro_event(today)
    return event["days_until"] if event else None


def macro_blackout(
    today: Optional[date] = None,
    threshold_days: int = MACRO_BLACKOUT_DAYS,
) -> Dict:
    """Whether we are inside a macro blackout window.

    Returns a dict the gates can pass straight through to reasoning/reason
    codes; ``in_blackout`` is False whenever the schedule is unknown, so a
    missing calendar can never fabricate a veto.
    """
    today = today or date.today()
    event = next_macro_event(today)
    if event is None:
        return {
            "in_blackout": False,
            "days_until": None,
            "next_event": None,
            "reason": "No known scheduled macro event",
        }
    days = event["days_until"]
    in_blackout = 0 <= days <= threshold_days
    reason = (
        f"Macro event ({event['label']}) in {days} day{'s' if days != 1 else ''} "
        f"on {event['date']} — no short premium through the print"
        if in_blackout
        else f"Next macro event ({event['label']}) in {days} days — outside the "
        f"{threshold_days}-day blackout"
    )
    return {
        "in_blackout": in_blackout,
        "days_until": days,
        "next_event": event,
        "reason": reason,
    }
