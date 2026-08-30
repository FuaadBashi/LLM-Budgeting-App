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

    expense_net = by_kind.get(AccountKind.EXPENSE, Decimal("0"))

    if AccountKind.LIABILITY in touched:
        # Neither the presence of a liability nor its direction is enough on its
        # own -- a repayment and a card refund both shrink the liability. What
        # separates them is the sign of the expense leg: interest on a repayment
        # is incurred (positive), whereas a refund reverses spending (negative).
        if by_kind[AccountKind.LIABILITY] > 0:
            if expense_net < 0:
                return TransactionClass.REFUND
            return TransactionClass.DEBT_PAYMENT

        # Liability growing: it funded something. If that something is an expense,
        # it is still spending -- groceries bought on a credit card are an expense,
        # not a species of borrowing -- so fall through rather than swallow it.
        if AccountKind.EXPENSE not in touched:
            # Pure drawdown with nothing bought. v1 does not model borrowing.
            return TransactionClass.UNCLASSIFIED

    if AccountKind.EXPENSE in touched:
        if expense_net < 0:
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
