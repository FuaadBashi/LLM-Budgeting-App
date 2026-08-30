"""Derive the plan's eight transaction types from account kinds.

Rulebook section 2. This is a reporting view computed on read; nothing here is
stored. A transaction that touches a liability and an expense is a debt payment
because of what it *does*, not because someone tagged it.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.enums import AccountKind, TransactionClass
from app.models.ledger import Transaction


def classify(txn: Transaction, kinds: dict) -> TransactionClass:
    """Classify a transaction from the kinds of the accounts its postings touch.

    ``kinds`` maps account_id -> AccountKind, supplied by the caller so this stays
    a pure function and callers can batch the account lookup.
    """
    if not txn.postings:
        return TransactionClass.UNCLASSIFIED

    # Net movement per account kind. Sign convention: debits positive.
    by_kind: dict[AccountKind, Decimal] = {}
    for p in txn.postings:
        kind = kinds[p.account_id]
        by_kind[kind] = by_kind.get(kind, Decimal("0")) + p.amount

    touched = set(by_kind)

    if AccountKind.INCOME_SOURCE in touched:
        if txn.reimburses_id is not None:
            return TransactionClass.REIMBURSEMENT
        return TransactionClass.INCOME

    if AccountKind.LIABILITY in touched:
        # Reducing a liability is a debt payment; increasing it is borrowing,
        # which v1 records as unclassified rather than guessing.
        if by_kind[AccountKind.LIABILITY] > 0:
            return TransactionClass.DEBT_PAYMENT
        return TransactionClass.UNCLASSIFIED

    if AccountKind.EXPENSE in touched:
        net = by_kind[AccountKind.EXPENSE]
        if net < 0:
            return TransactionClass.REFUND
        return TransactionClass.EXPENSE

    # Asset-to-asset: a transfer. Which kind depends on where the money landed.
    destinations = {
        kinds[p.account_id] for p in txn.postings if p.amount > 0
    }
    if AccountKind.INVESTMENT in destinations:
        return TransactionClass.INVESTMENT_CONTRIBUTION
    if AccountKind.SAVINGS in destinations:
        return TransactionClass.SAVINGS_TRANSFER
    if destinations:
        return TransactionClass.TRANSFER

    return TransactionClass.UNCLASSIFIED
