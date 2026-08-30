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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.categories import scope_ids
from app.models.enums import CategoryNature

ZERO = Decimal("0")

# One grouped query per budget. Bucketing into periods happens in Python, so SQL
# never performs the date arithmetic that differs between the two languages.
_SPEND_SQL = text(
    """
    SELECT t.booking_date, SUM(p.amount) AS total
      FROM postings p
      JOIN transactions t ON p.transaction_id = t.id
      JOIN accounts     a ON p.account_id     = a.id
      LEFT JOIN categories c ON p.category_id = c.id
     WHERE t.status = 'POSTED'
       AND a.kind   = 'EXPENSE'
       AND t.booking_date BETWEEN :start AND :end
       AND (
             (:scoped = FALSE AND (p.category_id IS NULL OR c.nature = :discretionary))
             OR
             (:scoped = TRUE  AND p.category_id = ANY(:ids))
           )
     GROUP BY t.booking_date
     ORDER BY t.booking_date
    """
)


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
    ids = scope_ids(session, category_id)
    rows = session.execute(
        _SPEND_SQL,
        {
            "start": start,
            "end": end,
            "scoped": ids is not None,
            "ids": list(ids) if ids else [],
            "discretionary": CategoryNature.DISCRETIONARY.name,
        },
    ).all()
    return [(r.booking_date, r.total or ZERO) for r in rows]


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
    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(p.amount), 0) AS total
              FROM postings p
              JOIN transactions t ON p.transaction_id = t.id
              JOIN accounts     a ON p.account_id     = a.id
             WHERE t.status = 'POSTED'
               AND a.kind   = 'EXPENSE'
               AND p.category_id IS NULL
               AND t.booking_date BETWEEN :start AND :end
            """
        ),
        {"start": start, "end": end},
    ).one()
    return row.total or ZERO
