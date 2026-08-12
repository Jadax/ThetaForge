"""Macro-event calendar (agents/trade_engine/macro_calendar.py).

All expectations use explicit ``today`` dates so the suite never depends on
the wall clock. The embedded schedule is the verified 2026-08-13 snapshot:
FOMC decision days 2026+2027, CPI 2026 (2027 intentionally absent — BLS has
not published it), NFP by the standing first-Friday rule.
"""
from datetime import date

import pytest

from agents.trade_engine.macro_calendar import (
    MACRO_BLACKOUT_DAYS,
    cpi_dates,
    fomc_dates,
    macro_blackout,
    macro_days_until,
    nfp_dates,
    next_macro_event,
)


# ── embedded schedule sanity ───────────────────────────────────────────────

def test_fomc_schedule_embeds_both_years():
    assert date(2026, 7, 29) in fomc_dates()
    assert date(2026, 12, 9) in fomc_dates()
    assert date(2027, 1, 27) in fomc_dates()
    assert date(2027, 12, 8) in fomc_dates()
    # 8 meetings per year.
    assert len([d for d in fomc_dates() if d.year == 2026]) == 8
    assert len([d for d in fomc_dates() if d.year == 2027]) == 8


def test_cpi_schedule_is_2026_only_and_fails_open_in_2027():
    assert date(2026, 8, 12) in cpi_dates()
    assert len([d for d in cpi_dates() if d.year == 2026]) == 12
    # 2027 is not yet scheduled by BLS — must NOT be fabricated from a pattern.
    assert all(d.year == 2026 for d in cpi_dates())


def test_nfp_rule_reproduces_verified_2026_dates():
    for expected in (
        date(2026, 8, 7), date(2026, 9, 4), date(2026, 10, 2),
        date(2026, 11, 6), date(2026, 12, 4),
    ):
        assert expected in nfp_dates()


def test_nfp_january_releases_in_second_week():
    # BLS releases the January Employment Situation in the second week, not
    # on the first Friday (Jan 2, 2026 would be too tight after the holidays).
    assert date(2026, 1, 9) in nfp_dates()


def test_nfp_july_shifts_past_observed_holiday():
    # Jul 4, 2026 is a Saturday, observed Friday Jul 3 — the release moves to
    # the following Friday (Jul 10) instead of the first Friday.
    assert date(2026, 7, 3) not in nfp_dates()
    assert date(2026, 7, 10) in nfp_dates()


# ── next-event lookups ─────────────────────────────────────────────────────

def test_next_event_from_2026_08_13_is_nfp_sep_04():
    event = next_macro_event(date(2026, 8, 13))
    assert event["kind"] == "NFP"
    assert event["date"] == "2026-09-04"
    assert event["days_until"] == 22


def test_days_until_counts_to_the_event_day():
    assert macro_days_until(date(2026, 7, 26)) == 3  # FOMC Jul 29
    assert macro_days_until(date(2026, 7, 29)) == 0  # event day


def test_past_events_are_never_reported():
    # CPI Aug 12, 2026 is in the past from Aug 13 — the next event must be NFP.
    event = next_macro_event(date(2026, 8, 13))
    assert event["kind"] == "NFP"
    assert event["days_until"] > 0


def test_schedule_exhaustion_fails_open():
    # Past the last embedded FOMC decision (Dec 8, 2027) there is no data,
    # and the calendar must report None rather than guess forward.
    assert next_macro_event(date(2027, 12, 31)) is None
    assert macro_days_until(date(2027, 12, 31)) is None


def test_no_cpi_guess_in_2027():
    # CPI 2027 is not published; mid-February 2027 the next known events are
    # NFP (Feb 5) and FOMC (Mar 17) — never a fabricated CPI.
    event = next_macro_event(date(2027, 2, 1))
    assert event["kind"] != "CPI"


# ── blackout window ────────────────────────────────────────────────────────

def test_blackout_inside_window():
    read = macro_blackout(date(2026, 7, 26))
    assert read["in_blackout"] is True
    assert read["days_until"] == 3
    assert read["next_event"]["kind"] == "FOMC"
    assert "macro" in read["reason"].lower()


def test_blackout_on_event_day():
    read = macro_blackout(date(2026, 8, 12))  # CPI day
    assert read["in_blackout"] is True
    assert read["days_until"] == 0
    assert read["next_event"]["kind"] == "CPI"


def test_blackout_outside_window():
    read = macro_blackout(date(2026, 7, 22))  # 7 days before the FOMC
    assert read["in_blackout"] is False
    assert read["days_until"] == 7


def test_blackout_with_exhausted_schedule_never_vetoes():
    read = macro_blackout(date(2027, 12, 31))
    assert read["in_blackout"] is False
    assert read["next_event"] is None


def test_blackout_threshold_constant():
    assert MACRO_BLACKOUT_DAYS == 4
