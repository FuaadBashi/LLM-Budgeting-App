"""Historical summaries. Plan section 8; Phase 5.

Every figure here is a re-aggregation of postings. Nothing is stored, and nothing
is computed a second way: the exit gate for this phase is that report numbers
reconcile to the ledger, which is only meaningful if the report and the ledger
read the same rows. Both go through ``posted_transaction_ids``.

Two rates, because they answer different questions and neither is a substitute
for the other:

* **savings rate** -- ``(income - spending) / income``. The standard household
  definition used by national statistics offices and personal finance generally:
  what fraction of income was not consumed. Money left sitting in a current
  account counts, because it was in fact not spent.
* **set-aside rate** -- ``(moved to savings and investments) / income``. What was
  deliberately put beyond easy reach. Zero for someone who simply underspends.

Reporting only the second was the earlier mistake: it told a careful saver with
no separate savings account that they saved nothing.

Either way a transfer is not spending and not income. That part is not a
convention -- under double entry, moving money between your own accounts touches
no nominal account at all.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.ledger_scope import posted_transaction_ids
from app.models.enums import AccountKind
from app.models.ledger import Account, Category, Posting, Transaction

ZERO = Decimal("0")


@dataclass(frozen=True)
class CategoryTotal:
    category_id: object
    name: str
    amount: Decimal


@dataclass(frozen=True)
class PeriodSummary:
    start: date
    end: date
    income: Decimal
    expense: Decimal
    #: Money moved into savings or investment accounts. A transfer, not spending.
    saved: Decimal
    net: Decimal
    #: (income - spending) / income. The standard definition.
    savings_rate: Decimal | None
    #: Deliberately moved to savings or investments, as a share of income.
    set_aside_rate: Decimal | None
    by_category: list[CategoryTotal] = field(default_factory=list)
    by_merchant: list[tuple[str, Decimal]] = field(default_factory=list)

    def explain(self) -> list[tuple[str, Decimal]]:
        return [("Income", self.income), ("Spending", -self.expense)]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _sum_over_kind(
    session: Session, kind: AccountKind, start: date, end: date
) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Account, Posting.account_id == Account.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind == kind)
    )
    return total or ZERO


def summarise(session: Session, start: date, end: date) -> PeriodSummary:
    """Income, spending and saving over ``[start, end]``."""
    # Income legs are credit-normal on a nominal account, so the sum is negative.
    income = -_sum_over_kind(session, AccountKind.INCOME_SOURCE, start, end)
    expense = _sum_over_kind(session, AccountKind.EXPENSE, start, end)

    saved = ZERO
    for kind in (AccountKind.SAVINGS, AccountKind.INVESTMENT):
        saved += _sum_over_kind(session, kind, start, end)

    # Undefined without income rather than zero: "saved 0%" and "no income this
    # period" are different statements and must not render the same.
    savings_rate = ((income - expense) / income) if income > ZERO else None
    set_aside_rate = (saved / income) if income > ZERO else None

    return PeriodSummary(
        start=start,
        end=end,
        income=income,
        expense=expense,
        saved=saved,
        net=income - expense,
        savings_rate=savings_rate,
        set_aside_rate=set_aside_rate,
        by_category=_by_category(session, start, end),
        by_merchant=_by_merchant(session, start, end),
    )


def _by_category(session: Session, start: date, end: date) -> list[CategoryTotal]:
    """Expense totals per category, largest first. Uncategorised is named, not dropped."""
    rows = session.execute(
        select(
            Posting.category_id,
            func.coalesce(Category.name, "Uncategorised").label("name"),
            func.sum(Posting.amount).label("total"),
        )
        .join(Account, Posting.account_id == Account.id)
        .outerjoin(Category, Posting.category_id == Category.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind == AccountKind.EXPENSE)
        .group_by(Posting.category_id, Category.name)
    ).all()
    return sorted(
        (CategoryTotal(r.category_id, r.name, r.total or ZERO) for r in rows),
        key=lambda c: -c.amount,
    )


def _by_merchant(session: Session, start: date, end: date) -> list[tuple[str, Decimal]]:
    rows = session.execute(
        select(Transaction.merchant, func.sum(Posting.amount).label("total"))
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Posting.account_id == Account.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind == AccountKind.EXPENSE)
        .where(Transaction.merchant.is_not(None))
        .group_by(Transaction.merchant)
    ).all()
    return sorted(((r.merchant, r.total or ZERO) for r in rows), key=lambda m: -m[1])


def monthly_series(
    session: Session, first: date, last: date
) -> list[PeriodSummary]:
    """One summary per calendar month across the range, oldest first."""
    out: list[PeriodSummary] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        start, end = month_bounds(year, month)
        out.append(summarise(session, start, end))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out
