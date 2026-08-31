"""HTTP routes.

Plan section 12.4: the client requests *actions*, never writes derived totals.
There is deliberately no endpoint that sets a balance, a spending total or a
safe-to-spend figure -- those are always computed from postings.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountIn,
    AccountOut,
    CategoryOut,
    NetWorthOut,
    PostingOut,
    SafeToSpendOut,
    TransactionIn,
    TransactionOut,
    from_minor,
    to_minor,
)
from app.db import get_session
from app.domain.classification import classify
from app.domain.clock import today as clock_today
from app.domain.disposable import account_balances, compute_safe_to_spend, net_worth
from app.models import Account, Category, Posting, Transaction

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[AccountOut]:
    balances = account_balances(session)
    return [
        AccountOut(
            id=a.id,
            name=a.name,
            kind=a.kind,
            currency=a.currency,
            balance_minor=to_minor(balances.get(a.id, from_minor(0))),
        )
        for a in session.scalars(select(Account).order_by(Account.name))
    ]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountIn, session: Session = Depends(get_session)
) -> AccountOut:
    account = Account(
        name=payload.name,
        kind=payload.kind,
        currency=payload.currency,
        opening_balance=from_minor(payload.opening_balance_minor),
    )
    session.add(account)
    session.commit()
    return AccountOut(
        id=account.id,
        name=account.name,
        kind=account.kind,
        currency=account.currency,
        balance_minor=payload.opening_balance_minor,
    )


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(session: Session = Depends(get_session)) -> list[Category]:
    """Expose category ids for transaction entry without exposing derived totals."""
    return list(session.scalars(select(Category).order_by(Category.name)))


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


def _to_out(txn: Transaction, kinds: dict) -> TransactionOut:
    return TransactionOut(
        id=txn.id,
        booking_date=txn.booking_date,
        description=txn.description,
        merchant=txn.merchant,
        classification=classify(txn, kinds),
        postings=[
            PostingOut(
                id=p.id,
                account_id=p.account_id,
                amount_minor=to_minor(p.amount),
                category_id=p.category_id,
            )
            for p in txn.postings
        ],
    )


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(
    payload: TransactionIn, session: Session = Depends(get_session)
) -> TransactionOut:
    txn = Transaction(
        booking_date=payload.booking_date,
        occurred_at=payload.occurred_at
        or datetime.combine(
            payload.booking_date, datetime.min.time(), tzinfo=timezone.utc
        ),
        description=payload.description,
        merchant=payload.merchant,
    )
    for leg in payload.postings:
        txn.postings.append(
            Posting(
                account_id=leg.account_id,
                amount=from_minor(leg.amount_minor),
                category_id=leg.category_id,
            )
        )
    session.add(txn)
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        # The L1 trigger fires at commit; surface it as a client error, not a 500.
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc

    kinds = {a.id: a.kind for a in session.scalars(select(Account))}
    return _to_out(txn, kinds)


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    limit: int = 50, session: Session = Depends(get_session)
) -> list[TransactionOut]:
    kinds = {a.id: a.kind for a in session.scalars(select(Account))}
    rows = session.scalars(
        select(Transaction).order_by(Transaction.booking_date.desc()).limit(limit)
    )
    return [_to_out(t, kinds) for t in rows]


# --------------------------------------------------------------------------
# Dashboard figures -- computed, never stored
# --------------------------------------------------------------------------


@router.get("/dashboard/safe-to-spend", response_model=SafeToSpendOut)
def safe_to_spend(
    as_of: date | None = None, session: Session = Depends(get_session)
) -> SafeToSpendOut:
    r = compute_safe_to_spend(session, as_of)
    return SafeToSpendOut(
        safe_to_spend_minor=to_minor(r.safe_to_spend),
        total_accessible_minor=to_minor(r.total_accessible),
        cash_minor=to_minor(r.cash),
        near_term_committed_minor=to_minor(r.near_term_committed),
        protected_buffer_minor=to_minor(r.protected_buffer),
        remaining_planned_minor=to_minor(r.remaining_planned),
        unprotected_savings_minor=to_minor(r.unprotected_savings),
        flexible_planned_release_minor=to_minor(r.flexible_planned_release),
        window_end=r.window_end,
        breakdown=[(label, to_minor(v)) for label, v in r.explain()],
    )


@router.get("/dashboard/net-worth", response_model=NetWorthOut)
def get_net_worth(
    as_of: date | None = None, session: Session = Depends(get_session)
) -> NetWorthOut:
    as_of = as_of or clock_today(session)
    return NetWorthOut(net_worth_minor=to_minor(net_worth(session, as_of)), as_of=as_of)
