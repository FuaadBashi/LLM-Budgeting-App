"""Reimbursement netting. Engine spec M3.

A merchant refund and an employer reimbursement are the same event to a budget.
Without netting they differ by £600 purely on who sent the money back.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.budgets import chain, current_period
from app.domain.reimbursement import allocate, netting_by_booking_date
from app.models import Budget, BudgetPeriod, BudgetRevision, RolloverPolicy
from tests.conftest import make_account, post
from app.models import AccountKind

START = date(2026, 8, 1)
END = date(2026, 8, 31)


def make_budget(session, amount="600", category=None, start=START):
    b = Budget(
        name="Discretionary",
        period=BudgetPeriod.MONTHLY,
        start_date=start,
        category_id=category.id if category is not None else None,
    )
    session.add(b)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=b.id,
            effective_from=start,
            amount=Decimal(amount),
            rollover_policy=RolloverPolicy.NONE,
        )
    )
    session.commit()
    session.refresh(b)
    return b


def claims_account(session):
    return make_account(session, "Expense Claims", AccountKind.INCOME_SOURCE)


# --------------------------------------------------------------------------
# allocate()
# --------------------------------------------------------------------------


def test_allocation_sums_exactly():
    """Rounding each share independently gives £49.99 -- money that vanishes in
    the split reappears as budget the user did not earn."""
    shares = allocate(Decimal("50.00"), [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])
    assert sum(shares) == Decimal("50.00")
    assert shares == [Decimal("16.67"), Decimal("16.66"), Decimal("16.67")]


@pytest.mark.parametrize(
    "total,legs",
    [
        ("100.00", ["50.00", "50.00"]),
        ("10.00", ["1.00", "2.00", "3.00", "4.00"]),
        ("0.01", ["1.00", "1.00", "1.00"]),
        ("45.00", ["45.00"]),
        ("99.99", ["33.33", "33.33", "33.33"]),
    ],
)
def test_allocation_always_sums_to_the_total(total, legs):
    shares = allocate(Decimal(total), [Decimal(x) for x in legs])
    assert sum(shares) == Decimal(total)


def test_allocation_is_proportional():
    shares = allocate(Decimal("90.00"), [Decimal("10.00"), Decimal("20.00")])
    assert shares == [Decimal("30.00"), Decimal("60.00")]


def test_allocation_handles_no_legs():
    assert allocate(Decimal("10"), []) == []


# --------------------------------------------------------------------------
# Netting against a budget
# --------------------------------------------------------------------------


def test_a_reimbursed_expense_does_not_consume_the_budget(session, accounts):
    """The headline case. Without netting this budget reads fully consumed."""
    claims = claims_account(session)
    budget = make_budget(session, amount="600")

    trip = post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Employer repayment",
        [(accounts["current"], "600"), (claims, "-600")],
        reimburses_id=trip.id,
    )

    r = current_period(session, budget, date(2026, 8, 25))
    assert r.spent == Decimal("0")
    assert r.remaining == Decimal("600")


def test_matches_the_outcome_of_an_identical_merchant_refund(session, accounts):
    """The two paths must agree -- the counterparty is not a budget concept."""
    claims = claims_account(session)

    reimbursed = make_budget(session, amount="600")
    trip = post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Employer repayment",
        [(accounts["current"], "600"), (claims, "-600")],
        reimburses_id=trip.id,
    )
    via_reimbursement = current_period(session, reimbursed, date(2026, 8, 25)).spent

    # Same money, refunded by the merchant instead.
    for t in ["postings", "transactions"]:
        session.execute(__import__("sqlalchemy").text(f'TRUNCATE "{t}" CASCADE'))
    session.commit()
    post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Merchant refund",
        [(accounts["current"], "600"), (accounts["groceries"], "-600")],
    )
    via_refund = current_period(session, reimbursed, date(2026, 8, 25)).spent

    assert via_reimbursement == via_refund == Decimal("0")


def test_partial_reimbursement_leaves_the_unrepaid_part(session, accounts):
    claims = claims_account(session)
    budget = make_budget(session, amount="600")

    trip = post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-500"), (accounts["groceries"], "500")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Partial repayment",
        [(accounts["current"], "300"), (claims, "-300")],
        reimburses_id=trip.id,
    )

    r = current_period(session, budget, date(2026, 8, 25))
    assert r.spent == Decimal("200")


def test_a_split_expense_is_repaid_pro_rata(session, accounts, categories):
    """One repayment against a transaction split across two categories."""
    claims = claims_account(session)
    household = make_account(session, "Household", AccountKind.EXPENSE)
    food_budget = make_budget(session, amount="400", category=categories["food"])

    trip = post(
        session,
        date(2026, 8, 10),
        "Mixed",
        [
            (accounts["current"], "-100"),
            (accounts["groceries"], "60", categories["groceries"]),
            (household, "40", categories["rent"]),
        ],
    )
    post(
        session,
        date(2026, 8, 20),
        "Repaid half",
        [(accounts["current"], "50"), (claims, "-50")],
        reimburses_id=trip.id,
    )

    # £50 over legs of 60/40 -> £30 to the food leg.
    r = current_period(session, food_budget, date(2026, 8, 25))
    assert r.spent == Decimal("30")


def test_over_repayment_is_capped_and_reported_as_excess(session, accounts):
    """Being repaid more than was spent is income, not negative spending."""
    claims = claims_account(session)
    budget = make_budget(session, amount="600")

    trip = post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-100"), (accounts["groceries"], "100")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Over-repaid",
        [(accounts["current"], "150"), (claims, "-150")],
        reimburses_id=trip.id,
    )

    offsets, excess = netting_by_booking_date(session, None, START, END)
    assert sum(v for _, v in offsets) == Decimal("100")   # capped at what was spent
    assert excess == Decimal("50")

    r = current_period(session, budget, date(2026, 8, 25))
    assert r.spent == Decimal("0")            # not -£50


def test_netting_lands_in_the_reimbursements_own_period(session, accounts):
    """As-booked, like refunds: money back in September is September's news."""
    claims = claims_account(session)
    budget = make_budget(session, amount="600")

    trip = post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-300"), (accounts["groceries"], "300")],
    )
    post(
        session,
        date(2026, 9, 5),
        "Repaid next month",
        [(accounts["current"], "300"), (claims, "-300")],
        reimburses_id=trip.id,
    )

    periods = chain(session, budget, date(2026, 9, 30), date(2026, 9, 30))
    august, september = periods[0], periods[1]
    assert august.spent == Decimal("300")      # August is left as it was reported
    assert september.spent == Decimal("-300")  # and September shows the credit


def test_an_unlinked_repayment_does_not_reduce_any_budget(session, accounts):
    """No reimburses_id means no link, so nothing to net against."""
    claims = claims_account(session)
    budget = make_budget(session, amount="600")

    post(
        session,
        date(2026, 8, 10),
        "Work trip",
        [(accounts["current"], "-300"), (accounts["groceries"], "300")],
    )
    post(
        session,
        date(2026, 8, 20),
        "Unexplained credit",
        [(accounts["current"], "300"), (claims, "-300")],
    )

    assert current_period(session, budget, date(2026, 8, 25)).spent == Decimal("300")


def test_scope_follows_the_original_expense_leg(session, accounts, categories):
    """A repayment of a Rent expense must not credit a Food budget."""
    claims = claims_account(session)
    food_budget = make_budget(session, amount="400", category=categories["food"])

    rent = post(
        session,
        date(2026, 8, 10),
        "Rent",
        [(accounts["current"], "-200"), (accounts["groceries"], "200", categories["rent"])],
    )
    post(
        session,
        date(2026, 8, 20),
        "Rent repaid",
        [(accounts["current"], "200"), (claims, "-200")],
        reimburses_id=rent.id,
    )

    assert current_period(session, food_budget, date(2026, 8, 25)).spent == Decimal("0")
