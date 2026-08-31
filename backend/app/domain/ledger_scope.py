"""Shared transaction-set selectors used by every ledger-derived engine."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.ledger import Transaction
from app.models.enums import TransactionStatus


def posted_transaction_ids(*, start: date | None = None, end: date | None = None):
    """IDs of posted transactions in an optional inclusive booking-date window.

    Budget spend and account balances must resolve transaction lifecycle the same
    way. Keeping the status and date predicates here makes candidate/void drift a
    single-source rule rather than two similar-looking query fragments.
    """
    query = select(Transaction.id).where(
        Transaction.status == TransactionStatus.POSTED
    )
    if start is not None:
        query = query.where(Transaction.booking_date >= start)
    if end is not None:
        query = query.where(Transaction.booking_date <= end)
    return query
