"""Analytics and export. Plan sections 8 and 10; Phase 5.

Exports are posting-level, not transaction-level. A transaction has no single
amount -- that is the whole point of the double-entry model -- so a row-per-
transaction CSV would have to invent one, and any invented figure is the one
that stops reconciling.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import to_minor
from app.db import get_session
from app.domain import analytics
from app.domain.clock import today as clock_today
from app.domain.ledger_scope import posted_transaction_ids
from app.models import Account, Category, Posting, Transaction

router = APIRouter()


class CategoryTotalOut(BaseModel):
    name: str
    amount_minor: int


class PeriodSummaryOut(BaseModel):
    start: date
    end: date
    income_minor: int
    expense_minor: int
    saved_minor: int
    net_minor: int
    #: None when there was no income -- "0% saved" and "no income" differ.
    savings_rate: float | None
    by_category: list[CategoryTotalOut]
    by_merchant: list[tuple[str, int]]


def _summary_out(s: analytics.PeriodSummary) -> PeriodSummaryOut:
    return PeriodSummaryOut(
        start=s.start,
        end=s.end,
        income_minor=to_minor(s.income),
        expense_minor=to_minor(s.expense),
        saved_minor=to_minor(s.saved),
        net_minor=to_minor(s.net),
        savings_rate=float(s.savings_rate) if s.savings_rate is not None else None,
        by_category=[
            CategoryTotalOut(name=c.name, amount_minor=to_minor(c.amount))
            for c in s.by_category
        ],
        by_merchant=[(name, to_minor(total)) for name, total in s.by_merchant],
    )


@router.get("/analytics/period", response_model=PeriodSummaryOut)
def period_summary(
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
) -> PeriodSummaryOut:
    today = clock_today(session)
    if start is None or end is None:
        start, end = analytics.month_bounds(today.year, today.month)
    return _summary_out(analytics.summarise(session, start, end))


@router.get("/analytics/monthly", response_model=list[PeriodSummaryOut])
def monthly(
    first: date | None = None,
    last: date | None = None,
    session: Session = Depends(get_session),
) -> list[PeriodSummaryOut]:
    today = clock_today(session)
    last = last or today
    first = first or date(last.year, 1, 1)
    return [_summary_out(s) for s in analytics.monthly_series(session, first, last)]


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _rows(session: Session, start: date, end: date):
    """One row per posting, joined to its transaction and account."""
    return session.execute(
        select(Transaction, Posting, Account, Category)
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Posting.account_id == Account.id)
        .outerjoin(Category, Posting.category_id == Category.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .order_by(Transaction.booking_date, Transaction.id)
    ).all()


@router.get("/export/transactions.csv")
def export_csv(
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Posting-level CSV. Amounts are decimal strings, never floats."""
    today = clock_today(session)
    start = start or date(today.year, 1, 1)
    end = end or today

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "booking_date",
            "transaction_id",
            "description",
            "merchant",
            "account",
            "account_kind",
            "category",
            "amount",
            "currency",
        ]
    )
    for txn, posting, account, category in _rows(session, start, end):
        writer.writerow(
            [
                txn.booking_date.isoformat(),
                str(txn.id),
                txn.description,
                txn.merchant or "",
                account.name,
                account.kind.value,
                category.name if category else "",
                # str(Decimal) keeps the exact scale; float() would not.
                str(posting.amount),
                posting.currency,
            ]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="transactions-{start}-{end}.csv"'
        },
    )


@router.get("/export/backup.json")
def export_json(session: Session = Depends(get_session)) -> StreamingResponse:
    """Full-fidelity machine-readable backup of the ledger.

    Decimals are serialised as strings. JSON has no decimal type, so emitting
    them as numbers would round-trip through a float and quietly change the
    figures a backup exists to preserve.
    """
    accounts = [
        {
            "id": str(a.id),
            "name": a.name,
            "kind": a.kind.value,
            "currency": a.currency,
            "opening_balance": str(a.opening_balance),
            "active": a.active,
        }
        for a in session.scalars(select(Account).order_by(Account.name))
    ]
    categories = [
        {
            "id": str(c.id),
            "name": c.name,
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "nature": c.nature.value,
        }
        for c in session.scalars(select(Category).order_by(Category.name))
    ]
    transactions = []
    for txn in session.scalars(select(Transaction).order_by(Transaction.booking_date)):
        transactions.append(
            {
                "id": str(txn.id),
                "booking_date": txn.booking_date.isoformat(),
                "occurred_at": txn.occurred_at.isoformat(),
                "description": txn.description,
                "merchant": txn.merchant,
                "status": txn.status.value,
                "source": txn.source,
                "reverses_id": str(txn.reverses_id) if txn.reverses_id else None,
                "reimburses_id": str(txn.reimburses_id) if txn.reimburses_id else None,
                "postings": [
                    {
                        "id": str(p.id),
                        "account_id": str(p.account_id),
                        "category_id": str(p.category_id) if p.category_id else None,
                        "amount": str(p.amount),
                        "currency": p.currency,
                    }
                    for p in txn.postings
                ],
            }
        )

    payload = json.dumps(
        {
            "format": "personal-finance-os/backup",
            "version": 1,
            "exported_for": clock_today(session).isoformat(),
            "accounts": accounts,
            "categories": categories,
            "transactions": transactions,
        },
        indent=1,
    )
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="backup.json"'},
    )
