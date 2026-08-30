"""Safe to Spend and net worth.

Rulebook sections 3-5. Two figures are always produced, never one:

    SafeToSpend      -- what can be spent without breaking any plan
    TotalAccessible  -- what could be spent if flexible savings were raided

Everything is computed from postings plus planning assumptions. Nothing is stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.clock import today as clock_today
from app.models.enums import ASSET_KINDS, LIQUID_KINDS, AccountKind
from app.models.ledger import Account, Posting, Transaction, TransactionStatus
from app.models.planning import (
    ExpectedIncome,
    FutureObligation,
    GoalContribution,
    ObligationInstance,
    SavingsGoal,
    UserProfile,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class SafeToSpend:
    """Every component is exposed so the UI can explain the number (plan section 2)."""

    cash: Decimal
    near_term_committed: Decimal
    protected_buffer: Decimal
    remaining_planned: Decimal
    safe_to_spend: Decimal
    unprotected_savings: Decimal
    total_accessible: Decimal
    window_end: date

    def explain(self) -> list[tuple[str, Decimal]]:
        return [
            ("Liquid cash", self.cash),
            ("Committed before next income", -self.near_term_committed),
            ("Protected buffer", -self.protected_buffer),
            ("Planned contributions still owed", -self.remaining_planned),
        ]


def account_balances(session: Session, as_of: date | None = None) -> dict:
    """Balance per account: opening_balance + sum of posted movements."""
    q = (
        select(Posting.account_id, func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Transaction, Posting.transaction_id == Transaction.id)
        .where(Transaction.status == TransactionStatus.POSTED)
    )
    if as_of is not None:
        q = q.where(Transaction.booking_date <= as_of)
    movements = dict(session.execute(q.group_by(Posting.account_id)).all())

    balances = {}
    for account in session.scalars(select(Account)):
        balances[account.id] = account.opening_balance + movements.get(account.id, ZERO)
    return balances


def net_worth(session: Session, as_of: date | None = None) -> Decimal:
    """Assets minus liabilities, over real (non-nominal) accounts. Invariant N1.

    Liabilities are credit-normal: money owed is stored negative, in the same
    debit-positive convention as every posting. So this is a plain sum with no
    special-casing -- and paying a loan is a transfer that nets to zero, rather
    than something that has to be subtracted twice.
    """
    balances = account_balances(session, as_of)
    real_kinds = ASSET_KINDS | {AccountKind.LIABILITY}
    return sum(
        (
            balances.get(account.id, ZERO)
            for account in session.scalars(select(Account))
            if account.kind in real_kinds
        ),
        ZERO,
    )


def near_term_window_end(session: Session, today: date) -> date:
    """Today until the next expected income date, with a floor (rulebook section 5)."""
    profile = session.scalars(select(UserProfile)).first()
    floor_days = profile.near_term_floor_days if profile else 7
    fallback_days = profile.near_term_fallback_days if profile else 30

    next_income = session.scalars(
        select(ExpectedIncome.next_expected_date)
        .where(ExpectedIncome.active.is_(True))
        .where(ExpectedIncome.next_expected_date >= today)
        .order_by(ExpectedIncome.next_expected_date)
    ).first()

    if next_income is None:
        return today + timedelta(days=fallback_days)
    return max(next_income, today + timedelta(days=floor_days))


def near_term_committed(session: Session, today: date, window_end: date) -> Decimal:
    """Hard obligations due inside the window whose money has not yet left cash.

    Invariant O1: an obligation stops counting once it is paid, because the payment
    is already visible in the ledger. Counting both would charge for rent twice.

    The fulfilment must also be *reflected in cash* to drop out. A future-dated
    transaction that fulfils a future obligation has not moved any money yet, so
    the obligation keeps counting until its booking date arrives -- otherwise
    pre-recording next week's rent would inflate today's safe-to-spend.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(ObligationInstance.amount), ZERO))
        .join(
            FutureObligation,
            ObligationInstance.obligation_id == FutureObligation.id,
        )
        .outerjoin(
            Transaction, ObligationInstance.fulfilled_by_transaction_id == Transaction.id
        )
        .where(ObligationInstance.due_date >= today)
        .where(ObligationInstance.due_date <= window_end)
        .where(FutureObligation.hard.is_(True))
        .where(FutureObligation.active.is_(True))
        .where(
            or_(
                ObligationInstance.fulfilled_by_transaction_id.is_(None),
                Transaction.booking_date > today,
            )
        )
    )
    return total or ZERO


def remaining_planned_contributions(session: Session, today: date) -> Decimal:
    """Planned contributions not yet made this period.

    Invariant S1. The subtraction is ``max(0, planned - contributed)``, never the
    bare planned amount: once the transfer is posted the money is already out of
    cash, and subtracting the plan again would understate safe-to-spend for the
    rest of the month, every month.
    """
    period_start = today.replace(day=1)

    total = ZERO
    for goal in session.scalars(select(SavingsGoal).where(SavingsGoal.active.is_(True))):
        contributed = session.scalar(
            select(func.coalesce(func.sum(GoalContribution.amount), ZERO))
            .where(GoalContribution.goal_id == goal.id)
            .where(GoalContribution.booking_date >= period_start)
            .where(GoalContribution.booking_date <= today)
        ) or ZERO
        outstanding = goal.planned_contribution - contributed
        if outstanding > ZERO:
            total += outstanding
    return total


def compute_safe_to_spend(session: Session, today: date | None = None) -> SafeToSpend:
    """The headline dashboard figure, with its components (rulebook section 4)."""
    # Invariant D1: "today" is a reporting-timezone question, not a server one.
    today = today or clock_today(session)
    balances = account_balances(session, today)

    cash = ZERO
    for account in session.scalars(select(Account).where(Account.active.is_(True))):
        if account.kind in LIQUID_KINDS:
            cash += balances.get(account.id, ZERO)

    window_end = near_term_window_end(session, today)
    committed = near_term_committed(session, today, window_end)

    profile = session.scalars(select(UserProfile)).first()
    buffer_ = profile.protected_cash_buffer if profile else ZERO

    planned = remaining_planned_contributions(session, today)

    # Invariant S2: this may legitimately be negative. A negative value is a real
    # state -- "you are past the point where the plan survives" -- not an error.
    sts = cash - committed - buffer_ - planned

    unprotected = ZERO
    for goal in session.scalars(select(SavingsGoal).where(SavingsGoal.active.is_(True))):
        if not goal.protected:
            unprotected += goal.attributed_balance

    return SafeToSpend(
        cash=cash,
        near_term_committed=committed,
        protected_buffer=buffer_,
        remaining_planned=planned,
        safe_to_spend=sts,
        unprotected_savings=unprotected,
        total_accessible=sts + unprotected,
        window_end=window_end,
    )
