"""Projected balance calendar. Plan section 7.4."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain import calendar as cal
from app.domain.obligations import generate_instances
from app.domain.recurrence import Frequency, build_rule
from app.models import ExpectedIncome, FutureObligation, UserProfile
from tests.conftest import post

TODAY = date(2026, 8, 31)


@pytest.fixture
def profile(session):
    p = UserProfile(protected_cash_buffer=Decimal("200"))
    session.add(p)
    session.commit()
    return p


def add_obligation(session, name, amount, first_due, frequency=None):
    ob = FutureObligation(
        name=name,
        amount=Decimal(amount),
        first_due_date=first_due,
        rrule=build_rule(frequency, first_due) if frequency else None,
        hard=True,
    )
    session.add(ob)
    session.commit()
    generate_instances(session, date(2027, 12, 31), ob)
    return ob


def add_income(session, name, amount, when, frequency=None):
    session.add(
        ExpectedIncome(
            name=name,
            amount=Decimal(amount),
            next_expected_date=when,
            rrule=build_rule(frequency, when) if frequency else None,
        )
    )
    session.commit()


def day(c: cal.Calendar, when: date) -> cal.CalendarDay:
    return next(d for d in c.days if d.day == when)


def test_opening_balance_is_liquid_cash_only(session, accounts, profile):
    """Savings and investments are not liquid; nominal accounts are not accounts."""
    c = cal.build(session, TODAY, TODAY)
    assert c.opening_balance == Decimal("1050")  # current 1000 + cash 50


def test_committed_outflows_reduce_the_curve(session, accounts, profile):
    add_obligation(session, "Rent", "600", date(2026, 9, 2))
    c = cal.build(session, TODAY, date(2026, 9, 5))

    assert day(c, date(2026, 9, 1)).closing_balance == Decimal("1050")
    assert day(c, date(2026, 9, 2)).closing_balance == Decimal("450")
    # The balance carries forward on days with no events.
    assert day(c, date(2026, 9, 5)).closing_balance == Decimal("450")


def test_expected_income_lifts_the_curve(session, accounts, profile):
    add_income(session, "Salary", "2500", date(2026, 9, 1))
    c = cal.build(session, TODAY, date(2026, 9, 3))
    assert day(c, date(2026, 9, 1)).closing_balance == Decimal("3550")


def test_income_on_the_start_day_is_not_counted_forward(session, accounts, profile):
    """Same rule as invariant I1: on payday the money is already in the ledger."""
    add_income(session, "Salary", "2500", TODAY)
    c = cal.build(session, TODAY, date(2026, 9, 3))
    assert day(c, TODAY).closing_balance == Decimal("1050")


def test_fulfilled_obligations_are_excluded(session, accounts, profile):
    """Invariant O1 again: a paid bill must not also be projected."""
    ob = add_obligation(session, "Rent", "600", date(2026, 9, 2))
    session.refresh(ob)  # instances were generated after the relationship loaded
    instance = ob.instances[0]
    txn = post(
        session,
        date(2026, 9, 2),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    instance.fulfilled_by_transaction_id = txn.id
    session.commit()

    c = cal.build(session, TODAY, date(2026, 9, 5))
    # Charged exactly once -- via the posted transaction, not the obligation.
    assert day(c, date(2026, 9, 2)).closing_balance == Decimal("450")
    assert len(day(c, date(2026, 9, 2)).events) == 1


def test_optional_obligations_do_not_affect_the_curve(session, accounts, profile):
    ob = FutureObligation(
        name="Maybe a holiday",
        amount=Decimal("800"),
        first_due_date=date(2026, 9, 2),
        hard=False,
    )
    session.add(ob)
    session.commit()
    generate_instances(session, date(2026, 12, 31), ob)

    c = cal.build(session, TODAY, date(2026, 9, 5))
    assert day(c, date(2026, 9, 5)).closing_balance == Decimal("1050")


# --------------------------------------------------------------------------
# The warning the plan actually asks for
# --------------------------------------------------------------------------


def test_breach_names_the_payment_that_caused_it(session, accounts, profile):
    """Plan section 7.4: not "bill due" but "this payment takes you below buffer".

    Three bills, and only the third is the problem. A list of upcoming bills
    cannot express that; the curve can.
    """
    add_obligation(session, "Phone", "40", date(2026, 9, 1))
    add_obligation(session, "Council tax", "210", date(2026, 9, 3))
    add_obligation(session, "Rent", "700", date(2026, 9, 5))

    c = cal.build(session, TODAY, date(2026, 9, 10))

    assert day(c, date(2026, 9, 1)).closing_balance == Decimal("1010")
    assert day(c, date(2026, 9, 3)).closing_balance == Decimal("800")
    assert day(c, date(2026, 9, 5)).closing_balance == Decimal("100")

    assert c.first_breach_date == date(2026, 9, 5)
    assert c.first_breach_cause == "Rent"  # the actionable one


def test_no_breach_when_the_buffer_holds(session, accounts, profile):
    add_obligation(session, "Phone", "40", date(2026, 9, 1))
    c = cal.build(session, TODAY, date(2026, 9, 10))
    assert c.first_breach_date is None
    assert c.first_breach_cause is None


def test_trough_is_the_lowest_point_not_the_last(session, accounts, profile):
    """Income after a dip must not hide the dip."""
    add_obligation(session, "Rent", "900", date(2026, 9, 2))
    add_income(session, "Salary", "2500", date(2026, 9, 10))

    c = cal.build(session, TODAY, date(2026, 9, 20))
    assert c.trough_date == date(2026, 9, 2)
    assert c.trough_balance == Decimal("150")
    # And the curve recovers afterwards.
    assert day(c, date(2026, 9, 20)).closing_balance == Decimal("2650")


def test_recurring_rent_appears_every_month(session, accounts, profile):
    add_obligation(session, "Rent", "600", date(2026, 9, 2), Frequency.MONTHLY)
    add_income(session, "Salary", "2500", date(2026, 9, 1), Frequency.MONTHLY)

    c = cal.build(session, TODAY, date(2026, 12, 31))
    rents = [
        d.day for d in c.days if any(e.name == "Rent" for e in d.events)
    ]
    assert rents == [
        date(2026, 9, 2),
        date(2026, 10, 2),
        date(2026, 11, 2),
        date(2026, 12, 2),
    ]


def test_future_dated_posted_transactions_are_on_the_curve(session, accounts, profile):
    """They are real ledger entries but sit outside account_balances(as_of=today),
    so the curve has to add them or it disagrees with its own opening balance."""
    post(
        session,
        date(2026, 9, 4),
        "Pre-recorded payment",
        [(accounts["current"], "-300"), (accounts["groceries"], "300")],
    )
    c = cal.build(session, TODAY, date(2026, 9, 10))
    assert c.opening_balance == Decimal("1050")
    assert day(c, date(2026, 9, 4)).closing_balance == Decimal("750")


def test_curve_covers_every_day_in_the_window(session, accounts, profile):
    c = cal.build(session, TODAY, date(2026, 9, 10))
    assert [d.day for d in c.days] == [
        date(2026, 8, 31),
        *(date(2026, 9, n) for n in range(1, 11)),
    ]
