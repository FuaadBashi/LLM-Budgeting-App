"""What counts as spending against a budget. Rulebook section 8, invariant B1.

Most of the failure modes here are *silent zeros*: the budget reports no spend at
all while money has plainly left the account, and nothing errors.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.spend import (
    spend_by_booking_date,
    total_between,
    uncategorised_between,
)
from app.models import TransactionStatus
from tests.conftest import make_account, post
from app.models import AccountKind

START = date(2026, 8, 1)
END = date(2026, 8, 31)
TODAY = date(2026, 8, 15)


def scoped_total(session, category_id, start=START, end=END) -> Decimal:
    return total_between(
        spend_by_booking_date(session, category_id, start, end), start, end
    )


# --------------------------------------------------------------------------
# Which leg, which sign
# --------------------------------------------------------------------------


def test_a_split_transaction_charges_each_category_its_own_leg(
    session, accounts, categories
):
    """Measuring the cash leg makes this impossible to express at all."""
    household = make_account(session, "Household", AccountKind.EXPENSE)
    post(
        session,
        TODAY,
        "Tesco",
        [
            (accounts["current"], "-80.00"),
            (accounts["groceries"], "50.00", categories["groceries"]),
            (household, "30.00", categories["restaurants"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("50.00")
    # Null scope sees the whole £80 -- both legs are discretionary.
    assert scoped_total(session, None) == Decimal("80.00")


def test_a_category_tagged_cash_leg_does_not_net_to_zero(
    session, accounts, categories
):
    """A category-only filter sums -45 + 45 = 0 and reports no spend at all."""
    post(
        session,
        TODAY,
        "Tesco",
        [
            (accounts["current"], "-45.00", categories["groceries"]),
            (accounts["groceries"], "45.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("45.00")


def test_savings_transfer_is_not_spending_even_when_categorised(
    session, accounts, categories
):
    """The account-kind filter is what enforces section 2's transfer rule."""
    post(
        session,
        TODAY,
        "To savings",
        [
            (accounts["current"], "-500.00"),
            (accounts["savings"], "500.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("0")
    assert scoped_total(session, None) == Decimal("0")


def test_a_fee_on_a_transfer_still_counts(session, accounts, categories):
    """The transaction is a transfer and still contains real spend."""
    post(
        session,
        TODAY,
        "Transfer with fee",
        [
            (accounts["current"], "-503.00"),
            (accounts["savings"], "500.00"),
            (accounts["groceries"], "3.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("3.00")


def test_credit_card_purchase_counts(session, accounts, categories):
    """Never derive Spent from the transaction classification."""
    post(
        session,
        TODAY,
        "Tesco on the card",
        [
            (accounts["loan"], "-45.00"),
            (accounts["groceries"], "45.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("45.00")


def test_debt_payment_counts_only_the_interest(session, accounts):
    """Charging the cash leg reports £300 spent in a month net worth fell £50."""
    post(
        session,
        TODAY,
        "Car loan payment",
        [
            (accounts["current"], "-300.00"),
            (accounts["loan"], "250.00"),
            (accounts["interest"], "50.00"),
        ],
    )
    assert scoped_total(session, None) == Decimal("50.00")


def test_refund_reduces_spend_in_its_own_period(session, accounts, categories):
    post(
        session,
        date(2026, 8, 5),
        "Coat",
        [
            (accounts["current"], "-220.00"),
            (accounts["groceries"], "220.00", categories["groceries"]),
        ],
    )
    post(
        session,
        date(2026, 8, 20),
        "Returned",
        [
            (accounts["current"], "220.00"),
            (accounts["groceries"], "-220.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("0.00")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def test_only_posted_transactions_count(session, accounts, categories):
    """Byte-identical to account_balances(). If CANDIDATE counted, an unreviewed
    import would move the budget while safe-to-spend stayed put."""
    txn = post(
        session,
        TODAY,
        "Unreviewed import",
        [
            (accounts["current"], "-340.00"),
            (accounts["groceries"], "340.00", categories["groceries"]),
        ],
        status=TransactionStatus.CANDIDATE,
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("0")

    txn.status = TransactionStatus.POSTED
    session.commit()
    assert scoped_total(session, categories["groceries"].id) == Decimal("340.00")


def test_voided_transactions_do_not_count(session, accounts, categories):
    txn = post(
        session,
        TODAY,
        "Mistake",
        [
            (accounts["current"], "-45.00"),
            (accounts["groceries"], "45.00", categories["groceries"]),
        ],
    )
    txn.status = TransactionStatus.VOIDED
    session.commit()
    assert scoped_total(session, categories["groceries"].id) == Decimal("0")


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


def test_a_parent_category_budget_counts_its_whole_subtree(
    session, accounts, categories
):
    """Exact matching gives £0 for ever, because leaves are what get tagged."""
    for amount, cat in [
        ("180.00", categories["groceries"]),
        ("70.00", categories["restaurants"]),
    ]:
        post(
            session,
            TODAY,
            "food",
            [(accounts["current"], f"-{amount}"), (accounts["groceries"], amount, cat)],
        )
    assert scoped_total(session, categories["food"].id) == Decimal("250.00")


def test_uncategorised_spend_counts_toward_the_null_scope_budget(session, accounts):
    """Excluding NULL gives a full green budget in a month the user is over."""
    post(
        session,
        TODAY,
        "untagged",
        [(accounts["current"], "-612.00"), (accounts["groceries"], "612.00")],
    )
    assert scoped_total(session, None) == Decimal("612.00")
    assert uncategorised_between(session, START, END) == Decimal("612.00")


def test_essential_spend_is_excluded_from_a_null_scope_budget(
    session, accounts, categories
):
    post(
        session,
        TODAY,
        "Rent",
        [
            (accounts["current"], "-1200.00"),
            (accounts["groceries"], "1200.00", categories["rent"]),
        ],
    )
    assert scoped_total(session, None) == Decimal("0")


def test_an_explicitly_scoped_essential_budget_still_counts_its_spend(
    session, accounts, categories
):
    """The discretionary filter applies only to null scope. Applying it globally
    makes every essential-category budget read £0.00 for ever."""
    post(
        session,
        TODAY,
        "Rent",
        [
            (accounts["current"], "-1200.00"),
            (accounts["groceries"], "1200.00", categories["rent"]),
        ],
    )
    assert scoped_total(session, categories["rent"].id) == Decimal("1200.00")


def test_spend_outside_the_window_is_excluded(session, accounts, categories):
    post(
        session,
        date(2026, 7, 31),
        "July",
        [
            (accounts["current"], "-100.00"),
            (accounts["groceries"], "100.00", categories["groceries"]),
        ],
    )
    post(
        session,
        date(2026, 9, 1),
        "September",
        [
            (accounts["current"], "-100.00"),
            (accounts["groceries"], "100.00", categories["groceries"]),
        ],
    )
    assert scoped_total(session, categories["groceries"].id) == Decimal("0")
