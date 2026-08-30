"""Recurrence rules. Rulebook section 6.

Rules are stored as RFC 5545 RRULE strings and expanded with ``dateutil``, so the
stored value means what the standard says it means rather than what one function
in this codebase happens to do with it.

One deviation from a naive reading, and it matters: RFC 5545 **skips** a month
that lacks the requested day. ``FREQ=MONTHLY;BYMONTHDAY=31`` yields January,
March, May, July... and silently drops rent for five months of the year. A bill
due "the 31st" has to land on the 28th in February, which the standard expresses
as the last available day of a candidate set:

    FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1

That is still pure RFC 5545 -- it is a different rule, not a special case bolted
on -- and it handles leap years without any calendar arithmetic here.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from dateutil.rrule import rrulestr

#: Guard against an unbounded expansion driven by bad data.
MAX_OCCURRENCES = 2000


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


def build_rule(frequency: Frequency, anchor: date) -> str:
    """The RRULE for a plain "every N period from this date" commitment.

    The anchor supplies the day-of-month or weekday; only the shape comes from the
    frequency. Month-based rules clamp rather than skip.
    """
    if frequency is Frequency.DAILY:
        return "FREQ=DAILY"
    if frequency is Frequency.WEEKLY:
        return "FREQ=WEEKLY"
    if frequency is Frequency.FORTNIGHTLY:
        return "FREQ=WEEKLY;INTERVAL=2"
    if frequency is Frequency.MONTHLY:
        return _monthly(anchor.day)
    if frequency is Frequency.QUARTERLY:
        return _monthly(anchor.day, interval=3)
    if frequency is Frequency.ANNUAL:
        # 29 February clamps to the 28th in common years, same principle.
        if anchor.month == 2 and anchor.day == 29:
            return "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
        return f"FREQ=YEARLY;BYMONTH={anchor.month};BYMONTHDAY={anchor.day}"
    raise ValueError(f"unsupported frequency: {frequency}")


def _monthly(day: int, interval: int = 1) -> str:
    prefix = f"FREQ=MONTHLY;INTERVAL={interval};" if interval != 1 else "FREQ=MONTHLY;"
    if day <= 28:
        # Every month has a 28th, so no clamping is needed and the simple rule
        # stays readable.
        return f"{prefix}BYMONTHDAY={day}"
    candidates = ",".join(str(d) for d in range(28, day + 1))
    return f"{prefix}BYMONTHDAY={candidates};BYSETPOS=-1"


def expand(rule: str, start: date, until: date) -> list[date]:
    """Occurrence dates in ``[start, until]``, inclusive of both ends.

    ``start`` is the rule's own anchor (DTSTART), so the first occurrence is
    normally ``start`` itself.
    """
    if until < start:
        return []
    occurrences = rrulestr(rule, dtstart=datetime.combine(start, datetime.min.time()))
    out: list[date] = []
    for moment in occurrences:
        d = moment.date()
        if d > until:
            break
        if d >= start:
            out.append(d)
        if len(out) >= MAX_OCCURRENCES:
            break
    return out
