"""HTTP routes.

Plan section 12.4: the client requests *actions*, never writes derived totals.
There is deliberately no endpoint that sets a balance, a spending total or a
safe-to-spend figure -- those are always computed from postings.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountEditIn,
    AccountIn,
    BudgetImpactOut,
    AccountOut,
    CategoryOut,
    NetWorthOut,
    PostingOut,
    SafeToSpendOut,
    TransactionEditIn,
    TransactionIn,
    TransactionOut,
    from_minor,
    to_minor,
)
from app.db import get_session
from app.domain.categories import apply_account_defaults
from app.domain.classification import classify
from app.domain.clock import today as clock_today
from app.domain.impact import assess_transaction
from app.domain.disposable import account_balances, compute_safe_to_spend, net_worth
from app.models import (
    Account,
    Category,
    Posting,
    Transaction,
    TransactionStatus,
)
from app.models.enums import LIQUID_KINDS, AccountKind

router = APIRouter()


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def _account_out(account: Account, balance_minor: int) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        kind=account.kind,
        currency=account.currency,
        balance_minor=balance_minor,
        default_category_id=account.default_category_id,
    )


def _validate_default_category(
    session: Session, account_kind: AccountKind, category_id: uuid.UUID | None
) -> None:
    """A default category is only read on expense legs, so refuse it elsewhere.

    ``Spent`` is defined over expense-kind legs (invariant B1), which is the only
    place ``apply_account_defaults`` has any effect. Storing one on a current
    account would be accepted, never read, and would show in the UI as a setting
    that does nothing -- accepted-and-ignored, which is the failure this codebase
    returns 422 for rather than shipping.
    """
    if category_id is None:
        return
    if account_kind is not AccountKind.EXPENSE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"a default category has no effect on a {account_kind.value} "
                "account: it is only ever read when stamping an untagged expense "
                "leg, which is what budget Spent is measured over"
            ),
        )
    if session.get(Category, category_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown category {category_id}")


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[AccountOut]:
    balances = account_balances(session)
    return [
        _account_out(a, to_minor(balances.get(a.id, from_minor(0))))
        for a in session.scalars(select(Account).order_by(Account.name))
    ]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountIn, session: Session = Depends(get_session)
) -> AccountOut:
    _validate_default_category(session, payload.kind, payload.default_category_id)
    account = Account(
        name=payload.name,
        kind=payload.kind,
        currency=payload.currency,
        opening_balance=from_minor(payload.opening_balance_minor),
        default_category_id=payload.default_category_id,
    )
    session.add(account)
    session.commit()
    return _account_out(account, payload.opening_balance_minor)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def edit_account(
    account_id: uuid.UUID,
    payload: AccountEditIn,
    session: Session = Depends(get_session),
) -> AccountOut:
    """Set or clear an account's default category.

    Changing it is forward-only: existing postings keep whatever category they
    were stamped with, because re-deriving them would change what a closed period
    meant. ``scripts/backfill_categories.py`` is the explicit, opt-in way to
    apply a new default to history.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    if "default_category_id" in payload.model_fields_set:
        _validate_default_category(session, account.kind, payload.default_category_id)
        account.default_category_id = payload.default_category_id

    session.commit()
    balances = account_balances(session)
    return _account_out(account, to_minor(balances.get(account.id, from_minor(0))))


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
    # What the transaction did to spendable cash. A card purchase moves a budget
    # but not cash, so the two figures legitimately differ (register item X2) --
    # showing it here means the list never has to be reconciled by eye.
    cash_effect = sum(
        (p.amount for p in txn.postings if kinds.get(p.account_id) in LIQUID_KINDS),
        Decimal("0"),
    )
    return TransactionOut(
        id=txn.id,
        booking_date=txn.booking_date,
        description=txn.description,
        merchant=txn.merchant,
        status=txn.status,
        cash_effect_minor=to_minor(cash_effect),
        edited=txn.updated_at > txn.created_at,
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
        reimburses_id=payload.reimburses_id,
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
    # Stamped here, on the write, so the category the budget measures is the one
    # in force when the money moved. Applying it on read instead would silently
    # recategorise closed periods every time an account default changed.
    apply_account_defaults(session, txn.postings)
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        # The L1 trigger fires at commit; surface it as a client error, not a 500.
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc

    kinds = {a.id: a.kind for a in session.scalars(select(Account))}
    out = _to_out(txn, kinds)
    # W3: the only place a before/after allowance pair exists. Reported on the
    # write that caused it rather than surfaced later out of context.
    out.budget_impacts = [
        BudgetImpactOut(
            budget_id=i.budget_id,
            budget_name=i.budget_name,
            allowance_before_minor=to_minor(i.allowance_before),
            allowance_after_minor=to_minor(i.allowance_after),
            delta_minor=to_minor(i.allowance_before - i.allowance_after),
            material=i.warning.fired,
        )
        for i in assess_transaction(session, txn.id)
    ]
    return out


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    limit: int = 50,
    offset: int = 0,
    include_voided: bool = False,
    session: Session = Depends(get_session),
) -> list[TransactionOut]:
    """Most recent first. Voided rows are hidden unless asked for.

    They are never deleted (invariant L3), so they stay reachable -- an audit
    trail you cannot see is not much of one.
    """
    kinds = {a.id: a.kind for a in session.scalars(select(Account))}
    query = select(Transaction)
    if not include_voided:
        query = query.where(Transaction.status != TransactionStatus.VOIDED)
    rows = session.scalars(
        query.order_by(
            Transaction.booking_date.desc(), Transaction.created_at.desc()
        )
        .offset(max(0, offset))
        .limit(min(200, max(1, limit)))
    )
    return [_to_out(t, kinds) for t in rows]


@router.post("/transactions/{transaction_id}/void", response_model=TransactionOut)
def void_transaction(
    transaction_id: uuid.UUID, session: Session = Depends(get_session)
) -> TransactionOut:
    """Mark a transaction as never having happened.

    Voiding is the correction path for a mis-entry; a genuine reversal is a new
    transaction carrying reverses_id. Doing both removes the money twice, which
    the L3 trigger makes unrepresentable -- so a transaction that has already
    been reversed is rejected here rather than silently double-counted.
    """
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    if txn.status == TransactionStatus.VOIDED:
        raise HTTPException(status_code=422, detail="transaction is already voided")

    txn.status = TransactionStatus.VOIDED
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc

    kinds = {a.id: a.kind for a in session.scalars(select(Account))}
    return _to_out(txn, kinds)


#: Refused by name rather than dropped. Naming them lets the reply say what to do
#: instead, which an unknown-key rejection cannot.
MONETARY_FIELDS = ("amount", "amount_minor", "booking_date", "postings")
EDITABLE_FIELDS = ("description", "merchant", "category_id")


@router.patch("/transactions/{transaction_id}", response_model=TransactionOut)
def edit_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionEditIn,
    session: Session = Depends(get_session),
) -> TransactionOut:
    """Correct a non-monetary field in place. Rulebook section 2.

    Money is corrected by void-and-reissue, everything else by editing. Voiding a
    typo is not a smaller version of the right answer, it is a different claim:
    it writes an audit entry saying the money was wrong, breaks the reverses_id
    and reimburses_id links, drops any obligation-fulfilment match so a paid bill
    reappears as unpaid, and leaves the row duplicated in the list for ever.

    A category edit does move a number -- budget ``Spent`` -- but a derived one,
    recomputed from postings on every read, so the new answer is as honest as the
    old one. An amount is a recorded number, which is why it is refused here.
    """
    sent = payload.model_fields_set

    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    if txn.status == TransactionStatus.VOIDED:
        raise HTTPException(
            status_code=422,
            detail=(
                "a voided transaction is not a live record and cannot be edited; "
                "enter a replacement instead"
            ),
        )

    refused = [f for f in MONETARY_FIELDS if f in sent]
    if refused:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(refused)} cannot be edited. A wrong amount or date is "
                "corrected by voiding this transaction "
                f"(POST /api/transactions/{transaction_id}/void) and entering a new "
                "one, so the ledger records that the money itself was wrong."
            ),
        )

    kinds = {a.id: a.kind for a in session.scalars(select(Account))}

    if "category_id" in sent:
        # Expense-kind legs, not category-tagged ones: Spent is defined that way
        # (invariant B1), so this is the same set the budget will measure.
        expense_legs = [
            p for p in txn.postings if kinds.get(p.account_id) == AccountKind.EXPENSE
        ]
        if not expense_legs:
            raise HTTPException(
                status_code=422,
                detail=(
                    "this transaction has no expense leg, so there is nothing for a "
                    "category to describe -- a transfer or savings movement is not "
                    "spending"
                ),
            )
        if len(expense_legs) > 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"this transaction has {len(expense_legs)} expense legs, so one "
                    "category_id does not say which of them to recategorise; "
                    "re-enter the split rather than have the server choose"
                ),
            )
        if (
            payload.category_id is not None
            and session.get(Category, payload.category_id) is None
        ):
            raise HTTPException(
                status_code=422, detail=f"unknown category {payload.category_id}"
            )
        expense_legs[0].category_id = payload.category_id

    if "description" in sent:
        txn.description = payload.description
    if "merchant" in sent:
        txn.merchant = payload.merchant

    if any(f in sent for f in EDITABLE_FIELDS):
        # The mixin's onupdate only fires when the transactions row itself is
        # dirty, and a category edit writes to a posting. Without this an edit
        # that moved budget Spent would report itself as never having happened.
        txn.updated_at = func.now()

    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc

    return _to_out(txn, kinds)


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
