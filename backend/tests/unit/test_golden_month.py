"""Hand-calculated, whole-month reconciliation fixture promised by the rulebook."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.domain import calendar
from app.domain.budget_recovery import assess
from app.domain.budgets import current_period
from app.domain.disposable import account_balances, compute_safe_to_spend, net_worth
from app.models import (
    AccountKind,
    Budget,
    BudgetPeriod,
    BudgetRevision,
    Category,
    CategoryNature,
    ExpectedIncome,
    FutureObligation,
    GoalContribution,
    GoalPriority,
    ObligationInstance,
    RolloverPolicy,
    SavingsGoal,
    UserProfile,
)
from tests.conftest import make_account, post

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "august_2026.yaml"


def D(value) -> Decimal:
    return Decimal(str(value))


def dt(value) -> date:
    return date.fromisoformat(value)


def test_august_2026_reconciles_every_engine(session):
    data = json.loads(FIXTURE.read_text())
    today = dt(data["as_of"])

    session.add(
        UserProfile(
            protected_cash_buffer=D(data["profile"]["protected_cash_buffer"])
        )
    )
    accounts = {
        name: make_account(
            session,
            name,
            AccountKind(config["kind"]),
            config["opening"],
        )
        for name, config in data["accounts"].items()
    }
    categories = {
        name: Category(name=name, nature=CategoryNature(nature))
        for name, nature in data["categories"].items()
    }
    session.add_all(categories.values())
    session.flush()

    for transaction in data["transactions"]:
        legs = [
            (
                accounts[account_name],
                amount,
                categories[category_name] if category_name else None,
            )
            for account_name, amount, category_name in transaction["legs"]
        ]
        post(
            session,
            dt(transaction["date"]),
            transaction["description"],
            legs,
        )

    for goal_data in data["goals"]:
        item = SavingsGoal(
            name=goal_data["name"],
            target_amount=D("10000"),
            priority=GoalPriority(goal_data["priority"]),
            planned_contribution=D(goal_data["planned"]),
            account_id=accounts[goal_data["account"]].id,
        )
        session.add(item)
        session.flush()
        for contribution in goal_data["contributions"]:
            session.add(
                GoalContribution(
                    goal_id=item.id,
                    amount=D(contribution["amount"]),
                    booking_date=dt(contribution["date"]),
                )
            )

    budget_data = data["budget"]
    budget = Budget(
        name=budget_data["name"],
        period=BudgetPeriod.MONTHLY,
        start_date=dt(budget_data["start"]),
    )
    session.add(budget)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=budget.id,
            effective_from=dt(budget_data["start"]),
            amount=D(budget_data["amount"]),
            rollover_policy=RolloverPolicy.NONE,
        )
    )

    income_data = data["expected_income"]
    session.add(
        ExpectedIncome(
            name=income_data["name"],
            amount=D(income_data["amount"]),
            next_expected_date=dt(income_data["date"]),
        )
    )
    obligation_data = data["obligation"]
    obligation = FutureObligation(
        name=obligation_data["name"],
        amount=D(obligation_data["amount"]),
        first_due_date=dt(obligation_data["date"]),
        hard=True,
    )
    session.add(obligation)
    session.flush()
    session.add(
        ObligationInstance(
            obligation_id=obligation.id,
            due_date=dt(obligation_data["date"]),
            amount=D(obligation_data["amount"]),
        )
    )
    session.commit()
    session.refresh(budget)

    expected = data["expected"]
    balances = account_balances(session, today)
    assert {
        name: balances[accounts[name].id] for name in expected["balances"]
    } == {name: D(value) for name, value in expected["balances"].items()}
    assert net_worth(session, today) == D(expected["net_worth"])

    safe = compute_safe_to_spend(session, today)
    safe_expected = expected["safe_to_spend"]
    assert safe.cash == D(safe_expected["cash"])
    assert safe.near_term_committed == D(safe_expected["committed"])
    assert safe.protected_buffer == D(safe_expected["buffer"])
    assert safe.remaining_planned == D(safe_expected["remaining_planned"])
    assert safe.safe_to_spend == D(safe_expected["safe"])
    assert safe.unprotected_savings == D(safe_expected["unprotected_savings"])
    assert safe.flexible_planned_release == D(safe_expected["flexible_release"])
    assert safe.total_accessible == D(safe_expected["total_accessible"])

    period = current_period(session, budget, today)
    assert period is not None
    budget_expected = expected["budget"]
    assert period.spent == D(budget_expected["spent"])
    assert period.remaining == D(budget_expected["remaining"])
    assert period.presented_allowance == D(budget_expected["presented_allowance"])

    recovery = assess(session, today)
    recovery_expected = expected["recovery"]
    assert recovery.headroom == D(recovery_expected["headroom"])
    assert recovery.gap == D(recovery_expected["gap"])
    assert recovery.planned_total == D(recovery_expected["planned_total"])
    assert recovery.already_contributed == D(
        recovery_expected["already_contributed"]
    )
    assert recovery.projected_contribution_total == D(
        recovery_expected["projected_total"]
    )

    curve = calendar.build(session, today, dt(obligation_data["date"]))
    calendar_expected = expected["calendar"]
    closing = {day.day.isoformat(): day.closing_balance for day in curve.days}
    assert curve.opening_balance == D(calendar_expected["opening"])
    assert closing["2026-09-01"] == D(calendar_expected["2026-09-01"])
    assert closing["2026-09-05"] == D(calendar_expected["2026-09-05"])
    assert curve.trough_balance == D(calendar_expected["trough"])
