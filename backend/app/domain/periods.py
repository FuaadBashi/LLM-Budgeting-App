"""Budget period arithmetic. Rulebook section 8.

Pure, total and session-free. Everything here operates on ``datetime.date`` and
never on a ``datetime`` -- "today" arrives from :mod:`app.domain.clock`, already
resolved in the reporting timezone, and no timezone reasoning happens below.

Bucketing deliberately happens in Python rather than SQL. Postgres disagrees with
Python on both of the operations this module depends on: ``EXTRACT(DOW)`` is
Sunday-based where ``date.weekday()`` is Monday-based, and SQL integer division
truncates toward zero where Python's ``//`` floors. Keeping the arithmetic in one
language removes both traps at once.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from app.models.enums import BudgetPeriod


@dataclass(frozen=True)
class Period:
    """A closed date interval, ``[start, end]``, both inclusive."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def __repr__(self) -> str:
        return f"<Period {self.start}..{self.end}>"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _month_period(year: int, month: int) -> Period:
    return Period(date(year, month, 1), date(year, month, monthrange(year, month)[1]))


def period_for(period: BudgetPeriod, d: date, anchor: date | None = None) -> Period:
    """The period of ``period`` kind containing ``d``.

    Total for every date: the grid extends infinitely in both directions. The
    anchor is an epoch, not a lower bound.
    """
    result = _resolve(period, d, anchor)
    # A period that does not contain the date it was asked about is the signature
    # of a truncating-division bug, and it is silent otherwise.
    assert result.contains(d), f"{result} does not contain {d}"
    return result


def _resolve(period: BudgetPeriod, d: date, anchor: date | None) -> Period:
    if period is BudgetPeriod.DAILY:
        return Period(d, d)

    if period is BudgetPeriod.WEEKLY:
        # weekday() is Monday==0, which is what ISO-8601 wants. isoweekday() is
        # Monday==1 and shifts every boundary by a day.
        start = d - timedelta(days=d.weekday())
        return Period(start, start + timedelta(days=6))

    if period is BudgetPeriod.FORTNIGHTLY:
        if anchor is None:
            raise ValueError("fortnightly budgets require an anchor_date")
        # Floor division, deliberately. int((d - anchor).days / 14) truncates
        # toward zero, so for any date before the anchor it returns k=0 and hands
        # back an interval that does not contain d.
        k = (d - anchor).days // 14
        start = anchor + timedelta(days=14 * k)
        return Period(start, start + timedelta(days=13))

    if period is BudgetPeriod.MONTHLY:
        return _month_period(d.year, d.month)

    if period is BudgetPeriod.QUARTERLY:
        start_month = 3 * ((d.month - 1) // 3) + 1
        end_month = start_month + 2
        return Period(
            date(d.year, start_month, 1),
            date(d.year, end_month, monthrange(d.year, end_month)[1]),
        )

    if period is BudgetPeriod.ANNUAL:
        return Period(date(d.year, 1, 1), date(d.year, 12, 31))

    raise ValueError(f"unsupported period: {period}")


# --------------------------------------------------------------------------
# Stepping
# --------------------------------------------------------------------------


def _step(period: BudgetPeriod, p: Period, n: int, anchor: date | None) -> Period:
    """Both boundaries are re-derived from the ordinal; end dates are never stepped.

    Adding a month to an end date is lossy and ratchets: from 2026-01-31,
    relativedelta gives 2026-02-28, then 2026-03-28, then 2026-04-28, and the
    boundary never recovers.
    """
    if period is BudgetPeriod.DAILY:
        return period_for(period, p.start + timedelta(days=n))
    if period is BudgetPeriod.WEEKLY:
        return period_for(period, p.start + timedelta(days=7 * n))
    if period is BudgetPeriod.FORTNIGHTLY:
        return period_for(period, p.start + timedelta(days=14 * n), anchor)
    if period is BudgetPeriod.MONTHLY:
        index = p.start.year * 12 + (p.start.month - 1) + n
        return _month_period(index // 12, index % 12 + 1)
    if period is BudgetPeriod.QUARTERLY:
        index = p.start.year * 4 + (p.start.month - 1) // 3 + n
        return period_for(period, date(index // 4, (index % 4) * 3 + 1, 1))
    if period is BudgetPeriod.ANNUAL:
        return Period(date(p.start.year + n, 1, 1), date(p.start.year + n, 12, 31))
    raise ValueError(f"unsupported period: {period}")


def next_period(period: BudgetPeriod, p: Period, anchor: date | None = None) -> Period:
    return _step(period, p, +1, anchor)


def prev_period(period: BudgetPeriod, p: Period, anchor: date | None = None) -> Period:
    return _step(period, p, -1, anchor)


# --------------------------------------------------------------------------
# Day counts and state
# --------------------------------------------------------------------------

FUTURE = "future"
OPEN = "open"
CLOSED = "closed"


def period_state(p: Period, today: date) -> str:
    if today < p.start:
        return FUTURE
    if today > p.end:
        return CLOSED
    return OPEN


def elapsed_days(p: Period, today: date) -> int:
    """Days of the period gone, including today. Zero before it starts."""
    if today < p.start:
        return 0
    return (min(today, p.end) - p.start).days + 1


def days_remaining(p: Period, today: date) -> int | None:
    """Days left including today, or None once the period has closed.

    None rather than 0: zero is already the value of an exhausted allowance, and
    "no budget left today" and "this period ended" must stay distinguishable. The
    rulebook formula taken literally divides by zero the day after any period ends
    and goes negative after that -- viewing July on 30 August yields -29 days and
    an allowance of -£5.18.
    """
    if today > p.end:
        return None
    return (p.end - max(today, p.start)).days + 1


def reporting_month(p: Period) -> tuple[int, int]:
    """The (year, month) a period is reported under.

    A period is attributed to the month containing its Thursday. For a Monday-start
    seven-day week that is provably the majority of its days, and it keeps a week
    straddling a month boundary in exactly one bucket.
    """
    pivot = p.start + timedelta(days=3)
    if p.days >= 28:
        pivot = p.start
    return pivot.year, pivot.month
