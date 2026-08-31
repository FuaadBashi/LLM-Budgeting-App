"""Derivation traces. Plan section 11; Phase 9.

This module exists because of a decision made in Phase 1: nothing derived is
stored. Safe-to-spend, budget allowances and net worth are recomputed from
postings on every read, which means each one can be *replayed* — and a number
you can replay is a number you can explain.

**Invariant E1: a derivation's terms sum to the figure being explained**, and a
term's parts sum to the term. That is what stops an explanation drifting from
the engine it describes: the alternative is a hand-written list of plausible
components that slowly stops matching the arithmetic, which is worse than no
explanation because it is believed.

Nothing here computes a figure of its own. Every number is read back from the
engine that owns it, so there is no second implementation to disagree with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import budgets as budget_engine
from app.domain.clock import today as clock_today
from app.domain.disposable import (
    account_balances,
    compute_safe_to_spend,
    near_term_committed_rows,
    net_worth,
    planned_contributions_split,
)
from app.domain.money import ZERO
from app.models.enums import LIQUID_KINDS, AccountKind, RolloverPolicy
from app.models.ledger import Account
from app.models.planning import Budget, SavingsGoal


@dataclass(frozen=True)
class Term:
    """One line of a derivation.

    `amount` is signed as it contributes to the total, so the terms can simply be
    added. A term that reduces the figure carries a negative amount rather than a
    positive amount and a subtract-me flag the renderer has to remember.
    """

    label: str
    amount: Decimal
    detail: str = ""
    #: Breakdown, when the term is itself a sum worth seeing.
    parts: tuple[Term, ...] = ()


@dataclass(frozen=True)
class Derivation:
    figure: str
    total: Decimal
    terms: tuple[Term, ...] = ()
    #: Said plainly, for the reader who wants the sentence rather than the sum.
    note: str = ""

    def balances(self) -> bool:
        """E1. Tested, not assumed."""
        return sum((t.amount for t in self.terms), ZERO) == self.total


def safe_to_spend(session: Session, today: date | None = None) -> Derivation:
    """Where the headline number came from.

    The terms are taken from `SafeToSpend.explain()` rather than rebuilt here --
    one arithmetic, one place. This adds the evidence underneath each term: which
    obligations make up the committed figure, which goals make up the planned one.
    """
    today = today or clock_today(session)
    sts = compute_safe_to_spend(session, today)

    liquid: list[Term] = []
    balances = account_balances(session, today)
    for account in session.scalars(
        select(Account).where(Account.active.is_(True)).order_by(Account.name)
    ):
        if account.kind in LIQUID_KINDS:
            amount = balances.get(account.id, ZERO)
            if amount != ZERO:
                liquid.append(Term(label=account.name, amount=amount))

    committed: list[Term] = []
    for instance in near_term_committed_rows(session, today, sts.window_end):
        committed.append(
            Term(
                label=instance.obligation.name,
                amount=-instance.amount,
                detail=f"due {instance.due_date:%-d %B}",
            )
        )

    planned: list[Term] = []
    for goal in session.scalars(
        select(SavingsGoal)
        .where(SavingsGoal.active.is_(True))
        .order_by(SavingsGoal.name)
    ):
        if goal.planned_contribution > ZERO:
            planned.append(
                Term(label=goal.name, amount=-goal.planned_contribution)
            )

    # Each term's amount comes from the engine; the parts are evidence for it.
    # Where the two disagree -- a fulfilled contribution already made, say -- the
    # engine wins and the difference is named rather than hidden.
    def reconcile(parts: list[Term], total: Decimal, why: str) -> tuple[Term, ...]:
        gap = total - sum((p.amount for p in parts), ZERO)
        if gap != ZERO:
            parts = [*parts, Term(label=why, amount=gap)]
        return tuple(parts)

    terms = (
        Term(
            label="Liquid cash",
            amount=sts.cash,
            detail="current and cash accounts",
            parts=reconcile(liquid, sts.cash, "Other liquid accounts"),
        ),
        Term(
            label="Committed before next income",
            amount=-sts.near_term_committed,
            detail=f"bills due on or before {sts.window_end:%-d %B}",
            parts=reconcile(
                committed, -sts.near_term_committed, "Other commitments"
            ),
        ),
        Term(
            label="Protected buffer",
            amount=-sts.protected_buffer,
            detail="held back deliberately",
        ),
        Term(
            label="Planned contributions still owed",
            amount=-sts.remaining_planned,
            detail="this month's goal contributions not yet made",
            parts=reconcile(
                planned, -sts.remaining_planned, "Already contributed this month"
            ),
        ),
    )

    return Derivation(
        figure="Safe to spend",
        total=sts.safe_to_spend,
        terms=terms,
        note=(
            "What is left after everything already promised. A negative figure is "
            "a real state, not an error: it means the plan no longer fits the cash."
            if sts.safe_to_spend < ZERO
            else "What is left after everything already promised."
        ),
    )


def total_accessible(session: Session, today: date | None = None) -> Derivation:
    """The other headline: what could be reached if the plan bent."""
    today = today or clock_today(session)
    sts = compute_safe_to_spend(session, today)
    split = planned_contributions_split(session, today)

    return Derivation(
        figure="Total accessible",
        total=sts.total_accessible,
        terms=(
            Term(label="Safe to spend", amount=sts.safe_to_spend),
            Term(
                label="Unprotected savings",
                amount=sts.unprotected_savings,
                detail="goals not marked protected",
            ),
            Term(
                label="Flexible contributions",
                amount=split.flexible,
                detail="planned contributions that could be skipped this month",
            ),
        ),
        note=(
            "Reachable, not free. Everything above safe-to-spend costs a goal "
            "something."
        ),
    )


def net_worth_breakdown(session: Session, as_of: date | None = None) -> Derivation:
    """Assets minus what is owed, by account kind.

    Liabilities are credit-normal -- money owed is stored negative -- so this is
    a plain sum and not a subtraction. Getting that backwards is the bug that
    double-counted a loan payment in Phase 1.
    """
    as_of = as_of or clock_today(session)
    balances = account_balances(session, as_of)

    groups: dict[AccountKind, list[Term]] = {}
    for account in session.scalars(
        select(Account).where(Account.active.is_(True)).order_by(Account.name)
    ):
        if account.kind in {AccountKind.EXPENSE, AccountKind.INCOME_SOURCE}:
            continue  # Nominal accounts are not worth anything; they measure flow.
        amount = balances.get(account.id, ZERO)
        if amount != ZERO:
            groups.setdefault(account.kind, []).append(
                Term(label=account.name, amount=amount)
            )

    labels = {
        AccountKind.CURRENT: "Current accounts",
        AccountKind.CASH: "Cash",
        AccountKind.SAVINGS: "Savings",
        AccountKind.INVESTMENT: "Investments",
        AccountKind.LIABILITY: "Owed",
    }
    terms = tuple(
        Term(
            label=labels.get(kind, kind.value),
            amount=sum((t.amount for t in parts), ZERO),
            parts=tuple(parts),
        )
        for kind, parts in sorted(groups.items(), key=lambda kv: kv[0].value)
    )

    return Derivation(
        figure="Net worth",
        total=net_worth(session, as_of),
        terms=terms,
        note="Liabilities are stored negative, so this is a sum, not a subtraction.",
    )


def budget_period(
    session: Session, budget_id, today: date | None = None
) -> Derivation | None:
    """How a budget's remaining figure was reached.

    The rollover term is the one worth showing: a budget can be £200 over its
    nominal amount because eight closed periods carried surplus forward, and
    without the chain that figure looks like a mistake.
    """
    today = today or clock_today(session)
    budget = session.get(Budget, budget_id)
    if budget is None:
        return None
    result = budget_engine.current_period(session, budget, today)
    if result is None:
        return None

    terms = [
        Term(
            label="Budgeted this period",
            amount=result.amount,
            detail=f"{result.period_start:%-d %b} to {result.period_end:%-d %b}",
        )
    ]
    if result.rollover_in != ZERO:
        terms.append(
            Term(
                label="Carried in",
                amount=result.rollover_in,
                detail=(
                    "surplus from earlier periods"
                    if result.rollover_in > ZERO
                    else "overspend carried from earlier periods"
                ),
            )
        )
    if result.rollover_forgiven != ZERO:
        terms.append(
            Term(
                label="Forgiven at the floor",
                amount=result.rollover_forgiven,
                detail="carry-in clamped so a bad month cannot compound forever",
            )
        )
    terms.append(Term(label="Spent", amount=-result.spent))

    return Derivation(
        figure=f"{result.budget_name} remaining",
        total=result.remaining,
        terms=tuple(terms),
        note=(
            f"{result.days_remaining} days left in this period."
            if result.days_remaining is not None
            else "This period has closed."
        ),
    )
