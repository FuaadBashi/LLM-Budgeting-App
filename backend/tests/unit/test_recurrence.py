"""Recurrence expansion and obligation instances. Rulebook section 6."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.obligations import generate_instances, match_instances
from app.domain.recurrence import Frequency, build_rule, expand
from app.models import FutureObligation, ObligationInstance
from tests.conftest import post

TODAY = date(2026, 8, 15)


# --------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------


def test_monthly_on_the_31st_clamps_rather_than_skipping():
    """The whole reason this module does not use a naive BYMONTHDAY.

    RFC 5545 skips a month that lacks the day, so the naive rule drops rent in
    February, April, June, September and November -- five months a year, silently.
    """
    rule = build_rule(Frequency.MONTHLY, date(2026, 1, 31))
    got = expand(rule, date(2026, 1, 31), date(2026, 6, 30))
    assert got == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_month_end_clamp_respects_leap_years():
    rule = build_rule(Frequency.MONTHLY, date(2024, 1, 31))
    got = expand(rule, date(2024, 1, 31), date(2024, 3, 31))
    assert got[1] == date(2024, 2, 29)


def test_day_29_clamps_only_where_needed():
    rule = build_rule(Frequency.MONTHLY, date(2026, 1, 29))
    got = expand(rule, date(2026, 1, 29), date(2026, 3, 31))
    assert got == [date(2026, 1, 29), date(2026, 2, 28), date(2026, 3, 29)]


def test_an_ordinary_day_uses_the_simple_rule():
    """No clamping machinery where every month has the day -- keeps it readable."""
    assert build_rule(Frequency.MONTHLY, date(2026, 8, 15)) == (
        "FREQ=MONTHLY;BYMONTHDAY=15"
    )


@pytest.mark.parametrize(
    "frequency,anchor,expected",
    [
        (Frequency.WEEKLY, date(2026, 8, 3), [date(2026, 8, 3), date(2026, 8, 10)]),
        (
            Frequency.FORTNIGHTLY,
            date(2026, 8, 3),
            [date(2026, 8, 3), date(2026, 8, 17)],
        ),
        (Frequency.DAILY, date(2026, 8, 3), [date(2026, 8, 3), date(2026, 8, 4)]),
    ],
)
def test_simple_frequencies(frequency, anchor, expected):
    rule = build_rule(frequency, anchor)
    got = expand(rule, anchor, expected[-1])
    assert got == expected


def test_quarterly_steps_three_months():
    rule = build_rule(Frequency.QUARTERLY, date(2026, 1, 15))
    got = expand(rule, date(2026, 1, 15), date(2026, 12, 31))
    assert got == [
        date(2026, 1, 15),
        date(2026, 4, 15),
        date(2026, 7, 15),
        date(2026, 10, 15),
    ]


def test_annual_on_29_february_clamps():
    rule = build_rule(Frequency.ANNUAL, date(2024, 2, 29))
    got = expand(rule, date(2024, 2, 29), date(2027, 3, 1))
    assert got == [
        date(2024, 2, 29),
        date(2025, 2, 28),
        date(2026, 2, 28),
        date(2027, 2, 28),
    ]


def test_expansion_is_empty_when_the_horizon_precedes_the_start():
    rule = build_rule(Frequency.MONTHLY, date(2026, 8, 1))
    assert expand(rule, date(2026, 8, 1), date(2026, 7, 1)) == []


# --------------------------------------------------------------------------
# Instance generation
# --------------------------------------------------------------------------


def add_obligation(session, name, amount, first_due, frequency=None, end=None):
    ob = FutureObligation(
        name=name,
        amount=Decimal(amount),
        first_due_date=first_due,
        end_date=end,
        rrule=build_rule(frequency, first_due) if frequency else None,
        hard=True,
    )
    session.add(ob)
    session.commit()
    return ob


def due_dates(session, ob) -> list[date]:
    from sqlalchemy import select

    return list(
        session.scalars(
            select(ObligationInstance.due_date)
            .where(ObligationInstance.obligation_id == ob.id)
            .order_by(ObligationInstance.due_date)
        )
    )


def test_generation_materialises_instances_to_the_horizon(session):
    ob = add_obligation(
        session, "Rent", "1200", date(2026, 8, 1), Frequency.MONTHLY
    )
    result = generate_instances(session, date(2026, 11, 30))
    assert result.created == 4
    assert due_dates(session, ob) == [
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    ]


def test_generation_is_idempotent(session):
    ob = add_obligation(
        session, "Rent", "1200", date(2026, 8, 1), Frequency.MONTHLY
    )
    generate_instances(session, date(2026, 10, 31))
    second = generate_instances(session, date(2026, 10, 31))
    assert second.created == 0
    assert second.skipped_existing == 3
    assert len(due_dates(session, ob)) == 3


def test_regenerating_does_not_clear_a_fulfilment_link(session, accounts):
    """Regeneration must never undo matching -- that would resurrect a paid bill."""
    ob = add_obligation(
        session, "Rent", "600", date(2026, 8, 1), Frequency.MONTHLY
    )
    generate_instances(session, date(2026, 9, 30))
    txn = post(
        session,
        date(2026, 8, 1),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    match_instances(session, TODAY)

    generate_instances(session, date(2026, 10, 31))

    from sqlalchemy import select

    linked = session.scalars(
        select(ObligationInstance)
        .where(ObligationInstance.obligation_id == ob.id)
        .where(ObligationInstance.due_date == date(2026, 8, 1))
    ).one()
    assert linked.fulfilled_by_transaction_id == txn.id


def test_a_one_off_obligation_generates_exactly_one_instance(session):
    ob = add_obligation(session, "Car service", "340", date(2026, 9, 12))
    generate_instances(session, date(2027, 12, 31))
    assert due_dates(session, ob) == [date(2026, 9, 12)]


def test_end_date_stops_generation(session):
    ob = add_obligation(
        session,
        "Gym",
        "40",
        date(2026, 8, 1),
        Frequency.MONTHLY,
        end=date(2026, 10, 15),
    )
    generate_instances(session, date(2027, 6, 30))
    assert due_dates(session, ob) == [
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]


def test_inactive_obligations_generate_nothing(session):
    ob = add_obligation(
        session, "Old subscription", "10", date(2026, 8, 1), Frequency.MONTHLY
    )
    ob.active = False
    session.commit()
    generate_instances(session, date(2026, 12, 31))
    assert due_dates(session, ob) == []


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_matching_links_an_exact_amount_within_the_window(session, accounts):
    ob = add_obligation(session, "Rent", "600", date(2026, 8, 10))
    generate_instances(session, date(2026, 8, 31))
    txn = post(
        session,
        date(2026, 8, 12),  # two days late, inside the window
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )

    assert match_instances(session, TODAY).matched == 1
    from sqlalchemy import select

    inst = session.scalars(select(ObligationInstance)).one()
    assert inst.fulfilled_by_transaction_id == txn.id
    # A suggestion, not a decision.
    assert inst.match_confirmed is False


def test_matching_rejects_a_different_amount(session, accounts):
    add_obligation(session, "Rent", "600", date(2026, 8, 10))
    generate_instances(session, date(2026, 8, 31))
    post(
        session,
        date(2026, 8, 10),
        "Rent",
        [(accounts["current"], "-595"), (accounts["groceries"], "595")],
    )
    assert match_instances(session, TODAY).matched == 0


def test_matching_rejects_a_date_outside_the_window(session, accounts):
    add_obligation(session, "Rent", "600", date(2026, 8, 10))
    generate_instances(session, date(2026, 8, 31))
    post(
        session,
        date(2026, 8, 20),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    assert match_instances(session, TODAY).matched == 0


def test_one_transaction_that_could_satisfy_two_instances_is_left_unmatched(
    session, accounts
):
    """The matcher cannot infer which of two identical bills one payment cleared."""
    add_obligation(session, "Rent", "600", date(2026, 8, 10))
    add_obligation(session, "Storage", "600", date(2026, 8, 11))
    generate_instances(session, date(2026, 8, 31))
    post(
        session,
        date(2026, 8, 10),
        "Payment",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    assert match_instances(session, TODAY).matched == 0


def test_matching_uses_the_expense_leg_not_the_cash_leg(session, accounts):
    """A card-funded bill has no cash leg at all."""
    add_obligation(session, "Rent", "600", date(2026, 8, 10))
    generate_instances(session, date(2026, 8, 31))
    post(
        session,
        date(2026, 8, 10),
        "Rent on the card",
        [(accounts["loan"], "-600"), (accounts["groceries"], "600")],
    )
    assert match_instances(session, TODAY).matched == 1
