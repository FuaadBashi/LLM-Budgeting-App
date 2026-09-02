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


def _offsets(
    session: Session, category_id: uuid.UUID | None, start: date, end: date
):
    """Walk every reimbursement in the window once, yielding what it actually
    offset. The single source both netting views below are read from -- a
    second, similar-looking walk over the same reimbursements would drift the
    moment one of them changed, which is exactly what happened here before
    :func:`merchant_netting_by_booking_date` existed: the merchant baseline read
    raw expense totals while the budget engine read the netted ones, and a fully
    reimbursed trip could trip the anomaly warning for spend the ledger itself
    treats as zero.

    Yields ``(original_transaction, posting, applied, reimbursement_booking_date)``
    for every expense leg an in-window reimbursement actually offset, plus
    accumulates any excess (repaid beyond what was spent, which is income rather
    than negative spending) onto the returned running total.
    """
    ids = scope_ids(session, category_id)

    reimbursements = session.scalars(
        select(Transaction)
        .where(Transaction.reimburses_id.is_not(None))
        .where(Transaction.id.in_(posted_transaction_ids(start=start, end=end)))
    ).all()

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
            yield original, posting, applied, reimbursement.booking_date

    return excess


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
    """
    daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
    walk = _offsets(session, category_id, start, end)
    while True:
        try:
            _original, _posting, applied, when = next(walk)
        except StopIteration as stop:
            return sorted(daily.items()), stop.value
        daily[when] += applied


def merchant_netting_by_booking_date(
    session: Session,
    category_id: uuid.UUID | None,
    start: date,
    end: date,
) -> tuple[list[tuple[str, date, Decimal]], Decimal]:
    """The same offsets as :func:`netting_by_booking_date`, split by merchant.

    ``merchant`` lives on ``Transaction``, not ``Posting``, so every expense leg
    of one original transaction shares one merchant -- there is no per-leg
    ambiguity to resolve. A reimbursed transaction with no merchant recorded
    contributes no row, matching :func:`app.domain.spend.merchant_spend_by_booking_date`,
    which drops the same rows for the same reason: "who was this with?" has no
    answer to net against.
    """
    daily: dict[tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    walk = _offsets(session, category_id, start, end)
    while True:
        try:
            original, _posting, applied, when = next(walk)
        except StopIteration as stop:
            rows = sorted((m, d, v) for (m, d), v in daily.items())
            return rows, stop.value
        if original.merchant is None:
            continue
        daily[(original.merchant, when)] += applied


def _in_scope(session: Session, posting: Posting, ids: set[uuid.UUID] | None) -> bool:
    """Same scope predicate `Spent` uses, applied to the original expense leg."""
    if ids is not None:
        return posting.category_id in ids
    # Null scope: total discretionary, and uncategorised counts as discretionary.
    if posting.category_id is None:
        return True
    category = session.get(Category, posting.category_id)
    return category is not None and category.nature == CategoryNature.DISCRETIONARY
