"""What counts as spending against a budget. Rulebook section 8, invariant B1.

``Spent`` is a **signed, posting-level sum over expense-kind legs only**. Both
halves of that sentence are load-bearing:

* *Posting-level*, because one transaction can split across two category budgets.
  Measuring the cash leg makes that impossible to express and charges a Groceries
  budget the whole £80 of a shop that was half household goods.
* *Expense-kind*, because filtering on category alone nets a transaction whose
  legs are both tagged to £0.00 -- a silent zero, the worst kind of wrong.

Everything else follows with no special-casing. Transfers and savings movements
never touch an expense account, so section 2's "transfers are not spending" is
enforced by the same filter rather than by a second rule that can drift. A card
purchase counts because its expense leg counts, whatever the funding side looks
like -- which is why Spent is never derived from ``TransactionClass``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.categories import scope_ids
from app.domain.ledger_scope import posted_transaction_ids
from app.models.enums import AccountKind, CategoryNature
from app.models.ledger import Account, Category, Posting, Transaction

ZERO = Decimal("0")

def _legs_in_scope(
    session: Session,
    category_id: uuid.UUID | None,
    start: date,
    end: date,
    *columns,
):
    """The posting set every Spent-shaped query must agree on.

    Extracted rather than repeated. Two similar-looking queries over the same
    money always drift, and the drift is silent -- a merchant baseline built from
    a slightly different posting set would quote a history the budget never had,
    then compare this period against it and call the difference an anomaly.
    """
    ids = scope_ids(session, category_id)
    query = (
        select(*columns)
        .join(Transaction, Posting.transaction_id == Transaction.id)
        .join(Account, Posting.account_id == Account.id)
        .outerjoin(Category, Posting.category_id == Category.id)
        .where(
            Posting.transaction_id.in_(
                posted_transaction_ids(start=start, end=end)
            )
        )
        .where(Account.kind == AccountKind.EXPENSE)
    )
    if ids is None:
        query = query.where(
            or_(
                Posting.category_id.is_(None),
                Category.nature == CategoryNature.DISCRETIONARY,
            )
        )
    else:
        query = query.where(Posting.category_id.in_(ids))
    return query


def spend_by_booking_date(
    session: Session,
    category_id: uuid.UUID | None,
    start: date,
    end: date,
) -> list[tuple[date, Decimal]]:
    """Daily expense totals in scope, over ``[start, end]`` inclusive.

    Returns only dates that actually have spend, which stays small even for a
    multi-year daily budget.
    """
    query = _legs_in_scope(
        session,
        category_id,
        start,
        end,
        Transaction.booking_date,
        func.sum(Posting.amount).label("total"),
    )
    rows = session.execute(
        query.group_by(Transaction.booking_date).order_by(Transaction.booking_date)
    ).all()
    return [(r.booking_date, r.total or ZERO) for r in rows]


def merchant_spend_by_booking_date(
    session: Session,
    category_id: uuid.UUID | None,
    start: date,
    end: date,
) -> list[tuple[str, date, Decimal]]:
    """The same totals as :func:`spend_by_booking_date`, split by merchant.

    Bucketing into periods happens in Python, not here. SQL can group by month,
    but not by a fortnight measured from an arbitrary anchor, and ``EXTRACT(DOW)``
    is Sunday-based where ``date.weekday()`` is Monday-based -- so a SQL-side
    grouping would be right for two of the six period kinds and quietly wrong for
    the rest.

    Rows with no merchant are dropped: "who was this with?" has no answer for
    them, and a single unnamed bucket would pool every untagged expense into one
    fictional merchant whose spend is anomalous every period.
    """
    query = _legs_in_scope(
        session,
        category_id,
        start,
        end,
        Transaction.merchant,
        Transaction.booking_date,
        func.sum(Posting.amount).label("total"),
    ).where(Transaction.merchant.is_not(None))
    rows = session.execute(
        query.group_by(Transaction.merchant, Transaction.booking_date).order_by(
            Transaction.booking_date
        )
    ).all()
    return [(r.merchant, r.booking_date, r.total or ZERO) for r in rows]


def total_between(
    daily: list[tuple[date, Decimal]], start: date, end: date
) -> Decimal:
    """Sum the pre-fetched daily rows falling inside ``[start, end]``."""
    return sum((v for d, v in daily if start <= d <= end), ZERO)


def uncategorised_between(
    session: Session, start: date, end: date
) -> Decimal:
    """Expense spend carrying no category, for the null-scope breakdown.

    Surfaced as a named component rather than folded in silently: a user whose
    imported statement is entirely untagged should be able to see why their
    discretionary budget moved.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Transaction, Posting.transaction_id == Transaction.id)
        .join(Account, Posting.account_id == Account.id)
        .where(
            Posting.transaction_id.in_(
                posted_transaction_ids(start=start, end=end)
            )
        )
        .where(Account.kind == AccountKind.EXPENSE)
        .where(Posting.category_id.is_(None))
    )
    return total or ZERO
