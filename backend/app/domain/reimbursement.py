"""Netting reimbursements out of budget spend. Engine spec M3.

A merchant refund and an employer reimbursement are economically identical to a
budget: both give the money back. But a refund touches the expense account (so
the signed sum already handles it) while a reimbursement touches an
``income_source``. Without netting, a £600 work trip fully repaid by an employer
still annihilates a £600 discretionary budget, and the difference between the two
outcomes is nothing but which counterparty happened to send the money.

The repayment is allocated across the expense legs of the transaction it
reimburses, pro-rata, because one payment can repay a transaction that was split
across several categories.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories import scope_ids
from app.domain.ledger_scope import posted_transaction_ids
from app.models.enums import AccountKind, CategoryNature
from app.models.ledger import Account, Category, Posting, Transaction

ZERO = Decimal("0")
PENCE = Decimal("0.01")


def allocate(total: Decimal, legs: list[Decimal]) -> list[Decimal]:
    """Split ``total`` across ``legs`` pro-rata, in whole pence, summing exactly.

    Largest remainder, because the obvious alternative does not add up: rounding
    each share independently with section 1's banker's rounding turns £50.00 over
    legs of 33.33 / 33.33 / 33.34 into £49.99. Money that vanishes in the split
    reappears as budget the user did not earn.

    Tie-break is explicit -- largest remainder, then largest leg, then index --
    so the result is deterministic rather than dependent on sort stability.
    """
    if not legs:
        return []
    denominator = sum(legs, ZERO)
    if denominator <= ZERO:
        return [ZERO for _ in legs]

    exact = [total * leg / denominator for leg in legs]
    floors = [(e * 100).quantize(Decimal("1"), rounding=ROUND_FLOOR) for e in exact]
    residual = int((total * 100).quantize(Decimal("1"))) - int(sum(floors))

    order = sorted(
        range(len(legs)),
        key=lambda i: (-(exact[i] * 100 - floors[i]), -legs[i], i),
    )
    for i in order[: max(0, residual)]:
        floors[i] += 1

    return [Decimal(f) / 100 for f in floors]


def _reimbursed_amount(session: Session, txn: Transaction) -> Decimal:
    """What a reimbursement transaction actually repaid.

    Measured on the income_source legs and sign-flipped: `current +45 /
    claims -45` is a £45 repayment.
    """
    total = ZERO
    for posting in txn.postings:
        account = session.get(Account, posting.account_id)
        if account is not None and account.kind == AccountKind.INCOME_SOURCE:
            total += posting.amount
    return -total


def netting_by_booking_date(
    session: Session,
    category_id: uuid.UUID | None,
    start: date,
    end: date,
) -> tuple[list[tuple[date, Decimal]], Decimal]:
    """Reimbursement offsets in scope, and any excess that is not spend.

    Returns ``(daily_offsets, excess)``. Offsets are bucketed by the
    **reimbursement's own** booking date, matching the as-booked rule refunds
    follow -- money coming back in September is September's news, whatever month
    the original expense sat in.

    Each link is capped at the original leg amount. Being repaid more than was
    spent is income, not negative spending, and letting it through would let an
    over-payment manufacture budget. The surplus is returned separately so it can
    be reported rather than silently dropped.
    """
    ids = scope_ids(session, category_id)

    reimbursements = session.scalars(
        select(Transaction)
        .where(Transaction.reimburses_id.is_not(None))
        .where(Transaction.id.in_(posted_transaction_ids(start=start, end=end)))
    ).all()

    daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
    excess = ZERO

    for reimbursement in reimbursements:
        repaid = _reimbursed_amount(session, reimbursement)
        if repaid <= ZERO:
            continue

        original = session.get(Transaction, reimbursement.reimburses_id)
        if original is None:
            excess += repaid
            continue

        # Expense legs of the original, in a stable order so allocation is
        # reproducible across runs.
        legs: list[tuple[Posting, Decimal]] = []
        for posting in sorted(original.postings, key=lambda p: str(p.id)):
            account = session.get(Account, posting.account_id)
            if account is not None and account.kind == AccountKind.EXPENSE and posting.amount > ZERO:
                legs.append((posting, posting.amount))

        if not legs:
            excess += repaid
            continue

        shares = allocate(repaid, [amount for _, amount in legs])

        for (posting, leg_amount), share in zip(legs, shares):
            # Cap per link: a single reimbursement can never push a category
            # below zero spend.
            applied = min(share, leg_amount)
            excess += share - applied
            if applied == ZERO:
                continue
            if not _in_scope(session, posting, ids):
                continue
            daily[reimbursement.booking_date] += applied

    return sorted(daily.items()), excess


def _in_scope(session: Session, posting: Posting, ids: set[uuid.UUID] | None) -> bool:
    """Same scope predicate `Spent` uses, applied to the original expense leg."""
    if ids is not None:
        return posting.category_id in ids
    # Null scope: total discretionary, and uncategorised counts as discretionary.
    if posting.category_id is None:
        return True
    category = session.get(Category, posting.category_id)
    return category is not None and category.nature == CategoryNature.DISCRETIONARY
