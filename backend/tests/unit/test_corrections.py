"""Invariant L3: exactly one correction mechanism per transaction.

The schema offers two ways to undo a transaction. Using both removes the money
twice, and the failure is silent -- the balance simply comes out wrong.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import ProgrammingError

from app.domain.disposable import account_balances
from app.models import TransactionStatus
from tests.conftest import post

TODAY = date(2026, 8, 15)


def rent(session, accounts, **kwargs):
    return post(
        session,
        TODAY,
        "Rent",
        [(accounts["current"], "-600.00"), (accounts["groceries"], "600.00")],
        **kwargs,
    )


def test_reversal_alone_nets_to_zero(session, accounts):
    original = rent(session, accounts)
    assert account_balances(session)[accounts["current"].id] == Decimal("400.00")

    post(
        session,
        TODAY,
        "Rent reversal",
        [(accounts["current"], "600.00"), (accounts["groceries"], "-600.00")],
        reverses_id=original.id,
    )
    assert account_balances(session)[accounts["current"].id] == Decimal("1000.00")


def test_void_alone_nets_to_zero(session, accounts):
    original = rent(session, accounts)
    original.status = TransactionStatus.VOIDED
    session.commit()
    assert account_balances(session)[accounts["current"].id] == Decimal("1000.00")


def test_voiding_an_already_reversed_transaction_is_rejected(session, accounts):
    """The double-count. Without the trigger this silently returns GBP 1,600."""
    original = rent(session, accounts)
    post(
        session,
        TODAY,
        "Rent reversal",
        [(accounts["current"], "600.00"), (accounts["groceries"], "-600.00")],
        reverses_id=original.id,
    )

    original.status = TransactionStatus.VOIDED
    with pytest.raises(ProgrammingError, match="Invariant L3"):
        session.commit()


def test_reversing_an_already_voided_transaction_is_rejected(session, accounts):
    """The mirror case, reached in the opposite order."""
    original = rent(session, accounts)
    original.status = TransactionStatus.VOIDED
    session.commit()

    post(
        session,
        TODAY,
        "Rent reversal",
        [(accounts["current"], "600.00"), (accounts["groceries"], "-600.00")],
        reverses_id=original.id,
        commit=False,
    )
    with pytest.raises(ProgrammingError, match="Invariant L3"):
        session.commit()


def test_unrelated_voids_and_reversals_still_work(session, accounts):
    """The trigger must not block legitimate corrections of different transactions."""
    a = rent(session, accounts)
    b = rent(session, accounts)

    a.status = TransactionStatus.VOIDED
    session.commit()

    post(
        session,
        TODAY,
        "Rent reversal",
        [(accounts["current"], "600.00"), (accounts["groceries"], "-600.00")],
        reverses_id=b.id,
    )
    assert account_balances(session)[accounts["current"].id] == Decimal("1000.00")
