"""Budget period arithmetic. Rulebook section 8.

Pure functions, so no database fixture. These pin the traps that make period math
fail silently: truncating division, Sunday-based weeks, and month-end ratcheting.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.periods import (
    CLOSED,
    FUTURE,
    OPEN,
    Period,
    days_remaining,
    elapsed_days,
    next_period,
    period_for,
    period_state,
    prev_period,
    reporting_month,
)
from app.models.enums import BudgetPeriod

D = BudgetPeriod.DAILY
W = BudgetPeriod.WEEKLY
F = BudgetPeriod.FORTNIGHTLY
M = BudgetPeriod.MONTHLY
Q = BudgetPeriod.QUARTERLY
Y = BudgetPeriod.ANNUAL


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period,d,anchor,start,end",
    [
        (D, date(2026, 8, 30), None, date(2026, 8, 30), date(2026, 8, 30)),
        # 2026-08-30 is a Sunday: the LAST day of an ISO week, not the first.
        (W, date(2026, 8, 30), None, date(2026, 8, 24), date(2026, 8, 30)),
        (W, date(2026, 8, 24), None, date(2026, 8, 24), date(2026, 8, 30)),
        (M, date(2026, 8, 15), None, date(2026, 8, 1), date(2026, 8, 31)),
        (M, date(2026, 2, 15), None, date(2026, 2, 1), date(2026, 2, 28)),
        (M, date(2024, 2, 15), None, date(2024, 2, 1), date(2024, 2, 29)),  # leap
        (Q, date(2026, 4, 5), None, date(2026, 4, 1), date(2026, 6, 30)),
        (Q, date(2026, 12, 31), None, date(2026, 10, 1), date(2026, 12, 31)),
        (Y, date(2026, 8, 30), None, date(2026, 1, 1), date(2026, 12, 31)),
        (F, date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 15)),
        (F, date(2026, 1, 15), date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 15)),
        (F, date(2026, 1, 16), date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 29)),
    ],
)
def test_period_resolution(period, d, anchor, start, end):
    p = period_for(period, d, anchor)
    assert (p.start, p.end) == (start, end)


def test_fortnightly_before_the_anchor_extends_backwards(session=None):
    """The anchor is an epoch, not a lower bound.

    int((d - anchor).days / 14) truncates toward zero and returns a period that
    does not contain d. Floor division gives k=-1 and the correct interval.
    """
    p = period_for(F, date(2026, 1, 1), anchor=date(2026, 1, 2))
    assert (p.start, p.end) == (date(2025, 12, 19), date(2026, 1, 1))
    assert p.contains(date(2026, 1, 1))


def test_fortnightly_without_an_anchor_raises(session=None):
    with pytest.raises(ValueError, match="anchor_date"):
        period_for(F, date(2026, 8, 30))


def test_every_resolved_period_contains_its_date():
    """Sweep two years across every period kind -- the postcondition must hold."""
    anchor = date(2026, 1, 2)
    d = date(2025, 6, 1)
    while d < date(2027, 6, 1):
        for kind in (D, W, F, M, Q, Y):
            assert period_for(kind, d, anchor).contains(d)
        d += timedelta(days=1)


# --------------------------------------------------------------------------
# Stepping
# --------------------------------------------------------------------------


def test_month_end_does_not_ratchet():
    """Stepping an END date with relativedelta clamps in February and never recovers.

    From 2026-01-31 it gives 02-28, then 03-28, 04-28, 05-28. Deriving both
    boundaries from the month ordinal keeps every end on the real month end.
    """
    p = period_for(M, date(2026, 1, 31))
    ends = []
    for _ in range(5):
        p = next_period(M, p)
        ends.append(p.end)
    assert ends == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_stepping_across_a_year_boundary():
    assert next_period(M, period_for(M, date(2026, 12, 5))).start == date(2027, 1, 1)
    assert prev_period(M, period_for(M, date(2026, 1, 5))).start == date(2025, 12, 1)
    assert next_period(Q, period_for(Q, date(2026, 11, 5))).start == date(2027, 1, 1)
    assert prev_period(Q, period_for(Q, date(2026, 2, 5))).start == date(2025, 10, 1)


@pytest.mark.parametrize("kind", [D, W, F, M, Q, Y])
def test_next_and_prev_are_inverse(kind):
    anchor = date(2026, 1, 2)
    p = period_for(kind, date(2026, 8, 30), anchor)
    assert prev_period(kind, next_period(kind, p, anchor), anchor) == p
    assert next_period(kind, prev_period(kind, p, anchor), anchor) == p


@pytest.mark.parametrize("kind", [D, W, F, M, Q, Y])
def test_periods_tile_without_gap_or_overlap(kind):
    anchor = date(2026, 1, 2)
    p = period_for(kind, date(2025, 3, 3), anchor)
    for _ in range(40):
        nxt = next_period(kind, p, anchor)
        assert nxt.start == p.end + timedelta(days=1)
        p = nxt


# --------------------------------------------------------------------------
# Day counts
# --------------------------------------------------------------------------


def test_elapsed_and_remaining_both_include_today():
    """Today has partly elapsed AND can still be spent on, so it is in both.

    The identity is elapsed + remaining == total + 1. Deriving one from the other
    via total - elapsed is off by one every day, and gives 0 on the last day of
    every period -- a division by zero in the allowance.
    """
    p = period_for(M, date(2026, 8, 15))
    today = date(2026, 8, 15)
    assert elapsed_days(p, today) == 15
    assert days_remaining(p, today) == 17
    assert elapsed_days(p, today) + days_remaining(p, today) == p.days + 1


def test_last_day_of_period_still_has_one_day_remaining():
    p = period_for(M, date(2026, 8, 31))
    assert days_remaining(p, date(2026, 8, 31)) == 1


def test_days_remaining_is_none_for_a_closed_period():
    """Not zero. Zero is the exhausted-allowance value and must stay distinct."""
    july = period_for(M, date(2026, 7, 1))
    assert days_remaining(july, date(2026, 8, 30)) is None
    assert period_state(july, date(2026, 8, 30)) == CLOSED


def test_daily_period_closed_yesterday_does_not_divide_by_zero():
    """The literal rulebook formula gives exactly 0 here."""
    yesterday = period_for(D, date(2026, 8, 29))
    assert days_remaining(yesterday, date(2026, 8, 30)) is None


def test_future_period_counts_only_its_own_days():
    """The rulebook's 'days from today to period end' charges August days
    against September. The window starts at max(today, start)."""
    september = period_for(M, date(2026, 9, 1))
    assert days_remaining(september, date(2026, 8, 30)) == 30
    assert elapsed_days(september, date(2026, 8, 30)) == 0
    assert period_state(september, date(2026, 8, 30)) == FUTURE


def test_open_period_state():
    p = period_for(M, date(2026, 8, 15))
    assert period_state(p, date(2026, 8, 15)) == OPEN


# --------------------------------------------------------------------------
# Reporting attribution
# --------------------------------------------------------------------------


def test_week_straddling_a_month_belongs_to_its_thursday():
    assert reporting_month(Period(date(2026, 8, 31), date(2026, 9, 6))) == (2026, 9)
    assert reporting_month(Period(date(2026, 12, 28), date(2027, 1, 3))) == (2026, 12)


def test_2026_has_53_iso_weeks():
    """Annual roll-ups must enumerate the grid, never multiply by 52."""
    p = period_for(W, date(2026, 1, 1))
    count = 0
    while p.start.year <= 2026:
        if reporting_month(p)[0] == 2026:
            count += 1
        p = next_period(W, p)
    assert count == 53
