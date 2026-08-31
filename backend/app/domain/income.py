"""Expected income occurrences. Rulebook section 5.

``ExpectedIncome.first_expected_date`` is a recurrence **anchor**, not a moving
pointer to the next payday. Nothing advances it, and nothing should: a stored
"next" date is a derived value that drifts the moment the date passes, which is
the same class of defect as a mutable ``Budget.amount``.

It was previously named ``next_expected_date``, and the name did the damage. Two
of the three consumers read it literally as "the next one", so once the anchor
was in the past:

* ``near_term_window_end`` found no future income and silently fell back to a
  30-day window, so the headline safe-to-spend figure changed meaning;
* recovery reported ``income_in = 0``, understating headroom by a full salary and
  spuriously firing "protected savings cannot be met" -- exactly the failure the
  income term was added to prevent.

Meanwhile the calendar expanded the rule and was right. One field, three engines,
two answers. Everything now derives occurrences here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.money import ZERO
from app.domain.recurrence import expand
from app.models.planning import ExpectedIncome

#: How far past the anchor a rule is expanded when hunting for the next payday.
#: Two years covers annual rules; the expansion stops at the first hit anyway.
LOOKAHEAD_DAYS = 760


def occurrences(
    session: Session, start: date, end: date
) -> list[tuple[date, str, Decimal]]:
    """Every expected income falling in ``[start, end]``, as (date, name, amount).

    A one-off income has a single occurrence at its anchor; a recurring one is
    expanded from the anchor by its rule.
    """
    out: list[tuple[date, str, Decimal]] = []
    for income in session.scalars(
        select(ExpectedIncome).where(ExpectedIncome.active.is_(True))
    ):
        if income.rrule:
            dates = expand(income.rrule, income.first_expected_date, end)
        else:
            dates = [income.first_expected_date]
        for when in dates:
            if start <= when <= end:
                out.append((when, income.name, income.amount))
    return sorted(out)


def next_date(session: Session, today: date) -> date | None:
    """The soonest expected income on or after ``today``, or None if there is none.

    Inclusive of today: the near-term window asks "how long until more money
    arrives", and money arriving today still ends the window.
    """
    found = occurrences(session, today, today + timedelta(days=LOOKAHEAD_DAYS))
    return found[0][0] if found else None


def total_between(
    session: Session, after: date, until: date
) -> Decimal:
    """Income expected strictly after ``after``, up to and including ``until``.

    Strictly after, per invariant I1: on payday itself the ledger is
    authoritative and the money is already counted in cash. Including it in the
    forward term as well overstates headroom by a full month's pay, on the single
    day the user is most likely to be checking.
    """
    return sum(
        (amount for when, _, amount in occurrences(session, after, until) if when > after),
        ZERO,
    )
