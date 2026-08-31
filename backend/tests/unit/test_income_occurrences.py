"""Expected income is derived from its rule, never read off a stored pointer.

Rulebook section 5. These pin the drift bug: `first_expected_date` is a
recurrence anchor, and once it is in the past every consumer must still know when
the next payday is. Before this, two of the three did not -- and the two that did
not are the ones that produce the headline figure.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain import budget_recovery
from app.domain import calendar as cal
from app.domain.disposable import near_term_window_end
from app.domain.income import next_date, occurrences, total_between
from app.domain.recurrence import Frequency, build_rule
from app.models import ExpectedIncome, UserProfile

ZERO = Decimal("0")


@pytest.fixture
def profile(session):
    p = UserProfile(protected_cash_buffer=ZERO)
    session.add(p)
    session.commit()
    return p


def add_income(session, when, amount="2500", frequency=None, active=True, name="Salary"):
    session.add(
        ExpectedIncome(
            name=name,
            amount=Decimal(amount),
            first_expected_date=when,
            rrule=build_rule(frequency, when) if frequency else None,
            active=active,
        )
    )
    session.commit()


# --------------------------------------------------------------------------
# The drift
# --------------------------------------------------------------------------


def test_next_date_is_derived_after_the_anchor_has_passed(session, profile):
    """The anchor is 25 August; three weeks later the answer is 25 September."""
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    assert next_date(session, date(2026, 8, 20)) == date(2026, 8, 25)
    assert next_date(session, date(2026, 9, 10)) == date(2026, 9, 25)
    assert next_date(session, date(2027, 1, 2)) == date(2027, 1, 25)


def test_near_term_window_does_not_collapse_to_the_fallback(session, accounts, profile):
    """The bug that changed what safe-to-spend meant.

    Reading the stored column found no future income once the anchor passed, so
    the window silently became "today + 30 days" instead of ending at payday.
    """
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    today = date(2026, 9, 10)

    assert near_term_window_end(session, today) == date(2026, 9, 25)
    # Not the 30-day fallback.
    assert near_term_window_end(session, today) != today + timedelta(days=30)


def test_recovery_still_sees_a_salary_inside_the_horizon(session, accounts, profile):
    """The bug that understated headroom by a full month's pay.

    Anchor 25 August, today 10 September, horizon 30 September. The salary due on
    25 September is plainly inside the horizon, but the stale pointer was not, so
    income_in came back as zero.
    """
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    r = budget_recovery.assess(session, date(2026, 9, 10))
    assert r.horizon == date(2026, 9, 30)
    assert r.income_in == Decimal("2500")


def test_all_three_engines_agree_on_the_next_payday(session, accounts, profile):
    """One field, one answer. The calendar expanded the rule; the others did not."""
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    today = date(2026, 9, 10)

    window = near_term_window_end(session, today)
    curve = cal.build(session, today, date(2026, 9, 30))
    curve_income = [
        d.day for d in curve.days if any(e.kind == "income" for e in d.events)
    ]

    assert window == date(2026, 9, 25)
    assert curve_income == [date(2026, 9, 25)]
    assert budget_recovery.assess(session, today).income_in == Decimal("2500")


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------


def test_next_date_includes_today(session, profile):
    """Money arriving today still ends the near-term window."""
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    assert next_date(session, date(2026, 8, 25)) == date(2026, 8, 25)


def test_total_between_excludes_today(session, profile):
    """Invariant I1: on payday the ledger is authoritative, so no forward term."""
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY)
    assert total_between(session, date(2026, 8, 25), date(2026, 8, 31)) == ZERO
    assert total_between(session, date(2026, 8, 24), date(2026, 8, 31)) == Decimal("2500")


def test_a_one_off_income_has_a_single_occurrence(session, profile):
    add_income(session, date(2026, 9, 12), amount="800", name="Bonus")
    assert [d for d, _, _ in occurrences(session, date(2026, 1, 1), date(2027, 12, 31))] == [
        date(2026, 9, 12)
    ]
    # And once it has passed there is no next one.
    assert next_date(session, date(2026, 9, 13)) is None


def test_inactive_income_is_ignored(session, profile):
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY, active=False)
    assert next_date(session, date(2026, 9, 10)) is None


def test_the_soonest_of_several_wins(session, profile):
    add_income(session, date(2026, 8, 25), frequency=Frequency.MONTHLY, name="Salary")
    add_income(session, date(2026, 9, 5), amount="200", name="Dividend")
    assert next_date(session, date(2026, 9, 1)) == date(2026, 9, 5)


def test_month_end_income_clamps_like_any_other_rule(session, profile):
    """A salary on the 31st must not vanish in the months that lack one."""
    add_income(session, date(2026, 1, 31), frequency=Frequency.MONTHLY)
    assert next_date(session, date(2026, 2, 1)) == date(2026, 2, 28)


def test_no_income_configured_falls_back(session, accounts, profile):
    """With nothing to derive from, the window uses the configured fallback."""
    today = date(2026, 9, 10)
    assert next_date(session, today) is None
    assert near_term_window_end(session, today) == today + timedelta(days=30)
