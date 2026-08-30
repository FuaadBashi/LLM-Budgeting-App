"""Invariants L1, L2, N1 and the derived classification (rulebook sections 2-3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import ProgrammingError

from app.domain.classification import classify
from app.domain.disposable import account_balances, net_worth
from app.models import AccountKind, TransactionClass
from tests.conftest import post

TODAY = date(2026, 8, 15)


def kinds_map(accounts: dict) -> dict:
    return {a.id: a.kind for a in accounts.values()}


# --------------------------------------------------------------------------
# L1 -- postings sum to zero
# --------------------------------------------------------------------------


def test_L1_balanced_transaction_commits(session, accounts):
    post(
        session,
        TODAY,
        "Tesco",
        [(accounts["current"], "-45.00"), (accounts["groceries"], "45.00")],
    )
    assert account_balances(session)[accounts["current"].id] == Decimal("955.00")


def test_L1_unbalanced_transaction_is_rejected_by_the_database(session, accounts):
    """The money-cannot-vanish check. Enforced by trigger, not application code."""
    post(
        session,
        TODAY,
        "Broken",
        [(accounts["current"], "-45.00"), (accounts["groceries"], "40.00")],
        commit=False,
    )
    with pytest.raises(ProgrammingError, match="Invariant L1"):
        session.commit()


def test_L1_single_leg_transaction_is_rejected(session, accounts):
    post(session, TODAY, "One leg", [(accounts["current"], "0.00")], commit=False)
    with pytest.raises(ProgrammingError, match="Invariant L1"):
        session.commit()


def test_L1_holds_for_raw_sql_writes_too(session, accounts):
    """The invariant is a property of the database, not of this ORM layer."""
    from sqlalchemy import text

    txn = post(
        session,
        TODAY,
        "Tesco",
        [(accounts["current"], "-45.00"), (accounts["groceries"], "45.00")],
    )
    session.execute(
        text(
            "INSERT INTO postings (id, transaction_id, account_id, amount, currency,"
            " created_at, updated_at) VALUES (gen_random_uuid(), :t, :a, 10, 'GBP',"
            " now(), now())"
        ),
        {"t": txn.id, "a": accounts["current"].id},
    )
    with pytest.raises(ProgrammingError, match="Invariant L1"):
        session.commit()


def test_L1_multi_leg_split_is_allowed(session, accounts):
    """A debt payment splits principal and interest -- three legs, still balanced."""
    txn = post(
        session,
        TODAY,
        "Car loan payment",
        [
            (accounts["current"], "-300.00"),
            (accounts["loan"], "250.00"),
            (accounts["interest"], "50.00"),
        ],
    )
    assert len(txn.postings) == 3
    assert classify(txn, kinds_map(accounts)) is TransactionClass.DEBT_PAYMENT


# --------------------------------------------------------------------------
# N1 -- transfers preserve net worth
# --------------------------------------------------------------------------


def test_N1_transfer_between_owned_accounts_leaves_net_worth_unchanged(
    session, accounts
):
    before = net_worth(session)
    post(
        session,
        TODAY,
        "To savings",
        [(accounts["current"], "-500.00"), (accounts["savings"], "500.00")],
    )
    assert net_worth(session) == before


def test_N1_expense_reduces_net_worth(session, accounts):
    before = net_worth(session)
    post(
        session,
        TODAY,
        "Tesco",
        [(accounts["current"], "-45.00"), (accounts["groceries"], "45.00")],
    )
    assert net_worth(session) == before - Decimal("45.00")


def test_N1_debt_payment_reduces_net_worth_by_the_interest_only(session, accounts):
    """The principal moves between asset and liability; only interest is a real cost."""
    before = net_worth(session)
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
    assert net_worth(session) == before - Decimal("50.00")


def test_N1_nominal_accounts_are_excluded_from_net_worth(session, accounts):
    before = net_worth(session)
    post(
        session,
        TODAY,
        "Salary",
        [(accounts["current"], "2500.00"), (accounts["salary"], "-2500.00")],
    )
    assert net_worth(session) == before + Decimal("2500.00")


# --------------------------------------------------------------------------
# Derived classification (rulebook section 2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description,legs,expected",
    [
        ("Salary", [("current", "2500"), ("salary", "-2500")], TransactionClass.INCOME),
        ("Tesco", [("current", "-45"), ("groceries", "45")], TransactionClass.EXPENSE),
        ("Refund", [("current", "45"), ("groceries", "-45")], TransactionClass.REFUND),
        (
            "Cash withdrawal",
            [("current", "-100"), ("cash", "100")],
            TransactionClass.TRANSFER,
        ),
        (
            "To savings",
            [("current", "-500"), ("savings", "500")],
            TransactionClass.SAVINGS_TRANSFER,
        ),
        (
            "To ISA",
            [("current", "-250"), ("investment", "250")],
            TransactionClass.INVESTMENT_CONTRIBUTION,
        ),
    ],
)
def test_classification_is_derived_from_account_kinds(
    session, accounts, description, legs, expected
):
    txn = post(
        session, TODAY, description, [(accounts[k], amt) for k, amt in legs]
    )
    assert classify(txn, kinds_map(accounts)) is expected


def test_savings_and_plain_transfers_differ_only_by_destination_account_kind(
    session, accounts
):
    """The distinction lives on the account, not on a stored transaction type."""
    plain = post(
        session,
        TODAY,
        "To cash",
        [(accounts["current"], "-100"), (accounts["cash"], "100")],
    )
    savings = post(
        session,
        TODAY,
        "To savings",
        [(accounts["current"], "-100"), (accounts["savings"], "100")],
    )
    kinds = kinds_map(accounts)
    assert classify(plain, kinds) is TransactionClass.TRANSFER
    assert classify(savings, kinds) is TransactionClass.SAVINGS_TRANSFER
    # Identical shape; only the destination kind differs.
    assert accounts["cash"].kind is AccountKind.CASH
    assert accounts["savings"].kind is AccountKind.SAVINGS
