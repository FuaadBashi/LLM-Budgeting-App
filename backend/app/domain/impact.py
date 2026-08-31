"""What a single transaction did to the budgets it touched. Warning W3.

``material_single_expense`` compares the daily allowance before and after one
transaction. Nothing could supply that pair until transactions could be posted
through the app, so the warning existed, was tested, and never fired.

The comparison holds **today fixed** and varies only the transaction. Measuring
the allowance on two different days would conflate "this expense hurt" with "a
day passed", and the second is not news.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.budget_warnings import BudgetWarning, material_single_expense
from app.domain.budgets import current_period
from app.domain.clock import today as clock_today
from app.models.ledger import Transaction, TransactionStatus
from app.models.planning import Budget


@dataclass(frozen=True)
class BudgetImpact:
    budget_id: object
    budget_name: str
    allowance_before: object
    allowance_after: object
    warning: BudgetWarning


def assess_transaction(
    session: Session, transaction_id, today: date | None = None
) -> list[BudgetImpact]:
    """Re-measure every budget with and without one posted transaction.

    The "before" figure is obtained by voiding the transaction inside a savepoint
    and recomputing, then rolling back. That is heavier than arithmetic, but it
    reuses the real engine: the alternative -- reimplementing "what would Spent
    have been" -- is a second implementation of `Spent` that will drift from the
    first, which is the failure X8 exists to prevent.
    """
    today = today or clock_today(session)

    txn = session.get(Transaction, transaction_id)
    if txn is None or txn.status != TransactionStatus.POSTED:
        return []

    budgets = list(session.scalars(select(Budget)))
    if not budgets:
        return []

    after = {b.id: current_period(session, b, today) for b in budgets}

    # Savepoint: flip the transaction out of scope, recompute, then undo. Nothing
    # is committed, so the ledger is untouched (invariant P1 in spirit -- an
    # assessment must never mutate what it measures).
    nested = session.begin_nested()
    try:
        txn.status = TransactionStatus.VOIDED
        session.flush()
        session.expire_all()
        before = {b.id: current_period(session, b, today) for b in budgets}
    finally:
        nested.rollback()
        session.expire_all()

    impacts: list[BudgetImpact] = []
    for budget in budgets:
        b_before, b_after = before.get(budget.id), after.get(budget.id)
        if b_before is None or b_after is None:
            continue
        if b_before.base_allowance is None or b_after.base_allowance is None:
            continue
        if b_before.base_allowance == b_after.base_allowance:
            continue
        impacts.append(
            BudgetImpact(
                budget_id=budget.id,
                budget_name=budget.name,
                allowance_before=b_before.base_allowance,
                allowance_after=b_after.base_allowance,
                warning=material_single_expense(
                    b_before.base_allowance, b_after.base_allowance
                ),
            )
        )
    return impacts
