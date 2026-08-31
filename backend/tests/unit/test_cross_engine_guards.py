"""Agreement locks for the ledger, safe-to-spend, budget, and recovery engines."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.budget_recovery import assess
from app.domain.budgets import current_period
from app.domain.disposable import (
    account_balances,
    compute_safe_to_spend,
    net_worth,
    planned_contributions_split,
    remaining_planned_contributions,
)
from app.domain.ledger_scope import posted_transaction_ids
from app.models import (
    Budget,
    BudgetPeriod,
    BudgetRevision,
    GoalContribution,
    GoalPriority,
    RolloverPolicy,
    SavingsGoal,
    TransactionStatus,
)
from tests.conftest import post

START = date(2026, 8, 1)
TODAY = date(2026, 8, 20)
END = date(2026, 8, 31)


def make_budget(session, *, amount="600", category=None) -> Budget:
    budget = Budget(
        name="Discretionary",
        period=BudgetPeriod.MONTHLY,
        start_date=START,
        category_id=category.id if category is not None else None,
    )
    session.add(budget)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=budget.id,
            effective_from=START,
            amount=Decimal(amount),
            rollover_policy=RolloverPolicy.NONE,
        )
    )
    session.commit()
    session.refresh(budget)
    return budget


def add_goal(session, name, planned, priority) -> SavingsGoal:
    goal = SavingsGoal(
        name=name,
        target_amount=Decimal("10000"),
        planned_contribution=Decimal(planned),
        priority=priority,
    )
    session.add(goal)
    session.commit()
    return goal


def contribute(session, goal, amount):
    session.add(
        GoalContribution(
            goal_id=goal.id,
            amount=Decimal(amount),
            booking_date=TODAY,
        )
    )
    session.commit()


def test_X1_budget_overspend_is_not_subtracted_from_safe_to_spend(
    session, accounts, categories
):
    post(
        session,
        TODAY,
        "Overspend",
        [
            (accounts["current"], "-750"),
            (accounts["groceries"], "750", categories["groceries"]),
        ],
    )
    without_budget = compute_safe_to_spend(session, TODAY)

    budget = make_budget(session, amount="600")
    period = current_period(session, budget, TODAY)
    with_budget = compute_safe_to_spend(session, TODAY)

    assert period is not None
    assert period.spent == Decimal("750")
    assert period.remaining == Decimal("-150")
    assert with_budget == without_budget
    assert with_budget.safe_to_spend == Decimal("300")
    assert all("budget" not in label.lower() for label, _ in with_budget.explain())


def test_X2_credit_card_overspend_moves_budget_not_cash(
    session, accounts, categories
):
    budget = make_budget(session, category=categories["groceries"])
    before = compute_safe_to_spend(session, TODAY)
    worth_before = net_worth(session, TODAY)

    post(
        session,
        TODAY,
        "Card-funded groceries",
        [
            (accounts["loan"], "-200"),
            (accounts["groceries"], "200", categories["groceries"]),
        ],
    )

    after = compute_safe_to_spend(session, TODAY)
    period = current_period(session, budget, TODAY)

    assert period is not None
    assert period.spent == Decimal("200")
    assert period.remaining == Decimal("400")
    assert after.cash == before.cash
    assert after.safe_to_spend == before.safe_to_spend
    assert net_worth(session, TODAY) == worth_before - Decimal("200")


def test_X3_budget_and_balances_resolve_the_same_posted_transaction_set(
    session, accounts, categories
):
    budget = make_budget(session, category=categories["groceries"])
    posted = post(
        session,
        TODAY,
        "Posted",
        [
            (accounts["current"], "-300"),
            (accounts["groceries"], "300", categories["groceries"]),
        ],
    )
    post(
        session,
        TODAY,
        "Candidate",
        [
            (accounts["current"], "-340"),
            (accounts["groceries"], "340", categories["groceries"]),
        ],
        status=TransactionStatus.CANDIDATE,
    )
    post(
        session,
        TODAY,
        "Voided",
        [
            (accounts["current"], "-90"),
            (accounts["groceries"], "90", categories["groceries"]),
        ],
        status=TransactionStatus.VOIDED,
    )
    post(
        session,
        date(2026, 9, 1),
        "Outside period",
        [
            (accounts["current"], "-70"),
            (accounts["groceries"], "70", categories["groceries"]),
        ],
    )

    resolved_ids = set(
        session.scalars(posted_transaction_ids(start=START, end=END))
    )
    period = current_period(session, budget, END)
    balances = account_balances(session, END)

    assert resolved_ids == {posted.id}
    assert period is not None
    assert period.spent == Decimal("300")
    assert balances[accounts["current"].id] == Decimal("700")


def test_X8_safe_to_spend_and_recovery_share_the_contribution_clamp(
    session, accounts
):
    protected = add_goal(session, "Emergency Fund", "500", GoalPriority.HIGH)
    flexible = add_goal(session, "Car Fund", "300", GoalPriority.MEDIUM)
    overfilled = add_goal(session, "Holiday", "400", GoalPriority.OPTIONAL)
    contribute(session, protected, "200")
    contribute(session, flexible, "100")
    contribute(session, overfilled, "450")

    split = planned_contributions_split(session, TODAY)
    safe = compute_safe_to_spend(session, TODAY)
    recovery = assess(session, TODAY)

    assert split.protected == Decimal("300")
    assert split.flexible == Decimal("200")
    assert split.total == Decimal("500")
    assert split.per_goal == {
        protected.id: Decimal("300"),
        flexible.id: Decimal("200"),
    }
    assert remaining_planned_contributions(session, TODAY) == split.total
    assert safe.remaining_planned == split.total
    assert recovery.protected_owed == split.protected
    assert recovery.flexible_owed == split.flexible
